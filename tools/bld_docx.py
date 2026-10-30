# -*- coding: utf-8 -*-
"""生成逐页讲稿 Word（与 PPT 页码一一对应）。

章节标题里的页数与分钟数、末尾时间总表，全部由同一份 slides_meta 计算，
避免手写数字与正文对不上（实测踩过：标题写 10 分钟、表里算出 11 分钟）。
"""
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HEI = "黑体"
SONG = "宋体"

# (章节名, 首页, 末页, 要点)
SECTIONS = [
    ("引入与推导", 1, 9, "三种推导、收敛速度对比"),
    ("几何意义", 10, 13, "切线法图示、切线方程推导"),
    ("收敛性分析", 14, 22, "定义 6.2、定理 6.3 及证明、初值反例"),
    ("教材例题", 23, 29, "例 6.7 / 6.8、开方与求倒数、计算步骤"),
    ("改进格式与对比", 30, 33, "下山法、弦截法、定理 6.4"),
    ("小结与作业", 34, 35, "易错点、思考题、作业"),
]


def _font(run, ea=SONG, ascii_=None, size=12, bold=False, color=None):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.name = ascii_ or ea
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), ea)
    rfonts.set(qn("w:ascii"), ascii_ or ea)
    rfonts.set(qn("w:hAnsi"), ascii_ or ea)


def _para(doc, text="", *, ea=SONG, size=12, bold=False, align=None,
          first_indent=0.74, space_before=0, space_after=4, line=1.5,
          color=None):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line
    if first_indent:
        pf.first_line_indent = Cm(first_indent)
    if align:
        p.alignment = align
    if text:
        run = p.add_run(text)
        _font(run, ea=ea, size=size, bold=bold, color=color)
    return p


def _heading(doc, text, level=1, page_break=False):
    sizes = {0: 26, 1: 16, 2: 14}
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(18 if level else 0)
    pf.space_after = Pt(8 if level else 6)
    pf.line_spacing = 1.25
    pf.keep_with_next = True
    if page_break:
        pf.page_break_before = True
    if level == 0:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    _font(run, ea=HEI, size=sizes[level], bold=True,
          color="1F3D95" if level <= 1 else "1F4E9C")
    return p


def _add_page_number_footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    _font(run, ea=SONG, size=9, color="808080")
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._element.addnext(fld)
    inner = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "18")
    rpr.append(sz)
    inner.append(rpr)
    t = OxmlElement("w:t")
    t.text = "1"
    inner.append(t)
    fld.append(inner)


def _table(doc, rows, widths, *, head_fill="1F4E9C"):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Table Grid"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            t.columns[j].width = widths[j]
            cell = t.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.line_spacing = 1.25
            r = p.add_run(val)
            _font(r, ea=HEI if i == 0 else SONG, size=10.5, bold=(i == 0),
                  color="FFFFFF" if i == 0 else None)
            if i == 0:
                shd = OxmlElement("w:shd")
                shd.set(qn("w:fill"), head_fill)
                cell._tc.get_or_add_tcPr().append(shd)
    return t


def build_docx(slides_meta, out_path):
    """slides_meta: [(页码, 标题, 讲稿, (起始分钟, 时长))]，按页码升序。"""
    timing = {no: t for no, _, _, t in slides_meta}

    def span(a, b):
        start = timing[a][0]
        last = timing[b]
        return start, last[0] + last[1] - start

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(2.5)
    sec.top_margin = Cm(2.4)
    sec.bottom_margin = Cm(2.3)

    style = doc.styles["Normal"]
    style.font.name = SONG
    style.font.size = Pt(12)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), SONG)

    # ---------------- 封面
    for _ in range(3):
        doc.add_paragraph()
    _heading(doc, "Newton 迭代法及其收敛性分析", 0)
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("—— 逐页讲稿（共 35 页 · 45 分钟）")
    _font(run, ea=HEI, size=15, color="5A6678")
    for _ in range(2):
        doc.add_paragraph()

    info = [["项目", "内容"],
            ["课程名称", "数值分析"],
            ["讲授内容", "第 6 章 方程求根 · 6.3 Newton 法 / 6.4 弦截法"],
            ["主讲人", "陈宇泓"],
            ["授课时长", "45 分钟"],
            ["课件页数", "35 页（含教材例题 5 道、示意图 4 幅）"],
            ["教材依据", "李庆扬《数值分析》（第 4 版）6.3、6.4 节；"
                       "《数值分析》第 5 版（南京大学）对应章节"]]
    _table(doc, info, (Cm(3.4), Cm(12.2)))

    # ---------------- 使用说明
    _heading(doc, "使用说明", 1, page_break=True)
    tips = [
        "本稿与课件严格逐页对应，页码一致，可直接照读，也可按自己的语速裁剪。",
        "每页标注建议用时（起止分钟），合计 45 分钟；课堂时间紧时可压缩第 3、27、29 页的展开，"
        "优先保证第 14–22 页的收敛性分析。",
        "公式读法已在稿中给出（如 f′(xₖ) 读作「f 撇 x k」），首次出现读全称，后续可简化。",
        "教材式号、例题号、表号与本稿一致，便于学生对照课本。",
        "第 22、28 页含反例，建议留出停顿让学生自己先算一步，效果更好。",
    ]
    for t in tips:
        _para(doc, "· " + t, size=11.5, first_indent=0)

    # ---------------- 正文
    for name, a, b, key in SECTIONS:
        _, mins = span(a, b)
        _heading(doc, f"{name}（第 {a}–{b} 页，约 {mins:.0f} 分钟）", 1,
                 page_break=True)
        _para(doc, f"本节要点：{key}", size=10.5, first_indent=0,
              space_after=8, color="5A6678")
        for no, stitle, note, tm in slides_meta:
            if not (a <= no <= b):
                continue
            s0, dur = tm
            _heading(doc, f"第 {no} 页｜{stitle}", 2)
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.keep_with_next = True
            run = p.add_run(f"建议用时：{s0:.1f}′ – {s0 + dur:.1f}′"
                            f"（约 {dur * 60:.0f} 秒）")
            _font(run, ea=SONG, size=9.5, color="F26522")
            for seg in [s for s in note.split("\n") if s.strip()]:
                _para(doc, seg, size=12, first_indent=0.74, line=1.6)

    # ---------------- 时间总表
    _heading(doc, "附：时间分配总表", 1, page_break=True)
    rows = [["环节", "页码", "时长", "要点"]]
    total = 0.0
    for name, a, b, key in SECTIONS:
        _, mins = span(a, b)
        total += mins
        rows.append([name, f"{a} – {b}", f"{mins:.0f} 分钟", key])
    rows.append(["合计", "1 – 35", f"{total:.0f} 分钟", "含开场与过渡缓冲"])
    _table(doc, rows, (Cm(4.2), Cm(2.4), Cm(2.4), Cm(6.6)))

    _add_page_number_footer(sec)
    doc.save(str(out_path))
    return out_path
