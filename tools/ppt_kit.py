# -*- coding: utf-8 -*-
"""生成《Newton 迭代法及其收敛性分析》PPT（35 页）与逐页讲稿 Word。

设计沿用模板：超宽 44.24×24.90cm、顶部横幅+底部色带、微软雅黑、
深蓝 #1F3D95 / #1F4E9C、正文 #1B2A41、强调橙 #F26522、绿 #2E9E4F。
内容取自教材（李庆扬《数值分析》6.3 Newton 法、6.4 弦截法）
与《数值分析》第5版对应章节，公式编号与例题编号保持一致。
"""
import copy
import re
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Cm, Pt

ROOT = Path(__file__).resolve().parent.parent
TPL = Path(r"C:\Users\Chen\xwechat_files\wxid_uqcqdcqcchkg22_724f\msg\file\2026-10"
           r"\4.1单相半波整流电路(1).pptx")
OUT_DIR = ROOT / "build_ppt"
ASSETS = OUT_DIR / "assets"

FONT = "微软雅黑"
C_TITLE = "1F3D95"      # 深蓝（主标题）
C_HEAD = "1F4E9C"       # 次标题
C_BODY = "1B2A41"       # 正文
C_MUTED = "5A6678"      # 次要
C_ORANGE = "F26522"
C_GREEN = "2E9E4F"
C_LIGHT = "BFD6F5"
C_LINE = "D8E4F5"

CARD_FILL = {"blue": "E8F1FD", "orange": "FFF1E8", "green": "EBF7EF",
             "grey": "F4F6FA", "white": "FFFFFF"}
CARD_LINE = {"blue": C_HEAD, "orange": C_ORANGE, "green": C_GREEN,
             "grey": "C9D6E8", "white": C_LINE}

SW, SH = Cm(44.24), Cm(24.90)
MX = 1.55                      # 左右留白
CW = 44.24 - 2 * MX            # 内容区宽度 ≈ 41.14
TOP = 4.10                     # 内容区起始 y
BOT = 20.55                    # 内容区结束 y


# ---------------------------------------------------------------- 富文本
TOKEN = re.compile(r"(\*\*|_\{[^}]*\}|\^\{[^}]*\})")


def _set_baseline(run, value: int) -> None:
    run.font._element.set("baseline", str(value))


def add_runs(paragraph, text: str, size: float, color: str = C_BODY,
             bold: bool = False, font: str = FONT, italic: bool = False):
    """支持 **粗体**、_{下标}、^{上标} 的简易标记。"""
    is_bold = bold
    for piece in TOKEN.split(text):
        if not piece:
            continue
        if piece == "**":
            is_bold = not is_bold
            continue
        sub = piece.startswith("_{")
        sup = piece.startswith("^{")
        body = piece[2:-1] if (sub or sup) else piece
        run = paragraph.add_run()
        run.text = body
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = is_bold
        run.font.italic = italic
        run.font.color.rgb = RGBColor.from_string(color)
        # 中文字体也要显式指定
        rpr = run.font._element
        ea = rpr.find(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        if ea is None:
            from lxml import etree
            ea = etree.SubElement(
                rpr, "{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
        ea.set("typeface", font)
        if sub:
            _set_baseline(run, -25000)
        elif sup:
            _set_baseline(run, 30000)
    return paragraph


def textbox(slide, x, y, w, h, *, anchor=MSO_ANCHOR.TOP,
            align=PP_ALIGN.LEFT, margins=(0, 0, 0, 0)):
    box = slide.shapes.add_textbox(Cm(x), Cm(y), Cm(w), Cm(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left, tf.margin_right, tf.margin_top, tf.margin_bottom = \
        [Cm(m) for m in margins]
    tf.paragraphs[0].alignment = align
    return box


def para(box, text, size, color=C_BODY, bold=False, *, first=False,
         space_before=0, line=1.15, align=PP_ALIGN.LEFT, italic=False,
         font=FONT, indent=0):
    tf = box.text_frame
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    p.line_spacing = line
    if space_before:
        p.space_before = Pt(space_before)
    if indent:
        p.level = indent
    add_runs(p, text, size, color, bold, font, italic)
    return p


# ---------------------------------------------------------------- 版式
SLIDE_META = []


def new_slide(prs, title, kicker, page, note=""):
    SLIDE_META.append((page, title))
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    for name, x, y, w, h in (("Picture_1.png", 0, 0, 44.24, 2.77),
                             ("Picture_2.png", 0, 3.23, 44.24, 0.15),
                             ("Picture_3.png", 0, 20.88, 44.24, 3.23)):
        slide.shapes.add_picture(str(ASSETS / name), Cm(x), Cm(y),
                                 Cm(w), Cm(h))
    # 标题
    tb = textbox(slide, 1.07, 0.18, 31.5, 2.2, anchor=MSO_ANCHOR.MIDDLE)
    para(tb, title, 30, "FFFFFF", True, first=True)
    # 右侧小标
    kb = textbox(slide, 30.6, 0.30, 12.7, 1.9, anchor=MSO_ANCHOR.MIDDLE,
                 align=PP_ALIGN.RIGHT)
    para(kb, kicker, 16, C_LIGHT, True, first=True, align=PP_ALIGN.RIGHT)
    # 页脚
    fb = textbox(slide, 5.46, 21.60, 22.86, 1.3)
    para(fb, "数值分析 · 第6章 方程求根 · 6.3 Newton 迭代法", 16, C_HEAD,
         first=True)
    pb = textbox(slide, 39.8, 21.60, 3.4, 1.3, align=PP_ALIGN.RIGHT)
    para(pb, f"第 {page} 页", 16, C_HEAD, first=True, align=PP_ALIGN.RIGHT)
    if note:
        slide.notes_slide.notes_text_frame.text = note
    return slide


def card(slide, x, y, w, h, accent="blue", shape="roundRect", adj=6000,
         line_w=2.0):
    shp = slide.shapes.add_shape(
        {"roundRect": MSO_SHAPE.ROUNDED_RECTANGLE,
         "rect": MSO_SHAPE.RECTANGLE}[shape],
        Cm(x), Cm(y), Cm(w), Cm(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor.from_string(CARD_FILL[accent])
    shp.line.color.rgb = RGBColor.from_string(CARD_LINE[accent])
    shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    if shape == "roundRect":
        shp.adjustments[0] = adj / 100000.0
    shp.text_frame.text = ""
    return shp


def block(slide, x, y, w, h, heading, lines, *, accent="blue",
          hsize=22, bsize=17, hcolor=None, space=8, line=1.2,
          bullets=False, body_align=PP_ALIGN.LEFT, pad=0.55):
    """一个卡片：标题 + 若干行正文。lines 里每项可以是 str 或 (str, 参数dict)。"""
    card(slide, x, y, w, h, accent)
    inner_x = x + pad
    inner_w = w - 2 * pad
    hcolor = hcolor or (C_ORANGE if accent == "orange" else
                        C_GREEN if accent == "green" else
                        C_HEAD if accent == "blue" else C_TITLE)
    tb = textbox(slide, inner_x, y + pad * 0.7, inner_w, h - pad * 1.2)
    para(tb, heading, hsize, hcolor, True, first=True, line=1.05)
    for i, item in enumerate(lines):
        if isinstance(item, tuple):
            txt, kw = item
        else:
            txt, kw = item, {}
        para(tb, txt, kw.get("size", bsize),
             kw.get("color", C_BODY), kw.get("bold", False),
             space_before=kw.get("space", space),
             line=kw.get("line", line),
             align=kw.get("align", body_align),
             italic=kw.get("italic", False))
    return tb


def formula_band(slide, x, y, w, h, lines, *, accent="blue", fill=None,
                 size=26, color=C_TITLE, align=PP_ALIGN.CENTER, note=None,
                 note_size=15):
    """公式横条：浅底 + 居中公式。"""
    bg = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                Cm(x), Cm(y), Cm(w), Cm(h))
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor.from_string(
        fill or CARD_FILL[accent])
    bg.line.color.rgb = RGBColor.from_string(CARD_LINE[accent])
    bg.line.width = Pt(2.0)
    bg.shadow.inherit = False
    bg.adjustments[0] = 0.06
    bg.text_frame.text = ""
    tb = textbox(slide, x + 0.5, y + 0.25, w - 1.0, h - 0.5,
                 anchor=MSO_ANCHOR.MIDDLE, align=align)
    first = True
    for ln in lines:
        if isinstance(ln, tuple):
            txt, kw = ln
        else:
            txt, kw = ln, {}
        para(tb, txt, kw.get("size", size), kw.get("color", color),
             kw.get("bold", False), first=first, line=kw.get("line", 1.1),
             align=kw.get("align", align), space_before=kw.get("space", 4))
        first = False
    if note:
        nb = textbox(slide, x + 0.6, y + h + 0.15, w - 1.2, 1.0)
        para(nb, note, note_size, C_MUTED, first=True)
    return tb


def table(slide, x, y, w, h, header, rows, *, col_w=None, size=16,
          header_size=16, first_col_bold=True, align_right=True):
    nrow, ncol = len(rows) + 1, len(header)
    gt = slide.shapes.add_table(nrow, ncol, Cm(x), Cm(y), Cm(w), Cm(h)).table
    if col_w:
        total = sum(col_w)
        for i, cw in enumerate(col_w):
            gt.columns[i].width = Cm(w * cw / total)
    for j, htxt in enumerate(header):
        cell = gt.cell(0, j)
        cell.text = ""
        cell.fill.solid()
        cell.fill.fore_color.rgb = RGBColor.from_string(C_HEAD)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = cell.margin_right = Cm(0.2)
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        add_runs(p, htxt, header_size, "FFFFFF", True)
    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = gt.cell(i, j)
            cell.text = ""
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(
                "FFFFFF" if i % 2 else "F2F6FC")
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Cm(0.2)
            p = cell.text_frame.paragraphs[0]
            p.alignment = (PP_ALIGN.CENTER if j == 0 else
                           PP_ALIGN.RIGHT if align_right else PP_ALIGN.LEFT)
            add_runs(p, str(val), size, C_BODY,
                     bold=(first_col_bold and j == 0))
    return gt


A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _arrow_end(conn, where="tail", kind="triangle", size="med"):
    from lxml import etree
    ln = conn.line._get_or_add_ln()
    el = etree.SubElement(ln, A_NS + ("tailEnd" if where == "tail"
                                      else "headEnd"))
    el.set("type", kind)
    el.set("w", size)
    el.set("len", size)


def seg(slide, x1, y1, x2, y2, *, color=C_BODY, w=1.5, dash=None,
        head=False, tail=False):
    """直线段（cm 坐标），可选箭头。"""
    from pptx.enum.shapes import MSO_CONNECTOR
    conn = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    conn.line.color.rgb = RGBColor.from_string(color)
    conn.line.width = Pt(w)
    if dash:
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        conn.line.dash_style = {"dash": MSO_LINE_DASH_STYLE.DASH,
                                "dot": MSO_LINE_DASH_STYLE.ROUND_DOT}[dash]
    if tail:
        _arrow_end(conn, "tail")
    if head:
        _arrow_end(conn, "head")
    return conn


def polyline(slide, pts, *, color=C_TITLE, w=2.5, close=False, fill=None):
    """折线（cm 坐标点列），用于画曲线。"""
    emu = [(Cm(x), Cm(y)) for x, y in pts]
    builder = slide.shapes.build_freeform(emu[0][0], emu[0][1], scale=1.0)
    builder.add_line_segments(emu[1:], close=close)
    shape = builder.convert_to_shape()
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    else:
        shape.fill.background()
    shape.line.color.rgb = RGBColor.from_string(color)
    shape.line.width = Pt(w)
    shape.shadow.inherit = False
    return shape


def dot(slide, cx, cy, d=0.22, *, fill=C_HEAD, line="FFFFFF"):
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Cm(cx - d / 2),
                                 Cm(cy - d / 2), Cm(d), Cm(d))
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    shp.line.color.rgb = RGBColor.from_string(line)
    shp.line.width = Pt(1.0)
    shp.shadow.inherit = False
    return shp


def label(slide, x, y, w, h, text, size=14, color=C_BODY, bold=False,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, italic=False):
    box = textbox(slide, x, y, w, h, anchor=anchor, align=align)
    para(box, text, size, color, bold, first=True, align=align,
         italic=italic, line=1.0)
    return box


def badge(slide, x, y, w, h, text, *, fill=C_HEAD, size=18, color="FFFFFF"):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                 Cm(x), Cm(y), Cm(w), Cm(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = RGBColor.from_string(fill)
    shp.line.fill.background()
    shp.shadow.inherit = False
    shp.adjustments[0] = 0.3
    tf = shp.text_frame
    tf.margin_left = tf.margin_right = Cm(0.2)
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    add_runs(p, text, size, color, True)
    return shp
