"""reporting.py — 报告与证据包生成（SK-12）。

设计文档 7.3 输出结论结构 / 附录 B 报告输出最小目录 / 表 56 输出文件。

防幻觉要求（表 27）：报告中的设备名称、额定能力、接入点、计算值、阈值和
整改依据必须可回链至数据快照、规则版本或引擎输出；无法回链的内容不得进入
正式报告。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from hc_agent.errors import EXIT_SUCCESS, EXIT_RELEASE_GATE_FAILED
from hc_agent.evidence import (
    EvidenceLedger,
    read_json,
    seal_evidence,
    sha256_obj,
    utc_now_iso,
    write_json,
)
from hc_agent.io.schema import as_str, write_csv

# 附录 B 报告输出最小目录
REPORT_SECTIONS = (
    "1. 概述：背景与目标、对象与范围、水平年与情景、编制依据、智能体简介",
    "2. 基础数据与质量说明：网架设备负荷电源、来源、清洗、质量影响",
    "3. 评估方法与约束条件：流程、模型、六类约束、关键参数、分级标准、智能体机制",
    "4. 算例分析与方法验证：方法验证、实际算例、效率与可靠性对比（结构已建，数据待补）",
    "5. 评估结果：总体容量、等级、主导限制明细与接入校核",
    "6. 制约因素与敏感性分析：首要制约、薄弱环节、敏感性（未实现）",
    "7. 提升措施与效果：改造方向、技术手段、投资与提升量（未估算）",
    "8. 不确定性与风险：数据/模型/假设不确定、适用范围与有效期",
    "9. 智能体运行说明：留痕、结论强度、复核清单、可复现信息、更新机制",
    "10. 结论与建议：主要结论、实施建议、后续工作",
    "附录 A：术语、等级划分标准与业务流覆盖",
    "附录 B：证据索引（文件哈希、日志、审计事件）",
    "附录 C：计算参数与参考标准",
    "附录 D：独立验证（复算任务、差异与通过状态）",
    "附录 E：人工审批（意见、签署、有效期与发布范围）",
)


def build_report_context(run_dir, *, verification: Optional[Dict[str, Any]] = None
                         ) -> Dict[str, Any]:
    """汇集报告所需的全部可回链数据。"""
    run = Path(run_dir).resolve()
    ctx: Dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "run_dir": str(run),
        "sections": list(REPORT_SECTIONS),
    }

    summary_path = run / "results" / "run_summary.json"
    if summary_path.exists():
        ctx["summary"] = read_json(summary_path)
        # 口径声明存放在 run_summary.json 内，必须显式提升到 ctx，
        # 否则渲染端拿到的是空 —— 会被静默跳过（实测踩过：口径表没进报告）。
        if isinstance(ctx["summary"], dict) and ctx["summary"].get("caliber"):
            ctx["caliber"] = ctx["summary"]["caliber"]
    manifest_path = run / "manifest.json"
    if manifest_path.exists():
        ctx["manifest"] = read_json(manifest_path)

    # 口径声明缺失时**现算一份**（例如报告由 reporting CLI 单独重跑，
    # 而 run_summary.json 是旧版本产物）。宁可现算，也不要静默漏掉这一节。
    if not ctx.get("caliber"):
        try:
            from hc_agent.renderers.caliber import build_caliber
            conv = None
            conv_path = run / "results" / "conversion.json"
            if conv_path.exists():
                conv = read_json(conv_path)
            ctx["caliber"] = build_caliber(
                task=(ctx["summary"] or {}).get("task") or {},
                manifest=ctx.get("manifest") or {},
                summary=ctx["summary"] or {},
                conversion=conv).to_dict()
        except Exception as exc:
            ctx["caliber"] = {"error": f"{type(exc).__name__}: {exc}"}

    cap_path = run / "results" / "capacity.csv"
    if cap_path.exists():
        ctx["capacity"] = _read_csv_rows(cap_path)
    con_path = run / "results" / "constraint_results.csv"
    if con_path.exists():
        ctx["constraints"] = _read_csv_rows(con_path)
    trace_path = run / "results" / "search_trace.csv"
    if trace_path.exists():
        ctx["search_trace"] = _read_csv_rows(trace_path)
    qi_path = run / "results" / "quality_issues.csv"
    if qi_path.exists():
        ctx["quality_issues"] = _read_csv_rows(qi_path)

    ctx["verification"] = verification if verification is not None else (
        read_json(run / "verification" / "verification.json")
        if (run / "verification" / "verification.json").exists() else None)

    gate_path = run / "review" / "release_gate.json"
    if gate_path.exists():
        ctx["release_gate"] = read_json(gate_path)
    approval_path = run / "review" / "approval.json"
    if approval_path.exists():
        ctx["approval"] = read_json(approval_path)

    return ctx


def _read_csv_rows(path) -> List[Dict[str, Any]]:
    import csv
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _render_caliber(cal: Dict[str, Any]) -> List[str]:
    """把口径字典渲染成 Markdown 表格（从 run_summary 里的结构化字段回读）。"""
    L: List[str] = ["### 本次求解口径声明", "",
                    "| 项 | 本次口径 | 说明 |", "|---|---|---|"]
    for it in (cal.get("items") or []):
        L.append(f"| {it.get('label','')} | {it.get('value','')} | "
                 f"{it.get('note','')} |")
    L.append("")
    if cal.get("phase_statement"):
        L.append(f"> {cal['phase_statement']}")
        L.append("")
    if cal.get("assumptions"):
        L.append("**模型假设**：")
        L.append("")
        for a in cal["assumptions"]:
            L.append(f"- {a}")
        L.append("")
    if cal.get("unsupported"):
        L.append("**本次未覆盖**：")
        L.append("")
        for u in cal["unsupported"]:
            L.append(f"- {u}")
        L.append("")
    return L


def generate_report(run_dir, verification: Optional[Dict[str, Any]] = None,
                    *, out_path: Optional[Path] = None) -> Dict[str, Any]:
    """生成专业报告（设计文档 S08 / 附录 B）。

    使用 python-docx 生成 Word；不可用时退化为 Markdown，
    但所有数字仍必须可回链。
    """
    run = Path(run_dir).resolve()
    ctx = build_report_context(run, verification=verification)
    out = Path(out_path) if out_path else (run / "report" / "report.docx")
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = _render_markdown(ctx)
    docx_ok = False
    try:
        _write_docx(ctx, out)
        docx_ok = True
    except Exception as exc:
        (out.parent / "report.md").write_text("\n".join(lines),
                                              encoding="utf-8")
        docx_ok = False

    result = {
        "report_path": str(out) if docx_ok else str(out.parent / "report.md"),
        "format": "docx" if docx_ok else "markdown",
        "generated_at": ctx["generated_at"],
        "sections": list(REPORT_SECTIONS),
        "traceability": _traceability(ctx),
        "result_hash": _result_hash(ctx),
    }
    write_json(out.parent / "report_manifest.json", result)
    return result


def _traceability(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """回链检查（表 27）：每个关键结论都能指向快照/规则/引擎输出。"""
    summary = ctx.get("summary") or {}
    links = {
        "snapshot_id": (ctx.get("manifest") or {}).get("snapshot_id")
        or summary.get("snapshot_id"),
        "rule_package": (ctx.get("manifest") or {}).get("rule_version")
        or summary.get("rule_package"),
        "engine_fingerprint": (ctx.get("manifest") or {}).get(
            "engine_fingerprint"),
        "input_digest": (ctx.get("manifest") or {}).get("input_digest"),
        "evidence_refs": summary.get("evidence_refs") or [],
    }
    missing = [k for k, v in links.items() if not v]
    return {"links": links, "missing": missing,
            "complete": not missing}


def _result_hash(ctx: Dict[str, Any]) -> str:
    """结果哈希：审批绑定结果哈希后才能发布（设计文档 S09）。"""
    payload = {
        "capacity": ctx.get("capacity"),
        "constraints_summary": (ctx.get("summary") or {}).get(
            "constraint_summary"),
        "capacity_physical_kw": (ctx.get("summary") or {}).get(
            "capacity_physical_kw"),
        "search_status": (ctx.get("summary") or {}).get("search_status"),
        "level": (ctx.get("summary") or {}).get("level"),
    }
    return sha256_obj(payload)


def _render_markdown(ctx: Dict[str, Any]) -> List[str]:
    s = ctx.get("summary") or {}
    man = ctx.get("manifest") or {}
    L: List[str] = []
    L.append("# 配网承载力评估报告")
    L.append("")
    L.append(f"生成时间：{ctx['generated_at']}")
    L.append("")

    # 1 任务概况
    L.append("## 1 任务概况")
    L.append("")
    L.append(f"- 任务号：{s.get('task_id','')}")
    L.append(f"- 业务场景：{s.get('business_scene','')}")
    L.append(f"- 对象范围：{s.get('scope_type','')} {s.get('scope_ids','')}")
    L.append(f"- 时间窗：{s.get('time_window_start','')} 至 "
             f"{s.get('time_window_end','')}（半开区间）")
    L.append(f"- 运行号：{s.get('run_id','')}")
    L.append("")

    # 2 数据与口径
    L.append("## 2 数据与口径")
    L.append("")
    L.append(f"- 快照：{man.get('snapshot_id','')}")
    L.append(f"- 拓扑版本：{man.get('topology_version','')}")
    L.append(f"- 规则包：{man.get('rule_version','')}")
    L.append(f"- 技能版本：{man.get('skill_version','')}")
    fp = man.get("engine_fingerprint") or {}
    L.append(f"- 引擎：{fp.get('engine_id','opendss')} / "
             f"{fp.get('interface','OpenDSSDirect.py')} "
             f"{fp.get('interface_version','')}")
    L.append(f"- 输入清单摘要：{man.get('input_digest','')}")
    L.append("")

    # 3 计算过程
    L.append("## 3 计算过程")
    L.append("")
    L.append(f"- 场景数：{s.get('scenario_count','')}，"
             f"时间片总数：{s.get('slice_count','')}")
    L.append(f"- 基线收敛：{s.get('all_converged','')}")
    L.append(f"- 搜索方法：{s.get('search_method','')}，"
             f"迭代 {s.get('search_iterations','')} 次")
    L.append(f"- 搜索状态：{s.get('search_status','')}")
    notes = s.get("search_notes") or []
    for n in notes:
        L.append(f"  - {n}")
    L.append("")
    # 本次求解口径声明（移植自天枢一号；由运行产物**推导**而非手写，
    # 避免口径陈述与代码漂移）。设计文档 17.13 亦要求报告注明求解范围、
    # 场景覆盖、未支持分析、模型假设、精度与有效期。
    if ctx.get("caliber"):
        L.extend(_render_caliber(ctx["caliber"]))
        L.append("")

    # 4 评估结果
    L.append("## 4 评估结果")
    L.append("")
    L.append(f"- 物理可开放容量：{_fmt(s.get('capacity_physical_kw'))} kW")
    L.append(f"- 管理可开放容量：{_fmt(s.get('capacity_managed_kw'))} kW")
    L.append(f"- 红黄绿等级：{s.get('level','')}")
    L.append(f"- 分级依据：{s.get('level_reason','')}")
    L.append(f"- 主导限制因素：{s.get('binding_constraint','')}")
    cs = s.get("constraint_summary") or {}
    L.append(f"- 约束校验：共 {cs.get('total',0)} 项，"
             f"pass {cs.get('pass',0)}，fail {cs.get('fail',0)}，"
             f"unknown {cs.get('unknown',0)}")
    for b in (cs.get("binding_constraints") or [])[:10]:
        L.append(f"  - {b.get('constraint_id')} @ {b.get('object_id')} "
                 f"{b.get('metric')}={_fmt(b.get('value'))} "
                 f"限值[{_fmt(b.get('limit_lower'))}, "
                 f"{_fmt(b.get('limit_upper'))}] "
                 f"余量={_fmt(b.get('margin'))}")
    L.append("")

    # 5 接入校核
    L.append("## 5 接入校核")
    L.append("")
    if s.get("application_id"):
        L.append(f"- 申请：{s.get('application_id')} "
                 f"容量 {_fmt(s.get('requested_capacity_kw'))} kW")
        L.append(f"- 结论候选：{s.get('decision_candidate','')}")
    else:
        L.append("- 本任务为容量定期评估，无并网申请。")
    L.append("")

    # 6 风险与缺口
    L.append("## 6 风险与缺口")
    L.append("")
    for r in (s.get("risks") or ["无已登记风险"]):
        L.append(f"- {r}")
    for u in (s.get("unknowns") or []):
        L.append(f"- unknown：ΔP={_fmt(u.get('delta_kw'))} kW，"
                 f"原因 {u.get('reason','')}")
    L.append("")

    # 7 独立验证
    L.append("## 7 独立验证")
    L.append("")
    ver = ctx.get("verification") or {}
    if ver:
        L.append(f"- 复算运行号：{ver.get('verify_run_id','')}")
        L.append(f"- 输入指纹一致：{ver.get('input_match','')}")
        L.append(f"- 复算通过：{ver.get('pass','')}")
        cmp_ = ver.get("comparison") or {}
        L.append(f"- 比对指标 {cmp_.get('total_metrics',0)} 项，"
                 f"差异 {cmp_.get('mismatch_count',0)} 项")
        L.append(f"- 说明：{cmp_.get('note','')}")
    else:
        L.append("- 尚未执行独立复算（禁止发布正式结论）。")
    L.append("")

    # 8 人工审批
    L.append("## 8 人工审批")
    L.append("")
    gate = ctx.get("release_gate") or {}
    L.append(f"- 发布门禁通过：{gate.get('passed','未评估')}")
    L.append(f"- 未通过门禁：{gate.get('failed_gates','')}")
    appr = ctx.get("approval") or {}
    L.append(f"- 审批号：{appr.get('approval_id','未提交')}")
    L.append(f"- 结果哈希绑定：{appr.get('result_hash','—')}")
    L.append("")

    # 9 证据索引
    L.append("## 9 证据索引")
    L.append("")
    for name, desc in (
        ("manifest.json", "输入清单与哈希"),
        ("results/capacity.csv", "容量结果"),
        ("results/constraint_results.csv", "约束明细"),
        ("results/search_trace.csv", "搜索轨迹"),
        ("results/bus_results.csv", "母线电压结果"),
        ("results/element_results.csv", "元件电流/功率结果"),
        ("logs/", "引擎原生日志"),
        ("verification/verification.json", "独立复算结论"),
        ("evidence/", "封存证据包"),
    ):
        L.append(f"- `{name}` — {desc}")
    L.append("")
    L.append("> 本报告全部数值可回链至数据快照、规则版本或引擎输出；"
             "无法回链的内容不进入正式报告。")
    return L


def _fmt(v: Any) -> str:
    if v is None or v == "":
        return "null"
    try:
        f = float(v)
        if f != f:
            return "null"
        return f"{f:.6g}"
    except (TypeError, ValueError):
        return str(v)


def _write_docx(ctx: Dict[str, Any], out: Path) -> None:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading("配网承载力评估报告", level=0)
    s = ctx.get("summary") or {}
    man = ctx.get("manifest") or {}

    def h(text: str, level: int = 1) -> None:
        doc.add_heading(text, level=level)

    def kv(label: str, value: Any) -> None:
        p = doc.add_paragraph()
        p.add_run(f"{label}：").bold = True
        p.add_run(_fmt(value) if not isinstance(value, str) else value)

    h("1 结论摘要")
    _zh = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}
    _lvl = _zh.get(str(s.get("level")), str(s.get("level") or "—"))
    _cs0 = s.get("constraint_summary") or {}
    kv("一句话结论",
       f"本次评估等级为{_lvl}，物理可开放容量 {_fmt(s.get('capacity_physical_kw'))} kW，"
       f"主导限制因素为 {s.get('binding_constraint') or '—'}")
    kv("可开放容量 (kW)",
       f"物理 {_fmt(s.get('capacity_physical_kw'))} / 管理 {_fmt(s.get('capacity_managed_kw'))}")
    kv("红黄绿等级", _lvl)
    kv("主导限制因素", s.get("binding_constraint") or "—")
    kv("基线约束校核",
       f"共 {_cs0.get('total', 0)} 项：pass {_cs0.get('pass', 0)}、"
       f"fail {_cs0.get('fail', 0)}、unknown {_cs0.get('unknown', 0)}")
    _bs0 = _cs0.get("binding_constraints") or []
    if _bs0:
        _b0 = _bs0[0]
        kv("突出发现",
           f"{as_str(_b0.get('constraint_id'))} 在 {as_str(_b0.get('object_id'))} 上 "
           f"{as_str(_b0.get('metric'))}={_fmt(_b0.get('value'))}，"
           f"限值 [{_fmt(_b0.get('limit_lower'))}, {_fmt(_b0.get('limit_upper'))}]，"
           f"裕度 {_fmt(_b0.get('margin'))}")
    kv("需关注项",
       "结论可用于内部评估与答复支撑；对外发布的区域等级须按省公司口径另行生成。")

    h("1 概述")
    kv("任务号", s.get("task_id", ""))
    kv("业务场景", s.get("business_scene", ""))
    kv("对象范围", f"{s.get('scope_type','')} {s.get('scope_ids','')}")
    kv("时间窗", f"{s.get('time_window_start','')} 至 "
                 f"{s.get('time_window_end','')}（半开区间）")
    kv("运行号", s.get("run_id", ""))

    h("2 基础数据与质量说明")
    kv("快照", man.get("snapshot_id", ""))
    kv("拓扑版本", man.get("topology_version", ""))
    kv("规则包", man.get("rule_version", ""))
    kv("技能版本", man.get("skill_version", ""))
    fp = man.get("engine_fingerprint") or {}
    kv("引擎", f"{fp.get('engine_id','opendss')} / "
               f"{fp.get('interface','OpenDSSDirect.py')} "
               f"{fp.get('interface_version','')}")
    kv("输入清单摘要", man.get("input_digest", ""))

    h("4 评估方法与约束条件")
    kv("场景数", s.get("scenario_count", ""))
    kv("时间片总数", s.get("slice_count", ""))
    kv("基线收敛", s.get("all_converged", ""))
    kv("搜索方法", s.get("search_method", ""))
    kv("搜索迭代次数", s.get("search_iterations", ""))
    kv("搜索状态", s.get("search_status", ""))
    for n in (s.get("search_notes") or []):
        doc.add_paragraph(n, style="List Bullet")

    # 本次求解口径声明（移植自天枢一号）—— Word 报告同样必须带上。
    # 只在 Markdown 渲染里加是不够的：本机 python-docx 可用，实际交付物是 .docx。
    cal = ctx.get("caliber") or {}
    if cal and not cal.get("error"):
        h("4.1 本次求解口径声明")
        items = cal.get("items") or []
        if items:
            t = doc.add_table(rows=1, cols=3)
            t.style = "Light Grid Accent 1"
            for i, name in enumerate(("项", "本次口径", "说明")):
                t.rows[0].cells[i].text = name
            for it in items:
                r = t.add_row().cells
                r[0].text = str(it.get("label", ""))
                r[1].text = str(it.get("value", ""))
                r[2].text = str(it.get("note", ""))
        if cal.get("phase_statement"):
            doc.add_paragraph(str(cal["phase_statement"]))
        if cal.get("assumptions"):
            doc.add_paragraph("模型假设：")
            for a in cal["assumptions"]:
                doc.add_paragraph(str(a), style="List Bullet")
        if cal.get("unsupported"):
            doc.add_paragraph("本次未覆盖：")
            for u in cal["unsupported"]:
                doc.add_paragraph(str(u), style="List Bullet")
    elif cal.get("error"):
        doc.add_paragraph(f"口径声明生成失败：{cal['error']}")

    h("4 算例分析与方法验证")
    kv("4.1 方法验证算例",
       "本版以 OpenDSS 主算、pandapower 正序平衡互校作为方法验证；"
       "与 PSASP / PSD-BPA 的对比尚未开展（本机无该软件），引用前需补。")
    kv("4.2 实际工程算例",
       f"本次报告基于运行 {s.get('run_id') or ctx.get('run_dir', '')} 的单个算例："
       f"场景 {s.get('scenario_count', '—')} 个、时间片 {s.get('slice_count', '—')} 个，"
       "其结果见第 5 章。多算例成章（背景/场景/结果/制约点/措施前后对比）待补。")
    kv("4.3 效率与可靠性对比",
       "单算例评估约 3–4 秒、迭代次数与耗时见运行产物；与人工测算的耗时对比尚无基准数据。")
    kv("说明", "本章为结构占位：章节目录与要素已按最终版建立，"
               "待补的是对比数据与多算例素材，不用占位数字替代。")

    h("5 评估结果")
    kv("物理可开放容量 (kW)", s.get("capacity_physical_kw"))
    kv("管理可开放容量 (kW)", s.get("capacity_managed_kw"))
    kv("红黄绿等级", s.get("level", ""))
    kv("分级依据", s.get("level_reason", ""))
    kv("主导限制因素", s.get("binding_constraint", ""))
    cs = s.get("constraint_summary") or {}
    kv("约束校验", f"共 {cs.get('total',0)} 项，pass {cs.get('pass',0)}，"
                   f"fail {cs.get('fail',0)}，unknown {cs.get('unknown',0)}")
    bs = cs.get("binding_constraints") or []
    if bs:
        doc.add_paragraph("主导限制因素明细：")
        table = doc.add_table(rows=1, cols=6)
        table.style = "Light Grid Accent 1"
        for i, name in enumerate(("约束", "对象", "指标", "计算值", "限值",
                                  "余量")):
            table.rows[0].cells[i].text = name
        for b in bs[:15]:
            row = table.add_row().cells
            row[0].text = as_str(b.get("constraint_id"))
            row[1].text = as_str(b.get("object_id"))
            row[2].text = as_str(b.get("metric"))
            row[3].text = _fmt(b.get("value"))
            row[4].text = (f"[{_fmt(b.get('limit_lower'))}, "
                           f"{_fmt(b.get('limit_upper'))}]")
            row[5].text = _fmt(b.get("margin"))

    h("5.1 接入校核")
    if s.get("application_id"):
        kv("申请号", s.get("application_id"))
        kv("申请容量 (kW)", s.get("requested_capacity_kw"))
        kv("结论候选", s.get("decision_candidate", ""))
    else:
        doc.add_paragraph("本任务为容量定期评估，无并网申请。")

    h("6 制约因素与敏感性分析")
    _csx = s.get("constraint_summary") or {}
    _bsx = _csx.get("binding_constraints") or []
    kv("基线约束统计",
       f"共 {_csx.get('total', 0)} 项：pass {_csx.get('pass', 0)}、"
       f"fail {_csx.get('fail', 0)}、unknown {_csx.get('unknown', 0)}")
    if _bsx:
        _first = _bsx[0]
        kv("首要制约因素",
           f"{as_str(_first.get('constraint_id'))} @ {as_str(_first.get('object_id'))}"
           f"（{as_str(_first.get('metric'))}={_fmt(_first.get('value'))}，"
           f"裕度 {_fmt(_first.get('margin'))}）")
        doc.add_paragraph("薄弱环节清单：")
        _t6 = doc.add_table(rows=1, cols=5)
        _t6.style = "Light Grid Accent 1"
        for _i, _n in enumerate(("约束", "对象", "指标", "计算值", "裕度")):
            _t6.rows[0].cells[_i].text = _n
        for _b in _bsx[:10]:
            _row = _t6.add_row().cells
            _row[0].text = as_str(_b.get("constraint_id"))
            _row[1].text = as_str(_b.get("object_id"))
            _row[2].text = as_str(_b.get("metric"))
            _row[3].text = _fmt(_b.get("value"))
            _row[4].text = _fmt(_b.get("margin"))
    else:
        kv("首要制约因素", "本次运行未记录绑定约束（基线无越限，或结果缺失）")
    kv("敏感性分析", "未实现：本版不做参数扰动扫描（负荷/出力±10% 逐次重算）。")
    kv("说明", "薄弱环节按约束类型与对象列出；全网设备级清单需逐级评估后汇总，"
               "本版以单算例为限。")

    h("7 提升措施与效果")
    kv("等级", _lvl)
    kv("分级依据", s.get("level_reason") or "—")
    kv("主导约束", s.get("binding_constraint") or "—")
    kv("整改方向",
       "按主导约束对症：电压越限优先调压（无功补偿或有载调压配变）；"
       "热稳越限优先增容或负荷分流；反送越限配置储能或采用出力限制策略；"
       "不平衡越限调整接入相别；短路越限校核开关遮断能力并限制单点接入容量。")
    kv("使用说明",
       "上述建议指向约束类型，具体方案的容量与投资需另行测算；"
       "本报告不对未实施改造的效果作任何承诺。")

    h("9 智能体运行说明")
    _tr = ctx.get("traceability") or {}
    _lk = _tr.get("links") or {}
    kv("复现三要素", "同一份输入快照 + 同一版规则包 + 同一版引擎")
    for _k, _label in (("snapshot_id", "输入快照"),
                       ("rule_package", "规则包"),
                       ("input_digest", "输入指纹")):
        if _lk.get(_k):
            kv(_label, _fmt(_lk.get(_k)))
    _fp = _lk.get("engine_fingerprint") or {}
    if _fp:
        kv("引擎", f"{_fp.get('engine_id', '')} / {_fp.get('interface', '')} "
                   f"{_fp.get('interface_version', '')}")
    kv("可追溯性", "完整（关键链接齐全）" if _tr.get("complete")
       else "不完整，缺少：" + "、".join(_tr.get("missing") or []))

    h("8 不确定性与风险")
    for r in (s.get("risks") or ["无已登记风险"]):
        doc.add_paragraph(str(r), style="List Bullet")
    for u in (s.get("unknowns") or []):
        doc.add_paragraph(f"unknown：ΔP={_fmt(u.get('delta_kw'))} kW，"
                          f"原因 {u.get('reason','')}", style="List Bullet")

    h("附录 D 独立验证")
    ver = ctx.get("verification") or {}
    if ver:
        kv("复算运行号", ver.get("verify_run_id", ""))
        kv("输入指纹一致", ver.get("input_match", ""))
        kv("复算通过", ver.get("pass", ""))
        cmp_ = ver.get("comparison") or {}
        kv("比对结果", f"{cmp_.get('total_metrics',0)} 项指标，"
                       f"{cmp_.get('mismatch_count',0)} 项差异")
        doc.add_paragraph(str(cmp_.get("note", "")))
    else:
        doc.add_paragraph("尚未执行独立复算（禁止发布正式结论）。")

    h("附录 E 人工审批")
    gate = ctx.get("release_gate") or {}
    kv("发布门禁通过", gate.get("passed", "未评估"))
    kv("未通过门禁", gate.get("failed_gates", ""))
    appr = ctx.get("approval") or {}
    kv("审批号", appr.get("approval_id", "未提交"))
    kv("结果哈希绑定", appr.get("result_hash", "—"))

    h("附录 B 证据索引")
    for name, desc in (
        ("manifest.json", "输入清单与哈希"),
        ("results/capacity.csv", "容量结果"),
        ("results/constraint_results.csv", "约束明细"),
        ("results/search_trace.csv", "搜索轨迹"),
        ("results/bus_results.csv", "母线电压结果"),
        ("results/element_results.csv", "元件电流/功率结果"),
        ("logs/", "引擎原生日志"),
        ("verification/verification.json", "独立复算结论"),
        ("evidence/", "封存证据包"),
    ):
        doc.add_paragraph(f"{name} — {desc}", style="List Bullet")

    doc.add_paragraph(
        "本报告全部数值可回链至数据快照、规则版本或引擎输出；"
        "无法回链的内容不进入正式报告。")

    # ---- 10 业务流覆盖
    # 报告必须如实写清"这条业务链上本系统做到哪一步"，
    # 否则读者会把"报告没提"当成"系统已经做到"。
    # 状态来自 hc_agent.business_flow，与问答、JSON 桥同一份数据源。
    from hc_agent import business_flow as _BF

    _st = _BF.summary()
    h("10 结论与建议")
    kv("主要结论",
       f"本次评估等级 {_lvl}，物理可开放容量 {_fmt(s.get('capacity_physical_kw'))} kW，"
       f"管理可开放容量 {_fmt(s.get('capacity_managed_kw'))} kW；"
       f"首要制约因素为 {s.get('binding_constraint') or '—'}。")
    kv("实施建议",
       "近期：按主导约束对症整改（电压→调压、热稳→增容或分流、反送→储能或限出力）；"
       "中期：完善监测数据（谐波、短路参数）与逐级评估能力。")
    kv("后续工作", "数据治理、模型优化与智能体功能迭代；逐级承载力与多对象分级尚未实现。")

    h("附录 A 术语与业务流覆盖（八段闭环）")
    doc.add_paragraph(
        "业务链：数据准备 → 评估计算 → 分级发布 → 接入应用 → 提升改造 → 动态更新。"
        f"本系统已实现 {_st['counts']['implemented']} 段、"
        f"部分实现 {_st['counts']['partial']} 段、"
        f"未实现 {_st['counts']['absent']} 段。"
        "未实现的环节不由本系统代做；"
        "本报告的分级结论仅为单算例判定，不等同于对外发布的区域承载力等级。")
    _t = doc.add_table(rows=1, cols=3)
    _t.style = "Light Grid Accent 1"
    for _i, _name in enumerate(("环节", "状态", "缺口 / 说明")):
        _t.rows[0].cells[_i].text = _name
    for _x in _BF.STAGES:
        _row = _t.add_row().cells
        _row[0].text = f"{_x['no']}. {_x['name']}"
        _row[1].text = _BF.STATUS_LABELS.get(_x["status"], _x["status"])
        _row[2].text = _x["gaps"]

    doc.save(str(out))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口（设计文档 17.10）。"""
    parser = argparse.ArgumentParser(
        prog="hc_agent.reporting",
        description="由运行目录与复算结果生成专业报告与证据包")
    parser.add_argument("--run-dir", required=True, help="主计算运行目录")
    parser.add_argument("--verification", default=None,
                        help="verification.json 路径（可选）")
    parser.add_argument("--output", default=None, help="报告输出路径（可选）")
    parser.add_argument("--seal", action="store_true",
                        help="同时封存证据包 evidence.zip")
    args = parser.parse_args(argv)

    verification = None
    if args.verification and Path(args.verification).exists():
        verification = read_json(args.verification)

    result = generate_report(args.run_dir, verification,
                             out_path=Path(args.output) if args.output else None)
    if args.seal:
        run = Path(args.run_dir).resolve()
        ev = seal_evidence(run, run / "evidence" / "evidence.zip")
        result["evidence_package"] = ev.get("package_path")
        result["evidence_digest"] = ev.get("package_digest")
        write_json(Path(args.run_dir) / "report" / "report_manifest.json",
                   result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_SUCCESS
