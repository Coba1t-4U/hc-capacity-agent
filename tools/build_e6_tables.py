# -*- coding: utf-8 -*-
"""生成 E6 实验表：可复现性证据（全部数字从运行产物读取，不手抄）。

数据来源：
  runs/ACCEPT/FD002/verification/verification.json   独立复算比对
  runs/ACCEPT/FD002/results/engine_crosscheck.json   双引擎交叉校核
  runs/ACCEPT/FD001/results/run_summary.json         边界行为（基线越限给 0）
  runs/ACCEPT/FD016/results/run_summary.json         多场景批量
"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
RUNS = ROOT / "runs" / "ACCEPT"
OUT = Path.home() / "Desktop" / "配网承载力" / "论文_实验E6_可复现性证据.docx"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def para(doc, text, *, size=10.5, bold=False, color=None, space=5, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.35
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def table(doc, rows, widths, size=8.5):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    for style in ("Light Grid Accent 1", "Table Grid"):
        try:
            t.style = style
            break
        except KeyError:
            continue
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = t.cell(i, j)
            c.width = Cm(widths[j])
            r = c.paragraphs[0].add_run(str(val))
            r.font.name = FONT
            r.font.size = Pt(size)
            r.bold = (i == 0)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def main() -> None:
    v = load(RUNS / "FD002" / "verification" / "verification.json")
    cc = load(RUNS / "FD002" / "results" / "engine_crosscheck.json")
    f1 = load(RUNS / "FD001" / "results" / "run_summary.json")
    f16 = load(RUNS / "FD016" / "results" / "run_summary.json")
    f2 = load(RUNS / "FD002" / "results" / "run_summary.json")

    doc = Document()
    doc.sections[0].left_margin = doc.sections[0].right_margin = Cm(2.0)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("实验 E6　可复现性证据")
    r.font.name = FONT
    r.font.size = Pt(18)
    r.bold = True
    r.font.color.rgb = BLUE
    para(doc, "全部数值取自运行产物；表下注明文件路径，可重跑复核", size=10,
         color=GREY, space=12)

    # 表 1 独立复算
    cmp_ = v.get("comparison") or {}
    rows = [["指标类别", "比对项数", "不一致项", "结论"],
            ["全部关键量", cmp_.get("total_metrics", "—"),
             cmp_.get("mismatch_count", "—"),
             "一致" if cmp_.get("pass") else "存在差异"],
            ["输入一致性", 1, 0 if v.get("input_match") else 1,
             "输入指纹一致" if v.get("input_match") else "输入指纹不同"]]
    if cmp_.get("diffs"):
        head = cmp_["diffs"][:4]
        for d in head:
            rows.append([d.get("metric_key", ""), 1,
                         0 if d.get("status") in ("exact_match", "within_tolerance") else 1,
                         d.get("status", "")])
    para(doc, "表 E6-1　独立复算：主运行与复算运行的关键量比对", bold=True, size=11, space=4)
    table(doc, rows, [5.0, 2.6, 2.6, 4.6])
    para(doc, "来源：runs/ACCEPT/FD002/verification/verification.json", size=8.5, color=GREY)

    # 表 2 双引擎
    pts = cc.get("points") or []
    tol = cc.get("tolerances") or {}
    rows2 = [["场景点", "时刻", "电压最大偏差 (pu)", "网损相对偏差 (%)", "判定"]]
    for p in pts:
        d = p.get("diff") or {}
        vmin = (d.get("vmin_pu") or {}).get("abs")
        vmax = (d.get("vmax_pu") or {}).get("abs")
        cand = [x for x in (vmin, vmax) if isinstance(x, (int, float))]
        vabs = max(cand) if cand else 0.0
        lo = d.get("losses_kw") or {}
        lo_d = lo.get("abs")
        base = lo.get("opendss")
        lo_pct = (abs(lo_d) / abs(base) * 100
                  if isinstance(lo_d, (int, float)) and isinstance(base, (int, float)) and base
                  else None)
        lp = (f"{lo_d:.3f} / {lo_pct:.2f}%" if lo_pct is not None else "—")
        rows2.append([p.get("point", ""), (p.get("timestamp") or "")[:16],
                      f"{vabs:.4f}", lp, p.get("verdict", "")])
    para(doc, "表 E6-2　双引擎交叉校核（OpenDSS 主算 × pandapower 正序平衡）", bold=True,
         size=11, space=4)
    table(doc, rows2, [3.0, 3.6, 4.2, 4.0, 2.6])
    para(doc, f"容差：电压 {tol.get('v_pu_abs')} pu、网损 {tol.get('loss_pct')} %。"
              "来源：runs/ACCEPT/FD002/results/engine_crosscheck.json", size=8.5, color=GREY)

    # 表 3 边界行为与多场景
    rows3 = [["算例", "物理可开放容量 (kW)", "绑定约束", "验证的实验意义"],
             ["FD002", f"{f2.get('capacity_physical_kw', '—')}",
              str(f2.get("binding_constraint", "—")),
              "正常情况下可给出容量并定位瓶颈"],
             ["FD001", f"{f1.get('capacity_physical_kw', '—')}",
              str(f1.get("binding_constraint", "—")),
              "基线越限时容量为 0，不给乐观值"],
             ["FD016", f"{f16.get('capacity_physical_kw', '—')}",
              str(f16.get("binding_constraint", "—")),
              "多场景批量，取最不利断面"]]
    para(doc, "表 E6-3　边界行为与多场景结果", bold=True, size=11, space=4)
    table(doc, rows3, [2.4, 4.6, 5.0, 6.0])
    para(doc, "来源：各算例 results/run_summary.json", size=8.5, color=GREY)

    # 表 4 复现步骤
    rows4 = [["步骤", "操作", "预期观察"],
             ["1", "拷贝 runs/ACCEPT/FD002 至新目录", "产物完整，可独立打开"],
             ["2", "重跑容量评估（同输入）",
              f"容量 {f2.get('capacity_physical_kw', '—')} kW，绑定约束一致"],
             ["3", "比对 verification.json", f"{cmp_.get('total_metrics', '—')} 项全部一致"],
             ["4", "改动任一输入文件后重跑", "输入指纹变化，口径绑定凭证失效"]]
    para(doc, "表 E6-4　复现步骤（评审可自行执行）", bold=True, size=11, space=4)
    table(doc, rows4, [1.6, 8.0, 8.0])

    para(doc, "写作提示", bold=True, size=11, space=4)
    para(doc, "① 表 E6-1、E6-2 放在“可复现性”小节，正文只写一句结论：关键量全部一致、"
              "双引擎偏差在容差内；不要在正文重复表格数字。", size=9.5, indent=0.3)
    para(doc, "② 表 E6-3 建议与“边界行为”合并叙述，强调 FD001 的意义："
              "系统在基线不合格时给 0，而不是给一个乐观容量。", size=9.5, indent=0.3)
    para(doc, "③ 表 E6-4 放附录，作为复现指南；图注须写明产物路径。", size=9.5, indent=0.3)
    para(doc, "④ 若审稿人要求公开数据，可提供 FD002 输入包与产物（不含任何客户信息）。",
         size=9.5, indent=0.3, color=RED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    print("已生成：", OUT)
    print("复算：", cmp_.get("total_metrics"), "项 / 不一致", cmp_.get("mismatch_count"))
    print("交叉校核点：", [p.get("point") for p in pts])


if __name__ == "__main__":
    main()
