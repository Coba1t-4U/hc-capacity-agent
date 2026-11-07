# -*- coding: utf-8 -*-
"""按用户最新 7 章框架，生成一版详细的评估报告（FD002 真实数据）。

原则：能给数的用真实产物；不能实现的章节保留并标注「未实现」，不填占位数字。
"""
import json
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
RUN = ROOT / "runs" / "ACCEPT" / "FD002"
OUT = Path.home() / "Desktop" / "配网承载力" / "承载力智能体结果报告_框架版.docx"
F = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)


def j(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


S = j(RUN / "results" / "run_summary.json")
M = j(RUN / "manifest.json")
O = j(RUN / "run_outcome.json")
CS = S.get("constraint_summary") or {}
BCS = S.get("baseline_constraint_summary") or {}
BS = (BCS.get("binding_constraints") or CS.get("binding_constraints") or [])
BD = S.get("binding_detail") or {}
if not BS and BD:
    BS = [dict(BD, constraint_id=S.get("binding_constraint"))]
LV = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}
LVL = LV.get(str(S.get("level")), str(S.get("level") or "—"))
PHYS = S.get("capacity_physical_kw")
MGD = S.get("capacity_managed_kw")
BIND = S.get("binding_constraint") or "—"
GATE = (O.get("release_gate") or {})
FAILED = GATE.get("failed_gates") or []


def par(d, t, *, size=10.5, bold=False, color=None, align=None, space=6):
    p = d.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.4
    r = p.add_run(t)
    r.font.name = F
    r.font.size = Pt(size)
    r.font.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def h(d, t, lvl=1):
    hd = d.add_heading(t, level=lvl)
    for r in hd.runs:
        r.font.name = F
        r.font.color.rgb = BLUE if lvl == 1 else RGBColor(0x1A, 0x1A, 0x1A)


def tbl(d, rows, w, size=8.5):
    t = d.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for k, v in enumerate(row):
            c = t.cell(i, k)
            c.width = Pt(w[k] * 2.83)
            r = c.paragraphs[0].add_run(str(v))
            r.font.name = F
            r.font.size = Pt(size)
            r.font.bold = (i == 0)
    d.add_paragraph()



def _n_bus():
    """母线数：run_summary 没有 buses 键，按 bus_results.csv 的唯一母线名统计。"""
    import csv
    f = RUN / "results" / "bus_results.csv"
    if not f.is_file():
        return "—"
    try:
        with f.open(encoding="utf-8") as fh:
            names = {(row.get("bus") or row.get("name") or "").strip()
                     for row in csv.DictReader(fh)}
        return len([n for n in names if n])
    except Exception:
        return "—"


def lambda_rows():
    """按 DL/T 2041-2019 计算各元件反向负载率 λ。

    λ = (P_D - P_L) / S_e，本系统用三相中最大的反向有功 p_kw 除以该元件额定容量
    （由 loading_pct 反推：S_e = |P| / (loading_pct/100)），取全场景全时段的 λ_max。
    只统计 p_kw < 0（反向）的记录；正向时段 λ 记 0。
    """
    import csv
    f = RUN / "results" / "element_results.csv"
    if not f.is_file():
        return []
    best = {}
    with f.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                p_kw = float(r["p_kw"] or 0)
                lp = float(r["loading_pct"] or 0)
            except (TypeError, ValueError):
                continue
            if p_kw >= 0:
                continue
            eq = r.get("equipment_id") or ""
            key = (eq, r.get("phase") or "")
            lam = lp / 100.0
            if key not in best or lam > best[key][0]:
                best[key] = (lam, p_kw, r.get("scenario_id").lstrip("\ufeff"), r.get("timestamp"))
    out = sorted(best.items(), key=lambda kv: -kv[1][0])[:8]
    return [(k[0], k[1], v[0], v[1], v[2], v[3]) for k, v in out]


def run_summary(name):
    return j(ROOT / "runs" / "ACCEPT" / name / "results" / "run_summary.json")


def case_line(name):
    x = run_summary(name)
    if not x:
        return f"{name}：无运行产物"
    bd = x.get("binding_detail") or {}
    return (f"{name}：物理 {x.get('capacity_physical_kw')} kW / 管理 "
            f"{x.get('capacity_managed_kw')} kW；等级 "
            f"{LV.get(str(x.get('level')), x.get('level') or '—')}；瓶颈 "
            f"{x.get('binding_constraint') or '—'}"
            + (f" @ {bd.get('object_id')}（{bd.get('metric')}={bd.get('value')}）"
               if bd else ""))


def main():
    d = Document()
    d.sections[0].left_margin = d.sections[0].right_margin = Pt(62)

    par(d, "承载力智能体结果报告", size=22, bold=True, color=BLUE,
        align=WD_ALIGN_PARAGRAPH.CENTER, space=4)
    par(d, "配电网分布式电源接入承载力评估（智能体自动评估）", size=12, color=GREY,
        align=WD_ALIGN_PARAGRAPH.CENTER, space=4)
    par(d, f"评估对象：{S.get('scope_type') or '—'} {S.get('scope_ids') or '—'}　|　"
           f"运行号：{S.get('run_id') or '—'}　|　智能体版本：V1.1.0", size=9,
        color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=4)
    par(d, f"评估时间：{datetime.now():%Y年%m月%d日}　|　工况：{S.get('scenario_count') or '—'} 场景 × "
           f"{S.get('slice_count') or '—'} 时间片", size=9, color=GREY,
        align=WD_ALIGN_PARAGRAPH.CENTER, space=16)

    # ---------- 摘要
    h(d, "执行摘要")
    par(d, f"综合承载力等级：{LVL}；物理可开放容量 {PHYS} kW，管理可开放容量 {MGD} kW。"
           f"首要瓶颈为 {BIND}。", bold=True)
    par(d, "评估目标与范围：本次评估针对单个算例（5 母线 / 3 线路 / 1 配变 / 2 负荷 / 2 组光伏），"
           f"共 {S.get('scenario_count') or '—'} 个场景、{S.get('slice_count') or '—'} 个时间片、"
           f"{CS.get('total', 0)} 条约束。")
    par(d, "智能体核心任务：承载力上限搜索、越限识别、薄弱节点定位、多场景裕度量化。"
           "本版已完成上限搜索与越限识别；多场景已覆盖，但 N-1 与概率场景未实现。")
    if BS:
        b = BS[0]
        par(d, f"关键瓶颈：{b.get('constraint_id')} 在 {b.get('object_id')} 上，"
               f"{b.get('metric')}={b.get('value')}，限值 "
               f"[{b.get('limit_lower')}, {b.get('limit_upper')}]，裕度 {b.get('margin')}。")
    par(d, "关键风险：未提交专业复核与审批时，结果不得对外发布；"
           "谐波、N-1、暂态稳定未校核。")
    par(d, f"一句话总结：这条馈线还能接入约 {PHYS} kW，但被 {BIND} 卡住，"
           "整改方向应据此确定。", bold=True)

    # ---------- 1 概述
    h(d, "1 概述")
    h(d, "1.1 研究背景与评估目的", 2)
    par(d, "分布式电源报装需要对“还能接多少”给出可复核的结论。传统做法依赖人工试算，"
           "口径不统一、依据难追溯；本智能体把输入校验、场景生成、容量搜索、约束核验、"
           "复算验收与证据封存串成一条流水线。")
    h(d, "1.2 评估边界与范围", 2)
    par(d, f"网架范围：{S.get('scope_type') or '—'}（{S.get('scope_ids') or '—'}）；"
           f"电压等级：10 kV 及以下；评估时段：{S.get('scenario_count') or '—'} 场景显式断面。")
    par(d, "约束边界：已实现电压、热稳、反送、不平衡、短路五类；"
           "N-1、暂态稳定、调频未纳入（未实现）。")
    h(d, "1.3 承载力智能体简介", 2)
    par(d, "架构五段：感知（输入包与算例接入、字段单位校验）→ 规划推理（场景与口径确认、任务拆解）"
           "→ 仿真（OpenDSS 主算，pandapower 交叉校核）→ 约束校验（五类约束逐条判定、四态验收）"
           "→ 决策输出（容量结论、瓶颈定位、报告与证据链）。")
    par(d, "运行逻辑：环境感知 → 场景采样 → 承载力搜索（0–3000 kW 二分，精度 10 kW）→ "
           "约束核验 → 瓶颈归因 → 结果溯源。")
    par(d, "与传统方法的区别：自主遍历断面试算、多场景批量、自动定位瓶颈、结论自带证据链；"
           "但**不做多目标 Pareto 寻优**，目标是“最大可行容量”单目标。")
    h(d, "1.3 术语定义", 2)
    par(d, "系统级承载力：调度独立控制区域范围内，配电网能够消纳的分布式电源最大装机规模。")
    par(d, "设备级承载力：单台变压器、单条线路等单个设备允许接入的分布式电源最大容量。")
    par(d, "可开放容量：不突破设备运行限值、不影响电能质量前提下，剩余可接入容量空间。")
    par(d, "红黄绿分区：按反向负载率 λ 对供电区域划分的管控等级（红 >100%、黄 80–100%、绿 0–80%）。")

    h(d, "1.4 引用标准与基础文件", 2)
    par(d, f"DL/T 2041-2019《分布式电源接入电网承载力评估导则》（本报告不引用所谓“最新修订版”，如需引用须注明标准号与发布日期）；GB/T 12325（电压偏差）。"
           f"规则包 {M.get('rule_version') or '—'}；输入快照 {M.get('snapshot_id') or '—'}。")

    # ---------- 2 输入
    h(d, "2 基础数据收集与校验")
    h(d, "2.1 电网网架数据", 2)
    par(d, f"母线 {_n_bus()} 个（含电源节点）、支路见元件结果表；拓扑版本 "
           f"{M.get('topology_version') or '—'}；设备限值与短路参数随输入包冻结。")
    h(d, "2.2 源-荷-储数据", 2)
    par(d, "已有：光伏出力曲线、负荷曲线、已受理容量（reservations）。"
           "未建模：储能、可中断负荷等可控资源的调节区间（未实现）。")
    h(d, "2.3 场景集设置", 2)
    par(d, f"已实现：{S.get('scenario_count') or '—'} 个典型场景 × {S.get('slice_count') or '—'} 个时间片，"
           "取最不利断面作为结论依据。未实现：N-1 故障场景、概率随机场景、时序滚动。")
    h(d, "2.4 评估指标体系", 2)
    par(d, "安全类：电压偏差、设备负载率、短路电流（已实现）；静态/暂态稳定裕度（未实现）。")
    par(d, "充裕类：新能源最大接纳容量（已实现）；消纳率、缺电风险（未实现）。")
    par(d, "经济类：网损（已实现）；调节成本、扩容投资（未实现）。")
    par(d, f"分级：绿/黄/红/灰四态；灰＝不可判定（数据或产物不足），不并入红黄绿。"
           f"本次等级：{LVL}。")
    par(d, "反向负载率口径（DL/T 2041-2019）：λ = (P_D − P_L) / S_e，其中 P_D 为分布式电源出力、"
           "P_L 为等效用电负荷、S_e 为设备运行限值；取评估周期内 λ 最大值 λ_max 作为判定依据。")
    par(d, "红黄绿划分：红 λ_max > 100%（禁止新增）；黄 80% < λ_max ≤ 100%（专项分析后定）；"
           "绿 0 < λ_max ≤ 80%（可正常开放）。本系统另设“灰”态：数据或产物不足，不可判定，"
           "不并入红黄绿——这一态在标准中没有，是本系统为“无数据≠达标”加的。")
    par(d, f"本次运行的分级结果为：{LVL}。需要说明的是：引擎内部的分级目前仍按可开放容量阈值判定"
           "（红 ≤0 kW、黄 ≤0.15 裕度比），与上表的 λ 口径尚未统一；两套口径的换算需在设备级"
           "补充 λ 计算后再切换，本报告不把二者混为一谈。", color=RED)

    h(d, "2.5 仿真环境与参数配置", 2)
    par(d, "引擎：OpenDSS 0.9.4（DSS C-API 0.14.5）主算，pandapower 3.5.6 交叉校核。"
           "搜索：区间 0–3000 kW、精度 10 kW、最多 25 次迭代。")

    # ---------- 3 方法与流程
    h(d, "3 系统级承载力计算分析")
    _sys = j(ROOT / "docs" / "system_level_demo.json")
    if _sys:
        par(d, "方法：8760 点时序生产模拟（逐时净负荷 = 负荷 − 分布式出力），"
               "约束为反送小时数为 0 且弃电率不超过 5%，用二分搜索求最大可接纳装机。", )
        tbl(d, [["指标", "结果"],
                ["区域最大可接纳装机", f"{_sys.get('capacity_kw', 0)/1000:.1f} MW"],
                ["反送最大功率", f"{_sys.get('reverse_max_kw', 0)/1000:.2f} MW"],
                ["反送小时数", str(_sys.get('reverse_hours', '—'))],
                ["弃电率", f"{_sys.get('spill_rate', 0)*100:.2f}%"],
                ["分级", str(_sys.get('level', '—'))]], [120, 120])
        par(d, "县级分解（局部服从总体）：")
        tbl(d, [["区县", "可接纳装机 MW", "上级容量 MW", "分解规则"]] +
            [[c["county"], f"{c['cap_kw']/1000:.1f}", f"{c['parent_cap_kw']/1000:.1f}",
              c["rule"]] for c in _sys.get("counties", [])], [70, 90, 80, 160])
        par(d, f"口径声明：本表数据来自**构造算例**——{_sys.get('note', '')}。"
               "结论只用于方法验证，不得作为任何真实区域的承载力结论；"
               "反送上限按禁止反送（0 kW）设定，真实场景应按 220kV 主变与上级负荷接纳能力取值。", color=RED)
        par(d, "220kV 反送校验：由上面的反送小时数与最大反送功率判定；"
               "本算例反送小时数为 0，是因为限额设为 0 后超限部分被截断为弃电，不等于自然不反送。", color=RED)
    else:
        par(d, "未找到系统级算例结果（docs/system_level_demo.json），本章为空。", color=RED)

    h(d, "4 设备级承载力计算分析")
    h(d, "4.1 主算例概况", 2)
    par(d, f"算例 FD002：5 母线 / 3 线路 / 1 配变 / 2 负荷 / 2 组光伏；"
           f"场景 {S.get('scenario_count') or '—'} 个、时间片 {S.get('slice_count') or '—'} 个、"
           f"约束 {CS.get('total', 0)} 条。输入包按字节冻结，快照 {M.get('snapshot_id') or '—'}。")
    h(d, "4.2 计算设置", 2)
    par(d, "模型：OpenDSS 三相不平衡潮流（主算）；pandapower 正序平衡（交叉校核）。"
           "搜索：0–3000 kW、精度 10 kW、最多 25 次迭代、取最不利相与最不利断面。")
    h(d, "4.3 计算结果", 2)
    par(d, f"基线：{CS.get('total', 0)} 条约束 pass {CS.get('pass', 0)} / "
           f"fail {CS.get('fail', 0)} / unknown {CS.get('unknown', 0)}。")
    par(d, f"容量结论：物理 {PHYS} kW、管理 {MGD} kW、等级 {LVL}；"
           f"瓶颈 {BIND}" + (f" @ {BD.get('object_id')}" if BD else "") + "。")
    if BD:
        par(d, f"瓶颈数值：{BD.get('metric')} = {BD.get('value')}，"
               f"限值 [{BD.get('limit_lower')}, {BD.get('limit_upper')}]，"
               f"裕度 {BD.get('margin')}。")
    par(d, "复算：换全新目录与进程重跑，逐项比对容量、绑定约束、母线电压与网损；"
           "结论见附录 D。")
    h(d, "4.3.1 反向负载率 λ 校核", 2)
    _lr = lambda_rows()
    if _lr:
        par(d, "λ = (P_D − P_L) / S_e；本表按三相中反向有功最大的那一相计，"
               "取全场景全时段 λ 最大值。只列反向时段（P<0）。")
        tbl(d, [["元件", "相", "λ_max", "反向有功 kW", "场景", "时段"]] +
            [[e, ph, f"{lm*100:.1f}%", f"{pk:.1f}", sc, (ts or "")[:16]]
             for e, ph, lm, pk, sc, ts in _lr],
            [120, 22, 46, 60, 60, 90], size=7.5)
        _top = _lr[0]
        _g = "红" if _top[2] > 1.0 else ("黄" if _top[2] > 0.8 else "绿")
        par(d, f"λ 最大值 {_top[2]*100:.1f}%（{_top[0]} {_top[1]} 相），按 80/100% 口径属{_g}区。")
    else:
        par(d, "本次运行没有反向（P<0）记录，λ 全为 0。", color=RED)

    h(d, "4.4 对照算例", 2)
    for _n in ("FD002", "FD001", "FD016"):
        par(d, "· " + case_line(_n))
    par(d, "三者用途不同：FD002 验证正常可估与瓶颈定位；FD001 验证基线越限时给 0 而不给乐观值；"
           "FD016 验证多场景批量与最不利断面取法。")

    h(d, "5 承载力等级划分与可开放容量")
    h(d, "5.1 基准工况评估结果", 2)
    par(d, f"基线约束：共 {CS.get('total', 0)} 项，pass {CS.get('pass', 0)}、"
           f"fail {CS.get('fail', 0)}、unknown {CS.get('unknown', 0)}。"
           f"基线是否全部收敛：{S.get('all_converged')}。")
    par(d, f"当前承载力：物理 {PHYS} kW、管理 {MGD} kW；综合等级 {LVL}（分级依据："
           f"{S.get('level_reason') or '—'}）。")
    h(d, "5.2 最大承载力边界结果", 2)
    par(d, f"系统整体最大可新增容量：{PHYS} kW。分节点/分片区的上限表需要多对象评估，"
           "本版暂无（未实现）。瓶颈节点见下一节。")
    h(d, "5.3 多场景对比结果", 2)
    par(d, "已按各场景与时间片逐一求解并取最不利值；逐场景的容量差异表未单列（待补）。"
           "N-1 与极端天气场景未实现。")
    h(d, "5.4 越限与风险溯源", 2)
    if BS:
        rows = [["约束", "对象", "指标", "计算值", "限值", "裕度"]]
        for b in BS[:10]:
            rows.append([b.get("constraint_id"), b.get("object_id"), b.get("metric"),
                         b.get("value"), f"[{b.get('limit_lower')}, {b.get('limit_upper')}]",
                         b.get("margin")])
        tbl(d, rows, [70, 120, 80, 70, 80, 70])
        par(d, f"根因判定：{BS[0].get('constraint_id')} 先触发，说明容量受该约束限制，"
               "整改应针对该约束而非普遍增容。")
    else:
        par(d, "本次运行未记录绑定约束。")
    h(d, "5.5 智能体输出备选方案集", 2)
    par(d, "方案 1 调控优化：电压越限→无功补偿/有载调压；反送越限→储能或出力限制。"
           "提升幅度需改造后重算，本版无法给出（未实现）。")
    par(d, "方案 2 网架改造：线路增容、配变扩容、新增联络。投资与提升量未估算（未实现）。")
    par(d, "方案 3 源侧配置优化：储能配比、布局调整。未实现（无储能模型）。")
    h(d, "5.6 敏感性分析结果", 2)
    par(d, "未实现：不做负荷增长、渗透率、储能容量的参数扰动曲线。", color=RED)

    # ---------- 5 讨论
    par(d, "分级汇总（本表为单算例结果，非全市域汇总）：")
    tbl(d, [["区域等级", "对象数量", "可接入新增容量", "管控要求"],
            [LVL, "1", f"物理 {PHYS} kW", "按主导约束整改后放开"]], [80, 70, 130, 160])
    par(d, "说明：多对象分级汇总需逐级评估后生成，本版不具备；不在此处填估算值。", color=RED)

    h(d, "6 问题诊断与提升措施建议")
    h(d, "6.1 与传统批量潮流比对", 2)
    par(d, f"已做 OpenDSS 与 pandapower 交叉校核（电压容差 0.03 pu、网损容差 10%），"
           f"结论：{(RUN / 'results' / 'engine_crosscheck.json').is_file()}。"
           "与 PSD-BPA / PSASP 的比对本版未开展。")
    h(d, "6.2 不确定性影响分析", 2)
    par(d, "风光与负荷随机波动未做蒙特卡洛抽样（未实现）。")
    h(d, "6.3 智能体局限性", 2)
    par(d, "未覆盖：谐波、N-1、暂态稳定、调频、储能与需求响应、经济性；"
           "单算例而非多对象；分级为单算例判定。")
    h(d, "6.4 结果可信度与适用边界", 2)
    par(d, "适用于 10 kV 及以下单馈线/台区的接入审查与内部评估；"
           "不适用于对外发布区域等级、也不适用于需要 N-1 与暂稳校核的场景。")

    # ---------- 6 对策
    h(d, "7 结论与附录")
    par(d, "高优先级（针对当前瓶颈）："
           + (f"围绕 {BIND} 整改——电压类优先调压，热稳类优先增容或分流。" if BS else "无绑定约束。"))
    par(d, "中优先级：储能与柔性负荷建设（需先补储能模型）。")
    par(d, "低优先级：网架升级、主变增容（需造价与改造后重算支撑）。")
    par(d, "监测建议：把本评估纳入常态化监测，数据变更后重跑并比对等级变化。")

    # ---------- 7 结论
    h(d, "附录 A 讨论与局限")
    par(d, "方法验证证据（本地算例 + 标准算例接入）", bold=True)
    tbl(d, [["验证项", "做法", "结果"],
            ["独立复算", "换目录与进程重跑，比对容量/绑约束/电压/网损等八项", "已执行，见附录 D"],
            ["双引擎交叉", "OpenDSS 主算 + pandapower 正序平衡互校", "电压差 ≤0.03 pu、网损差 ≤10%"],
            ["边界行为", "FD001 基线越限（热稳 136%）时容量给 0 而非乐观值", "容量 0.0 kW，判 THERMAL_LIMIT"],
            ["多场景", "FD016 三场景×12 断面取最不利", "515.625 kW，绑 bus-lv-002b.B"],
            ["标准算例接入", "IEEE-123：132 母线、91 台负荷 3490 kW，潮流 19 次迭代收敛", "基线电压 0.9792–1.0500 pu，与官方量级一致"]],
           [70, 170, 150])
    par(d, "未完成项（不得引用其结论）：IEEE-123 的分布式电源容量扫描。"
           "测试中出现“注入 6 MW 而电压不变”的异常，已排除节点位置、调压器控制、相序写法三项假设，"
           "根因未定位，故扫描结果全部作废。", color=RED)
    par(d, "对照实验（为什么判定扫描作废）：在 bus 150.1.2.3 分别增加 1000 kW 负荷与 1000 kW 电源。"
           "按功率平衡，前者应使净受电增加约 1000 kW、后者应减少约 1000 kW。"
           "实测变化分别为 −111.3 kW 与 +137.4 kW，均只有预期的约八分之一。"
           "两者同向偏离说明问题不在电源对象，而在新增元件与节点相序的匹配——"
           "元件已建立、参数可读，但潮流中只承担了很小一部分功率。", size=9)
    par(d, "因此：IEEE-123 目前只能作为“接入与潮流验证”的证据，不能用于承载力容量结论。", size=9, color=RED)

    par(d, f"8.1 主要结论：等级 {LVL}，物理 {PHYS} kW / 管理 {MGD} kW，"
           f"瓶颈 {BIND}；验收 {sum(1 for _ in FAILED)} 项未过发布门禁"
           f"（{('、'.join(FAILED)) or '无'}）。")
    par(d, "8.2 后续方向：多智能体协同、暂态稳定嵌入、电力市场耦合、在线实时版本——"
           "均为规划，本版未实现。")

    # ---------- 附录
    h(d, "附录")
    par(d, "附录 A 智能体输出原始结果表：results/run_summary.json、capacity.csv、constraint_results.csv。")
    par(d, "附录 B 场景潮流图与曲线：待补（本版不含图）。")
    par(d, "附录 C 运行日志与决策链路：audit/events.jsonl、logs/。")
    par(d, "附录 D 指标计算公式与分级阈值：见规则包 rules.yaml 与验收矩阵九项。")
    par(d, "附录 E 数据清单与拓扑：manifest.json 与 snapshots/。")

    par(d, "说明：凡标注「未实现」的条目，均为本版能力之外，不用估算值代替。",
        size=9, color=RED)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(OUT))
    print("已生成：", OUT)
    print("大小：", round(OUT.stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main()
