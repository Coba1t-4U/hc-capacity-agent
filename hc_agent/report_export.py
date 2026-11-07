"""report_export.py — 把一次运行的 report.docx 加工成「可直接交付」的成品。

原 `report.docx` 只有正文（章节标题 + 内容），缺三样交付件必备的东西：

  ① 封面：报告名、评估对象、运行号、结论摘要、规则包/快照、编制日期、版本
  ② 目录：章节目录（**取自正文实际标题**，不用运行记录里的旧快照）
  ③ 页眉页脚：页眉写报告名，页脚给「第 X 页 / 共 Y 页」（Word 域）

为什么放在包内而不是 tools/：界面上的「导出成品报告」按钮要直接调用，
Python 包之外的脚本不能可靠导入。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

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


def _par(doc, text="", *, size=10.5, bold=False, color=None,
         align=WD_ALIGN_PARAGRAPH.LEFT, space=6):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    if text:
        r = p.add_run(text)
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        if color is not None:
            r.font.color.rgb = color
    return p


def _page_break_par() -> OxmlElement:
    p = OxmlElement("w:p")
    ppr = OxmlElement("w:pPr")
    ppr.append(OxmlElement("w:pageBreakBefore"))
    p.append(ppr)
    return p


def export(run_dir: Path, dest_dir: Optional[Path] = None) -> Path:
    """把 ``run_dir/report/report.docx`` 加工为成品，返回新文件路径。

    :param run_dir: 一次运行的目录（含 report/、results/、manifest.json）
    :param dest_dir: 输出目录，默认桌面下的「配网承载力」
    """
    run_dir = Path(run_dir)
    src = run_dir / "report" / "report.docx"
    if not src.is_file():
        raise FileNotFoundError(
            f"这次运行没有报告文件：{src}。"
            "可能停在「生成报告」之前的阶段，先看运算过程的阶段轨迹。")

    def _load(name: str) -> dict:
        p = run_dir / name
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

    summ = _load("results/run_summary.json") or _load("run_summary.json")
    man = _load("manifest.json")
    outcome = _load("run_outcome.json")

    run_id = summ.get("run_id") or run_dir.name
    level = LEVEL_ZH.get(str(summ.get("level")), str(summ.get("level") or "—"))
    phys = summ.get("capacity_physical_kw")
    mgd = summ.get("capacity_managed_kw")
    binding = summ.get("binding_constraint") or "—"
    rule = man.get("rule_version") or "—"
    snapshot = man.get("snapshot_id") or "—"
    today = datetime.now().strftime("%Y年%m月%d日")

    doc = Document(str(src))

    # 目录：以**正文实际标题**为准（运行记录里的 sections 可能是旧快照）
    sections = [p.text.strip() for p in doc.paragraphs
                if p.style.name.startswith("Heading") and p.text.strip()
                and p.text.strip()[0].isdigit()]
    if not sections:
        sections = (outcome.get("report") or {}).get("sections") or [
            f"{i}. （章节名缺失）" for i in range(1, 13)]

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
    for piece in ("第 ",):
        r = fp.add_run(piece)
        r.font.name = FONT
        r.font.size = Pt(9)
        r.font.color.rgb = GREY
    _field(fp, "PAGE")
    for piece in (" 页 / 共 ",):
        r = fp.add_run(piece)
        r.font.name = FONT
        r.font.size = Pt(9)
        r.font.color.rgb = GREY
    _field(fp, "NUMPAGES")
    r = fp.add_run(" 页")
    r.font.name = FONT
    r.font.size = Pt(9)
    r.font.color.rgb = GREY

    # ---- 目录与封面（先追加到文末，再整体搬到开头）
    toc = [_par(doc, "目　录", size=16, bold=True, color=BLUE,
                align=WD_ALIGN_PARAGRAPH.CENTER, space=12)]
    toc += [_par(doc, s, size=10, space=4) for s in sections]

    # scope_ids 可能是列表，也可能是字符串（"FD-002"）；字符串不能逐字符拼接
    scope = summ.get("scope_ids")
    if isinstance(scope, (list, tuple)):
        scope_txt = "、".join(str(x) for x in scope)
    else:
        scope_txt = str(scope or "—")

    cover = [_par(doc, "配网承载力智能体评估报告", size=24, bold=True, color=BLUE,
                  align=WD_ALIGN_PARAGRAPH.CENTER, space=6),
             _par(doc, "承载能力评估与可开放容量结论", size=12, color=GREY,
                  align=WD_ALIGN_PARAGRAPH.CENTER, space=26)]
    for k, v in (("评估对象", f"{summ.get('scope_type') or '—'}　{scope_txt}"),
                 ("运行号", run_id),
                 ("结论等级", level),
                 ("可开放容量", f"物理 {phys} kW　/　管理 {mgd} kW"),
                 ("主导限制因素", binding),
                 ("规则包 / 快照", f"{rule}　/　{snapshot}"),
                 ("编制日期", today),
                 ("报告版本", "配网承载力智能体 V1.1.0")):
        p = _par(doc, "", size=10.5, space=10, align=WD_ALIGN_PARAGRAPH.CENTER)
        rk = p.add_run(f"{k}：")
        rk.font.name = FONT
        rk.font.size = Pt(10.5)
        rk.font.bold = True
        rv = p.add_run(str(v))
        rv.font.name = FONT
        rv.font.size = Pt(10.5)
        cover.append(p)
    cover.append(_par(
        doc,
        "说明：本报告的电气数值全部由 OpenDSS 引擎计算，并经 pandapower 交叉校核与"
        "独立复算；结论用于内部评估与答复支撑，对外发布的区域等级须按省公司口径另行生成。",
        size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=0))

    body = doc.element.body
    for p in reversed(toc):
        body.remove(p._p)
        body.insert(0, p._p)
    body.insert(len(toc), _page_break_par())
    for p in reversed(cover):
        body.remove(p._p)
        body.insert(0, p._p)
    body.insert(len(cover), copy.deepcopy(_page_break_par()))

    out_dir = Path(dest_dir) if dest_dir else (Path.home() / "Desktop" / "配网承载力")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"配网承载力评估报告_{run_id}_{datetime.now():%Y%m%d}.docx"
    doc.save(str(out))
    return out
