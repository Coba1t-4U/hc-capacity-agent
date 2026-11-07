# -*- coding: utf-8 -*-
"""把运行目录里的 report.docx 加工成「可直接交付」的成品报告。

原 report.docx 只有 12 章正文（标题+内容），缺三样交付件必备的东西：
  ① 封面：报告名、评估对象、运行号、结论摘要、版本与规则包、编制日期
  ② 目录：12 章清单（静态文本，不带页码，避免 Word 域未更新时显示错误页码）
  ③ 页眉页脚：页眉写报告名，页脚给「第 X 页 / 共 Y 页」（Word 域）

输出：桌面\\配网承载力评估报告_<运行号>_<日期>.docx
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
DESK = Path.home() / "Desktop" / "配网承载力"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)

LEVEL_ZH = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}


def _field(par, instr: str) -> None:
    """在段落里插一个 Word 域（PAGE / NUMPAGES）。"""
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "1"
    r.append(t)
    fld.append(r)
    par._p.append(fld)


def _new_par(doc, text="", *, size=10.5, bold=False, color=None,
             align=WD_ALIGN_PARAGRAPH.LEFT, space=6, page_break=False):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.page_break_before = page_break
    if text:
        r = p.add_run(text)
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        if color is not None:
            r.font.color.rgb = color
    return p


def _move_to_top(doc, par, index=0):
    body = doc.element.body
    body.remove(par._p)
    body.insert(index, par._p)


def export(run_dir: Path) -> Path:
    src = run_dir / "report" / "report.docx"
    if not src.is_file():
        raise SystemExit(f"找不到报告：{src}（先跑一次评估或 generate_report）")

    # run_summary.json 在 results/ 下；run_outcome.json 在根目录，二者取其一
    _summ_path = run_dir / "results" / "run_summary.json"
    if not _summ_path.is_file():
        _summ_path = run_dir / "run_summary.json"
    summ = json.loads(_summ_path.read_text(encoding="utf-8"))
    man = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    outcome = {}
    if (run_dir / "run_outcome.json").is_file():
        outcome = json.loads((run_dir / "run_outcome.json").read_text(encoding="utf-8"))

    run_id = summ.get("run_id") or run_dir.name
    level = LEVEL_ZH.get(str(summ.get("level")), str(summ.get("level") or "—"))
    phys = summ.get("capacity_physical_kw")
    mgd = summ.get("capacity_managed_kw")
    binding = summ.get("binding_constraint") or "—"
    rule = man.get("rule_version") or "—"
    snapshot = man.get("snapshot_id") or "—"
    today = datetime.now().strftime("%Y年%m月%d日")

    doc = Document(str(src))

    # ---- 目录：**从 docx 实际标题生成**，不用运行记录里的旧清单。
    # 运行记录里的 sections 可能是上一次生成的快照（章节改版后会对不上），
    # 而目录必须与正文一致，所以以正文为准。
    sections = [p.text.strip() for p in doc.paragraphs
                if p.style.name.startswith("Heading") and p.text.strip()
                and p.text.strip()[0].isdigit()]
    if not sections:
        sections = (outcome.get("report") or {}).get("sections") or \
                   [f"{i}. （章节名缺失）" for i in range(1, 13)]

    # ---- 页眉页脚
    sec = doc.sections[0]
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    hr = hp.add_run("配网承载力智能体评估报告")
    hr.font.name = FONT
    hr.font.size = Pt(9)
    hr.font.color.rgb = GREY

    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r1 = fp.add_run("第 ")
    _field(fp, "PAGE")
    r2 = fp.add_run(" 页 / 共 ")
    _field(fp, "NUMPAGES")
    r3 = fp.add_run(" 页")
    for r in (r1, r2, r3):
        r.font.name = FONT
        r.font.size = Pt(9)
        r.font.color.rgb = GREY
    for r in fp.runs:
        r.font.name = FONT
        r.font.size = Pt(9)

    # ---- 先在文末造好「目录」与「封面」段落，再整体搬到开头（倒序插入）
    toc_title = _new_par(doc, "目　录", size=16, bold=True, color=BLUE,
                         align=WD_ALIGN_PARAGRAPH.CENTER, space=12)
    toc_pars = [toc_title]
    for s in sections:
        toc_pars.append(_new_par(doc, s, size=10, space=4))

    cover = []
    cover.append(_new_par(doc, "配网承载力智能体评估报告", size=24, bold=True,
                          color=BLUE, align=WD_ALIGN_PARAGRAPH.CENTER, space=6))
    cover.append(_new_par(doc, "承载能力评估与可开放容量结论", size=12,
                          color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=26))
    # scope_ids 可能是列表，也可能是一个字符串（如 "FD002"）；字符串不能逐字符 join
    _scope = summ.get("scope_ids") or (man.get("task") or {}).get("scope_ids")
    if isinstance(_scope, (list, tuple)):
        _scope_txt = "、".join(str(x) for x in _scope)
    else:
        _scope_txt = str(_scope or "—")
    rows = [
        ("评估对象", f"{summ.get('scope_type') or '—'}　{_scope_txt}"),
        ("运行号", run_id),
        ("结论等级", level),
        ("可开放容量", f"物理 {phys} kW　/　管理 {mgd} kW"),
        ("主导限制因素", binding),
        ("规则包 / 快照", f"{rule}　/　{snapshot}"),
        ("编制日期", today),
        ("报告版本", "配网承载力智能体 V1.1.0"),
    ]
    for k, v in rows:
        p = _new_par(doc, "", size=10.5, space=10, align=WD_ALIGN_PARAGRAPH.CENTER)
        rk = p.add_run(f"{k}：")
        rk.font.name = FONT
        rk.font.size = Pt(10.5)
        rk.font.bold = True
        rv = p.add_run(str(v))
        rv.font.name = FONT
        rv.font.size = Pt(10.5)
        cover.append(p)
    cover.append(_new_par(
        doc,
        "说明：本报告的电气数值全部由 OpenDSS 引擎计算，并经理 pandapower 交叉校核与"
        "独立复算；结论用于内部评估与答复支撑，对外发布的区域等级须按省公司口径另行生成。",
        size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=0))

    # 把封面与目录按顺序插到最前：先插目录（在正文前），再插封面
    body = doc.element.body
    for p in reversed(toc_pars):
        body.remove(p._p)
        body.insert(0, p._p)
    # 目录后分页：让正文从新页开始
    pb = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    prev = OxmlElement("w:pageBreakBefore")
    ppr.append(prev)
    pb.append(ppr)
    body.insert(len(toc_pars), pb)
    for p in reversed(cover):
        body.remove(p._p)
        body.insert(0, p._p)
    # 封面后分页
    pb2 = copy.deepcopy(pb)
    body.insert(len(cover), pb2)

    DESK.mkdir(parents=True, exist_ok=True)
    out = DESK / f"配网承载力评估报告_{run_dir.name}_{datetime.now():%Y%m%d}.docx"
    doc.save(str(out))
    return out


if __name__ == "__main__":
    run = ROOT / "runs" / "ACCEPT" / "FD002"
    path = export(run)
    print(f"已生成：{path}")
    print(f"大小：{path.stat().st_size / 1024:.0f} KB")
