# -*- coding: utf-8 -*-
"""以《配电网潮流计算智能体.pptx》为模板，生成《配网承载力智能体.pptx》。

**做法**：加载模板（保留母版/主题/字体/配色），清空原页，按模板的版式参数
重画。为什么不直接改模板文字：模板 31 页的版式各不相同，逐页改文字容易
把卡片撑破；而模板的**设计系统**（标题带、三列卡片、配色）是可以复用的，
按同一套网格重排更可控。

重点章节：业务痛点（3 页）、效率提升（3 页）。
"""

from __future__ import annotations

import copy

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Cm, Pt

TPL = (r"C:\Users\Chen\Desktop\配网承载力\配电网潮流计算智能体.pptx")
OUT = (r"C:\Users\Chen\Desktop\配网承载力\配网承载力智能体.pptx")
SHOTS = (r"C:\Users\Chen\Documents\deepseek-harness\default-workspace"
         r"\hc_agent_project\tests\_desktop_shots")

# ---- 版式常量（取自模板实测）
PAGE_W, PAGE_H = 33.87, 19.05
BAND_H = 2.90
TITLE_L, TITLE_T, TITLE_W, TITLE_H = 1.09, 0.38, 30.35, 2.13
SUB_L, SUB_T, SUB_W = 1.68, 2.62, 30.43
CARD_T, CARD_H, CARD_W = 6.20, 8.60, 9.78
CARD_X = (1.47, 12.04, 22.58)

# ---- 配色（取自模板主题与形状）
PRIMARY = RGBColor(0x01, 0x4C, 0x83)
DARK = RGBColor(0x01, 0x2E, 0x57)
BLUE = RGBColor(0x33, 0x71, 0xA4)
BLUE2 = RGBColor(0x31, 0x5D, 0x7D)
BROWN = RGBColor(0x8A, 0x60, 0x3B)
RED = RGBColor(0x86, 0x4B, 0x48)
GREEN = RGBColor(0x24, 0x67, 0x3E)
GREY = RGBColor(0x5A, 0x62, 0x6A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLACK = RGBColor(0x1A, 0x1A, 0x1A)
CARDBG = RGBColor(0xF7, 0xF9, 0xFB)
BAND_LIGHT = RGBColor(0xEA, 0xF1, 0xF7)

FONT = "微软雅黑"


# ------------------------------------------------------------------ 基础件
def _text(slide, left, top, width, height, paras, *, size=14,
          color=BLACK, bold=False, align=PP_ALIGN.LEFT, spacing=1.15,
          anchor=MSO_ANCHOR.TOP):
    """多段落文本框。``paras`` 是 list[str]，或 list[(str, dict)] 用局部样式。"""
    box = slide.shapes.add_textbox(Cm(left), Cm(top), Cm(width), Cm(height))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    for i, item in enumerate(paras):
        txt, sty = (item, {}) if isinstance(item, str) else item
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = sty.get("align", align)
        para.line_spacing = sty.get("spacing", spacing)
        if i:
            para.space_before = Pt(sty.get("space_before", 5))
        run = para.add_run()
        run.text = txt
        f = run.font
        f.name = FONT
        f.size = Pt(sty.get("size", size))
        f.bold = sty.get("bold", bold)
        f.color.rgb = sty.get("color", color)
    return box


def _rect(slide, left, top, width, height, fill=None, line=None,
          shape=MSO_SHAPE.ROUNDED_RECTANGLE, line_w=1.0):
    shp = slide.shapes.add_shape(shape, Cm(left), Cm(top), Cm(width), Cm(height))
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shp.adjustments[0] = 0.06
        except Exception:
            pass
    if shp.has_text_frame:
        shp.text_frame.text = ""
    return shp


def _blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[8])   # 布局「空白」


def _page_no(slide, n):
    _text(slide, 31.37, 17.98, 1.4, 0.6, [str(n)], size=12, color=GREY,
          align=PP_ALIGN.RIGHT)


def _head(slide, title, subtitle="", n=None, sub_size=19):
    """标题带 + 副标题（模板：深色带 2.9cm，白字 34pt）。"""
    _rect(slide, 0, 0, PAGE_W, BAND_H, fill=PRIMARY, shape=MSO_SHAPE.RECTANGLE)
    _text(slide, TITLE_L, TITLE_T, TITLE_W, TITLE_H, [title],
          size=32, color=WHITE, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    if subtitle:
        _text(slide, SUB_L, SUB_T, SUB_W, 1.9, [subtitle],
              size=sub_size, color=BLACK, bold=True)
    if n is not None:
        _page_no(slide, n)


def _card(slide, x, top, width, height, num, name, bullets, accent,
          num_size=15, name_size=15, body_size=13.5):
    """模板的三列卡片：白底 + 彩色描边 + 左侧色条 + 编号/标题/要点。"""
    _rect(slide, x, top, width, height, fill=WHITE, line=accent)
    _rect(slide, x, top, 0.18, height, fill=accent, shape=MSO_SHAPE.RECTANGLE)
    _text(slide, x + 0.5, top + 0.25, width - 0.9, 0.9,
          [f"{num}.　{name}"], size=name_size, color=accent, bold=True)
    paras = []
    for i, b in enumerate(bullets):
        paras.append((b, {"size": body_size, "space_before": 7 if i else 0,
                          "spacing": 1.22}))
    _text(slide, x + 0.5, top + 1.25, width - 0.95, height - 1.5, paras,
          size=body_size, color=BLACK)


def _takeaway(slide, text, accent=RED, top=16.55):
    _rect(slide, 1.47, top, 30.91, 1.15, fill=WHITE, line=accent)
    _rect(slide, 1.47, top, 0.15, 1.15, fill=accent, shape=MSO_SHAPE.RECTANGLE)
    _text(slide, 1.98, top + 0.12, 30.0, 0.95, [text], size=14,
          color=accent, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def _table(slide, left, top, width, rows, col_w, *, head_size=12,
           body_size=11, row_h=0.72):
    """表格：表头深色白字，正文细线。``rows[0]`` 是表头。"""
    shape = slide.shapes.add_table(len(rows), len(rows[0]),
                                   Cm(left), Cm(top), Cm(width),
                                   Cm(row_h * len(rows)))
    tbl = shape.table
    for j, w in enumerate(col_w):
        tbl.columns[j].width = Cm(w)
    for i, row in enumerate(rows):
        tbl.rows[i].height = Cm(row_h)
        for j, val in enumerate(row):
            cell = tbl.cell(i, j)
            cell.margin_left = cell.margin_right = Cm(0.18)
            cell.margin_top = cell.margin_bottom = Cm(0.06)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = PRIMARY if i == 0 else (
                WHITE if i % 2 else BAND_LIGHT)
            tf = cell.text_frame
            tf.word_wrap = True
            para = tf.paragraphs[0]
            para.alignment = PP_ALIGN.LEFT
            run = para.add_run()
            run.text = str(val)
            run.font.name = FONT
            run.font.size = Pt(head_size if i == 0 else body_size)
            run.font.bold = i == 0
            run.font.color.rgb = WHITE if i == 0 else BLACK
    return tbl


def _bullets(slide, left, top, width, height, items, *, size=15, color=BLACK,
             accent=PRIMARY, gap=9):
    """左侧小方块 + 要点的列表（模板正文风格）。"""
    paras = []
    for i, it in enumerate(items):
        txt, sty = (it, {}) if isinstance(it, str) else it
        paras.append((txt, {"size": sty.get("size", size),
                            "color": sty.get("color", color),
                            "bold": sty.get("bold", False),
                            "space_before": 0 if i == 0 else sty.get("gap", gap),
                            "spacing": 1.2}))
    _text(slide, left, top, width, height, paras, size=size, color=color)


def _picture(slide, path, width_cm, top, left=None, caption=None, cap_top=None):
    pic = slide.shapes.add_picture(str(path), 0, Cm(top), width=Cm(width_cm))
    pic.left = Cm((PAGE_W - width_cm) / 2) if left is None else Cm(left)
    if caption:
        _text(slide, 1.68, cap_top if cap_top is not None else top +
              pic.height / 360000 + 0.15, 30.4, 0.7, [caption],
              size=11, color=GREY, align=PP_ALIGN.CENTER)
    return pic


# ------------------------------------------------------------------ 内容
def build() -> str:
    prs = Presentation(TPL)
    # 清空模板原有 31 页（保留母版、版式、主题）
    sld_id = prs.slides._sldIdLst
    for sid in list(sld_id):
        prs.part.drop_rel(sid.rId)
        sld_id.remove(sid)

    # ---------- 1 封面
    s = _blank(prs)
    _rect(s, 0, 0, PAGE_W, PAGE_H, fill=PRIMARY, shape=MSO_SHAPE.RECTANGLE)
    _rect(s, 0, 12.2, PAGE_W, 0.9, fill=DARK, shape=MSO_SHAPE.RECTANGLE)
    _text(s, 2.6, 5.0, 28.0, 2.2, ["配网承载力智能体"], size=52,
          color=WHITE, bold=True)
    _text(s, 2.6, 7.4, 28.0, 1.2,
          ["自然语言驱动的分布式电源接入承载力评估与依据追溯"],
          size=22, color=RGBColor(0xCF, 0xE2, 0xF0), bold=True)
    _text(s, 2.6, 9.4, 28.0, 1.9, [
        ("评估对象：10 kV 及以下配电网的馈线、台区与并网点", {"size": 15,
                                                    "color": WHITE}),
        ("底座：开源版 DeepSeek Harness（MIT）　·　"
         "引擎：OpenDSS 0.9.4 主算 ＋ pandapower 3.5.6 交叉校核",
         {"size": 15, "color": WHITE, "space_before": 6}),
        ("规则包：HC-RULES@1.0（APPROVAL-2026-002）　·　版本 V1.0（发布稿）",
         {"size": 15, "color": WHITE, "space_before": 6}),
    ], size=15, color=WHITE)
    _text(s, 2.6, 13.4, 28.0, 0.9, ["2026 年 11 月"], size=15,
          color=RGBColor(0xBF, 0xD4, 0xE4))
    _page_no(s, 1)

    # ---------- 2 目录
    s = _blank(prs)
    _head(s, "目录", n=2)
    toc = [
        ("01", "研究背景", RED),
        ("02", "总体架构", BLUE2),
        ("03", "专用智能体技术", BROWN),
        ("04", "产品界面与核心功能", GREEN),
        ("05", "配网承载力计算案例", BLUE),
        ("06", "结论与展望", DARK),
    ]
    for i, (num, name, col) in enumerate(toc):
        top = 3.55 + i * 2.42
        _text(s, 7.6, top, 2.0, 1.2, [num], size=26, color=BLUE2, bold=True)
        _text(s, 9.9, top + 0.08, 20.0, 1.2, [name], size=24, color=BLACK,
              bold=True)


    # ---------- 3 业务痛点①
    s = _blank(prs)
    _head(s, "研究背景①：口径混用、约束取不全、余量当总量",
          "承载力评估的门槛不在潮流求解，而在“同一口径、约束齐全、余量算准”；"
          "这三件事任一件出错，答复意见就会出错。", n=3)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 1, "口径混用，结论无法比较", [
        "同样叫“电压合格”：国标 0.90–1.10 pu，项目批准口径可能是 ±7%",
        "公共连接点与单个用户限值不同，混用即偏差",
        "口径不随结果固化，两次评估的数字不可比",
        "本系统：判据写进规则包并绑定审批号与生效日期，换口径即换凭证、"
        "旧结论判失效",
    ], BLUE2)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 2, "约束取不全，容量偏大", [
        "只校核电压、漏掉热稳与反送，会得出偏乐观的可接入容量",
        "不平衡与短路电流常被忽略，恰恰是并网审查看得最多的项",
        "本系统：电压、热稳、反送、不平衡、短路五类逐条校核",
        "取各约束下的最小值作为可开放容量，并记录哪一条先到限",
    ], BROWN)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 3, "余量当总量，两个口径混淆", [
        "物理可开放容量：受物理约束的最大可接入量",
        "管理可开放容量：再叠加管理裕度与已受理容量",
        "两者混用，答复意见直接出错",
        "本系统：两口径分别给数并注明系数（FD002 为 0.81）",
    ], RED)
    _takeaway(s, "①—③ 的共同点：算出了一个数，却不是可以直接拿去答复的数。", RED)

    # ---------- 4 业务痛点②
    s = _blank(prs)
    _head(s, "研究背景②：逐级靠手算、依据断层、数值不可信",
          "即使单点算对了，把台区—线路—主变—区域串起来、并把依据留全，"
          "仍然依赖人工经验。", n=4)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 4, "逐级叠加靠手算", [
        "四级对象：台区 → 10 kV 线路 → 主变 → 区域电网",
        "本级的上限未必是上级的上限，人工串接容易漏掉上级约束",
        "每换一次边界（新增并网、改造投运）就要重算一遍",
        "本版按单算例评估，逐级计算尚未实现——报告里如实标注，不代做",
    ], GREEN)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 5, "依据断层，难以复核", [
        "报告里只有一个 kW 数字，追问“卡在哪个节点、裕度多少”往往答不上来",
        "模型版本、工况、数值基准没有同时保留，后续追问会混入别的任务结果",
        "本系统：结论记录场景、断面、元件、字段与数值，并生成哈希链",
        "哈希链只证明记录未被改动，不证明结论正确——这一点必须说清",
    ], BLUE)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 6, "工程数值不能交给概率", [
        "通用大模型可能生成看似合理、实则错误的容量与电压",
        "一旦数值由模型生成，结论就失去可复核性",
        "本系统硬性规定：全部电气数值只来自引擎",
        "模型不产生、不修改任何潮流结果，只做理解与编排",
    ], DARK)
    _takeaway(s, "④—⑥ 的共同点：结论看起来完整，但没有一条可以追溯到引擎与规则。",
              GREEN)

    # ---------- 5 业务痛点③ 代价
    s = _blank(prs)
    _head(s, "研究背景③：把六类负担折算成三类代价",
          "痛点不是感觉，是可以量化的：一次评估的时间、一次返工的代价、"
          "一次错答复的风险。", n=5)
    _table(s, 1.47, 4.90, 30.9, [
        ["代价类型", "现状表现", "直接后果", "本系统的对策"],
        ["时间代价", "准备文件、核对依赖、启动求解、提取数据、整理报告",
         "求解本身不慢，慢在前后的取数与搬运",
         "统一校验 + 自动求解 + 自动成章"],
        ["返工代价", "口径散落在个人经验，换人换批次结果就变",
         "两次评估不一致，需重算并重新解释",
         "规则包带审批号，判据固定可复现"],
        ["风险代价", "约束取不全、数值无法复核、越限点定位不到",
         "答复偏乐观，接入后出现越限或过载",
         "五类约束取最小 + 独立复算 + 定位到相位"],
    ], [4.4, 10.6, 8.3, 7.6], row_h=2.15, head_size=13, body_size=12)
    _takeaway(s, "三类代价合起来，就是“不敢用、用了还要再核一遍”。", RED, top=14.5)
    _text(s, 1.68, 16.2, 30.4, 1.2,
          ["注：不给“节省 X%”这类行业数字。本系统能证明的是“环节被自动化了”，"
           "不能证明某单位节省了多少。"],
          size=12, color=GREY)

    # ---------- 6 现有做法与差距
    s = _blank(prs)
    _head(s, "研究背景④：现有做法与差距，三种路径各有缺口",
          "导则给出方法但不给工具；商业仿真强在求解但不做分级与答复；"
          "通用大模型会说话但不保证数值。", n=6)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 1, "评估导则 / 人工测算", [
        "优点：口径权威、流程合规",
        "缺口：靠人工取数与试算，逐级叠加与复核全靠经验",
        "缺口：结论以文档形式留存，难以逐项回链",
    ], BLUE2)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 2, "商业仿真软件", [
        "优点：潮流与短路计算成熟、精度可信",
        "缺口：只到“算潮流”，不做到“判红黄绿、出可开放容量”",
        "缺口：批量场景、验收判据、证据链需要另行搭建",
    ], BROWN)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 3, "通用大模型直接问答", [
        "优点：交互自然、上手快",
        "缺口：数值可能编造，且无法保证同一问题同一答案",
        "缺口：不能作为并网答复的依据",
    ], RED)
    _takeaway(s, "本项目的定位：用导则口径 + 商业级求解 + 智能体编排，"
                 "把三者接成一条可复核的链路。", PRIMARY, top=15.5)

    # ---------- 7 平台定位
    s = _blank(prs)
    _head(s, "总体架构①：把自然语言任务接到专业计算上",
          "底座是开源版 DeepSeek Harness，专业核心是 Python 引擎；"
          "两者以单行 JSON 协议通信，职责边界清晰。", n=7)
    _rect(s, 1.47, 5.6, 9.6, 6.2, fill=BAND_LIGHT, line=BLUE)
    _text(s, 1.9, 5.9, 8.8, 5.6, [
        ("大模型（Harness 会话）", {"size": 15, "bold": True, "color": BLUE2}),
        ("理解需求、拆解任务", {"size": 13, "space_before": 8}),
        ("选择技能与引擎", {"size": 13, "space_before": 6}),
        ("组织追问、解释结果", {"size": 13, "space_before": 6}),
        ("不产生任何电气数值", {"size": 13, "space_before": 6,
                         "bold": True, "color": RED}),
    ])
    _rect(s, 12.15, 5.6, 9.6, 6.2, fill=BAND_LIGHT, line=BROWN)
    _text(s, 12.58, 5.9, 8.8, 5.6, [
        ("Python 专业核心", {"size": 15, "bold": True, "color": BROWN}),
        ("九阶段评估流水线", {"size": 13, "space_before": 8}),
        ("OpenDSS 主算 + pandapower 交叉校核", {"size": 13, "space_before": 6}),
        ("规则包校核、容量搜索、独立复算", {"size": 13, "space_before": 6}),
        ("产出全部数值与产物", {"size": 13, "space_before": 6,
                       "bold": True, "color": GREEN}),
    ])
    _rect(s, 22.83, 5.6, 9.6, 6.2, fill=BAND_LIGHT, line=GREEN)
    _text(s, 23.26, 5.9, 8.8, 5.6, [
        ("报告与证据链", {"size": 15, "bold": True, "color": GREEN}),
        ("12 章报告（含验收与业务流覆盖）", {"size": 13, "space_before": 8}),
        ("验收矩阵 9 项、四态判定", {"size": 13, "space_before": 6}),
        ("哈希链与证据包", {"size": 13, "space_before": 6}),
        ("缺口如实标注“未实现”", {"size": 13, "space_before": 6,
                        "bold": True, "color": RED}),
    ])
    _text(s, 1.68, 12.4, 30.4, 1.4, [
        ("插件化接入：自研 tool-hc-agent 把 7 项专业能力注册为 Harness 工具"
         "（引擎状态、容量评估、验收指标、交叉校核、算例分析、规则问答、业务流）。",
         {"size": 13, "color": BLACK}),
    ])
    _takeaway(s, "语义理解与数值生产在环节上分离：模型负责“问对”，"
                 "引擎负责“算对”，报告负责“说清”。", PRIMARY, top=14.6)

    # ---------- 8 执行链路
    s = _blank(prs)
    _head(s, "总体架构②：九阶段评估流水线",
          "每一步都留痕，任何一步失败都不会被写成成功；缺产物时判“无数据”，"
          "不推达标。", n=8)
    stages = [
        ("S01", "冻结输入", "输入包按字节冻结并生成指纹"),
        ("S02", "校验输入", "字段、单位、缺失、越界在开算前暴露"),
        ("S03", "构建算例", "生成 Master.dss 与引用文件、场景"),
        ("S04", "基线潮流", "先报告基线越限，再谈新增容量"),
        ("S05", "约束校核", "五类约束逐条比对规则包限值"),
        ("S06", "容量搜索", "0–3000 kW 二分，精度 10 kW"),
        ("S07", "独立复算", "全新目录、全新进程重跑并比对"),
        ("S08", "生成报告", "12 章专业报告，含验收与业务流覆盖"),
        ("S09", "封存证据", "哈希链、证据包与审计事件"),
    ]
    for i, (code, name, desc) in enumerate(stages):
        row, col = divmod(i, 3)
        x = CARD_X[col]
        top = 5.0 + row * 3.95
        _rect(s, x, top, CARD_W, 3.35, fill=WHITE, line=BLUE)
        _rect(s, x, top, 0.16, 3.35, fill=BLUE, shape=MSO_SHAPE.RECTANGLE)
        _text(s, x + 0.45, top + 0.28, CARD_W - 0.9, 0.8,
              [f"{code}　{name}"], size=15, color=BLUE2, bold=True)
        _text(s, x + 0.45, top + 1.25, CARD_W - 0.95, 1.9, [desc],
              size=12.5, color=BLACK)
    _takeaway(s, "退出码区分成因：0 成功／2 输入被阻断／3 计算失败／4 验证失败／"
                 "5 未过发布门禁／6 被中断／70 看门狗超时。", BLUE, top=17.0)

    # ---------- 9 规则包与五类约束
    s = _blank(prs)
    _head(s, "总体架构③：规则包把判据变成可执行代码",
          "规则包带审批号与生效日期；口径变了，旧结论会被判定失效。", n=9)
    _table(s, 1.47, 5.0, 30.9, [
        ["约束类别", "判据（本工程规则包）", "依据", "动作"],
        ["电压", "节点电压 0.90–1.10 pu", "GB/T 12325", "超限即阻断"],
        ["热稳", "线路与配变负载率 ≤ 100%", "设备额定参数", "超限即阻断"],
        ["反送", "上级反送有功 ≤ 400 kW", "项目批准口径", "超限即阻断"],
        ["不平衡", "三相不平衡度 ≤ 2.0%", "项目批准口径", "超限即阻断"],
        ["短路", "短路电流 ≤ 20000 A", "开关遮断能力", "超限即阻断"],
    ], [4.4, 11.6, 8.4, 6.5], row_h=1.55, head_size=13, body_size=12)
    _text(s, 1.68, 13.4, 30.4, 2.4, [
        ("规则包 HC-RULES@1.0 的关键字段：审批引用 APPROVAL-2026-002、"
         "生效日期、每条约束的 standard_ref 与 failure_action。",
         {"size": 13}),
        ("搜索参数同样固化在规则包：区间 0–3000 kW、精度 10 kW、最多 25 次迭代、"
         "管理系数 0.9、裕度 0.1、分级阈值（红 ≤ 0 kW，黄 ≤ 0.15）。",
         {"size": 13, "space_before": 8}),
    ])
    _takeaway(s, "判据不满足时不出正式结论：宁可判“无数据”，也不给一个"
                 "看起来达标的数。", RED, top=16.2)

    # ---------- 10 关键技术① 数值只来自引擎
    s = _blank(prs)
    _head(s, "专用智能体技术①：数值只来自引擎，双引擎互为校核",
          "OpenDSS 以三相不平衡方式主算，pandapower 以正序平衡方式复核；"
          "校核只比量级与极值，不参与容量判定。", n=10)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 1, "主算：OpenDSS", [
        "三相不平衡潮流，保留真实相别与接线",
        "版本：OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5",
        "承担全部容量与约束判定的数值来源",
        "取最不利相作为节点电压结论",
    ], BLUE)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 2, "校核：pandapower", [
        "正序平衡潮流，独立实现路径",
        "版本：pandapower 3.5.6（独立安装，不污染主环境）",
        "容差：电压 0.03 pu、网损 10%",
        "FD002 两个断面：电压差 0.001461 / 0.005540 pu，网损差 "
        "0.000179 / 0.219186 kW，皆 match",
    ], BROWN)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 3, "边界：模型不产数", [
        "所有电气数值由引擎产生，模型只做理解与编排",
        "模型若试图给数值，答案为“请以产物为准”",
        "产物缺失时判“无数据”，不给近似值",
        "哈希链证明记录未改，不证明结论正确",
    ], RED)
    _takeaway(s, "“两个引擎都说接近”比“一个引擎说没问题”更接近可信。", BROWN)

    # ---------- 11 关键技术② 容量搜索
    s = _blank(prs)
    _head(s, "专用智能体技术②：容量搜索与绑定约束定位",
          "承载力不是一个“能不能”的判断，而是一条“还能加多少、被谁卡住”的曲线。",
          n=11)
    _bullets(s, 1.68, 5.2, 14.6, 6.0, [
        ("先括号、再二分：从 0 kW 起，找到可行/不可行的包夹区间", {"bold": True}),
        ("精度 10 kW，最多 25 次迭代；FD002 实测 11 次收敛", {}),
        ("每个候选容量都跑完整潮流与五类约束，不做线性外推", {}),
        ("取各约束下的最小可行值，同时记录是哪一条先到限", {"bold": True}),
        ("绑定约束定位到元件 + 相位 + 字段 + 裕度", {}),
        ("结果同时给物理口径与管理口径，并注明换算系数", {}),
    ], size=14.5, gap=11)
    _rect(s, 17.2, 5.2, 15.1, 10.4, fill=BAND_LIGHT, line=BLUE)
    _text(s, 17.7, 5.5, 14.2, 9.8, [
        ("FD002 的搜索结论", {"size": 16, "bold": True, "color": BLUE2}),
        ("物理可开放容量 333.984375 kW", {"size": 14, "space_before": 12,
                               "bold": True, "color": GREEN}),
        ("管理可开放容量 270.52734375 kW（×0.81）", {"size": 14, "space_before": 8}),
        ("收敛区间 333.984375 / 339.84375 kW", {"size": 13, "space_before": 8}),
        ("迭代 11 次，精度 10 kW", {"size": 13, "space_before": 6}),
        ("绑定约束：电压越限", {"size": 14, "space_before": 12, "bold": True,
                       "color": RED}),
        ("位置 bus-lv-002 的 C 相：1.1008292527209553 pu", {"size": 13,
                                                "space_before": 6}),
        ("上限 1.1 pu，裕度 −0.000829 pu", {"size": 13, "space_before": 6}),
        ("→ 整改方向是调压，不是增容", {"size": 13, "space_before": 10,
                          "bold": True, "color": RED}),
    ])
    _takeaway(s, "同一个容量数字，配上“卡在哪一相、还差多少”，"
                 "才能直接支撑整改决策。", BLUE, top=16.3)

    # ---------- 12 关键技术③ 独立复算与验收
    s = _blank(prs)
    _head(s, "专用智能体技术③：独立复算与九项验收矩阵",
          "复算换目录、换进程；验收按四态判定，“无数据”既不是达标也不是未达标。",
          n=12)
    _table(s, 1.47, 5.0, 30.9, [
        ["验收指标", "门槛", "FD002 实测", "判定"],
        ["评估覆盖（约束类数）", "≥ 5 类", "5 类", "达标"],
        ["红黄绿一致率", "≥ 90%", "需专家标签", "不适用"],
        ["可开放容量偏差", "≤ 3%", "需二次独立实现", "不适用"],
        ["跨版本复现", "完全一致", "需新旧规则包两版", "不适用"],
        ["独立复算通过率", "100%", "8/8 项一致", "达标"],
        ["原始数据字节保护", "100%", "输入指纹一致", "达标"],
        ["可追溯率", "100%", "审计链完整", "达标"],
        ["适配引擎数", "≥ 2 种", "2 种（OpenDSS / pandapower）", "达标"],
        ["单台区耗时降幅", "≥ 50%", "缺人工基准", "无数据"],
    ], [11.0, 5.4, 10.2, 4.3], row_h=1.12, head_size=12.5, body_size=11.5)
    _takeaway(s, "FD002 的结果：5 项达标、3 项不适用、1 项无数据——"
                 "不把“不适用”说成“达标”。", RED, top=16.6)

    # ---------- 13 关键技术④ 证据链
    s = _blank(prs)
    _head(s, "专用智能体技术④：证据链让每个结论可回链",
          "报告里的每个数字都能指回某一次计算、某一份产物、某一条规则。", n=13)
    _bullets(s, 1.68, 5.2, 15.2, 8.0, [
        ("12 章专业报告：任务与口径、算例来源、求解配置、潮流状态、母线/支线结果、"
         "功率平衡、独立验证、验收指标、证据索引、结果解释、限制建议、业务流覆盖",
         {"bold": True}),
        ("证据索引：每份产物记录哈希，结论引用证据 ID", {}),
        ("审计链：事件按序哈希串联，可验证记录未被改动", {}),
        ("口径绑定凭证：规则包 + 审批号 + 生效日期，口径变化即判结论失效", {}),
        ("业务流覆盖：八段闭环中“未实现”的环节在报告里明确标注", {}),
    ], size=14, gap=12)
    _rect(s, 17.9, 5.2, 14.4, 9.4, fill=BAND_LIGHT, line=GREEN)
    _text(s, 18.4, 5.5, 13.5, 8.8, [
        ("一份报告的产物构成", {"size": 15, "bold": True, "color": GREEN}),
        ("report.docx — 12 章正文（含验收）", {"size": 13, "space_before": 10}),
        ("run_summary.json — 容量与等级", {"size": 13, "space_before": 6}),
        ("constraint_results.csv — 逐条约束明细", {"size": 13, "space_before": 6}),
        ("bus_results.csv / element_results.csv", {"size": 13, "space_before": 6}),
        ("search_trace.csv — 二分轨迹", {"size": 13, "space_before": 6}),
        ("verification/verification.json — 复算结论", {"size": 13, "space_before": 6}),
        ("evidence/ — 证据包与哈希清单", {"size": 13, "space_before": 6}),
        ("audit/events.jsonl — 审计事件", {"size": 13, "space_before": 6}),
    ])
    _takeaway(s, "可追溯不是“有个日志”，而是“每条结论都能指回它的来源”。",
              GREEN, top=15.4)

    # ---------- 14 核心功能总览
    s = _blank(prs)
    _head(s, "产品界面与核心功能①：一句话需求到可追溯报告", n=14)
    _table(s, 1.47, 5.0, 30.9, [
        ["功能模块", "能力说明"],
        ["自然语言任务接入", "对话提出“这条馈线还能接多少光伏、卡在哪”，拆解为可执行任务"],
        ["多来源输入接入", "输入包、OpenDSS 算例目录、压缩包拖入，三种入口统一校验"],
        ["九阶段评估流水线", "冻结→校验→建案→基线→校核→搜索→复算→报告→封存"],
        ["容量搜索", "0–3000 kW、精度 10 kW、最多 25 次；FD002 实测 11 次"],
        ["五类约束校核", "电压、热稳、反送、不平衡、短路，逐条比对规则包"],
        ["红黄绿分级", "给出等级与主导限制因素，定位到元件与相位"],
        ["独立复算", "全新目录、全新进程重跑并逐项比对"],
        ["验收矩阵", "9 项指标、四种判定；无数据不等于通过"],
        ["报告与证据链", "12 章报告 + 哈希链 + 证据包"],
    ], [8.4, 22.5], row_h=1.18, head_size=13, body_size=12)
    _takeaway(s, "功能不是并列的九个按钮，而是一条串起来的链路："
                 "少了任何一环，结论都不能发布。", PRIMARY, top=17.0)

    # ---------- 15 产品界面①
    s = _blank(prs)
    _head(s, "产品界面与核心功能②：问答式任务控制与结果解释", n=15)
    _picture(s, SHOTS + r"\35_title_new.png", 17.9, 4.45,
             caption="问答界面：提出需求后给出结论、关键结果、依据与报告路径",
             cap_top=16.15)
    _text(s, 1.68, 16.95, 30.4, 1.5,
          ["界面自带“可以这样问”的引导与算例选择，降低首次使用门槛；"
           "回答中标注出处（产物文件），便于回查。"], size=12, color=GREY)

    # ---------- 16 产品界面②
    s = _blank(prs)
    _head(s, "产品界面与核心功能③：验收与指标，把“可信”显示出来", n=16)
    _picture(s, SHOTS + r"\33_results_new.png", 17.9, 4.45,
             caption="验收与指标页：九项指标口径说明、近期运行总体状况与审计链状态",
             cap_top=16.15)
    _text(s, 1.68, 16.95, 30.4, 1.5,
          ["“未知约束项”与“无数据”会单独统计；成功率低时如实显示，"
           "不用绿色掩盖失败。"], size=12, color=GREY)

    # ---------- 17 算例 FD002
    s = _blank(prs)
    _head(s, "计算案例①：FD002，容量被末端 C 相电压卡住", n=17)
    _table(s, 1.47, 5.0, 15.2, [
        ["项", "数值"],
        ["对象", "5 母线 / 3 线路 / 1 配变 / 2 负荷 / 2 光伏"],
        ["基线", "0 项越限、0 项无数据"],
        ["物理可开放容量", "333.984375 kW"],
        ["管理可开放容量", "270.52734375 kW（×0.81）"],
        ["迭代", "11 次，精度 10 kW"],
        ["等级", "绿"],
    ], [6.2, 9.0], row_h=1.05, head_size=12, body_size=11.5)
    _rect(s, 17.4, 5.0, 14.9, 10.2, fill=BAND_LIGHT, line=RED)
    _text(s, 17.9, 5.3, 14.0, 9.6, [
        ("绑定约束：电压越限", {"size": 16, "bold": True, "color": RED}),
        ("节点 bus-lv-002 的 C 相", {"size": 14, "space_before": 10}),
        ("接入后 1.1008292527209553 pu", {"size": 13.5, "space_before": 6}),
        ("上限 1.1 pu", {"size": 13.5, "space_before": 6}),
        ("裕度 −0.000829 pu", {"size": 13.5, "space_before": 6, "bold": True}),
        ("说明：容量不是被线路或配变卡住的，而是被末端电压卡住；"
         "整改方向是调压（无功补偿 / 有载调压），不是增容。",
         {"size": 12.5, "space_before": 14}),
    ])
    _takeaway(s, "“能接 334 kW”只是一半；“被 C 相电压卡住”才决定下一步做什么。",
              RED, top=15.8)

    # ---------- 18 算例 FD001
    s = _blank(prs)
    _head(s, "计算案例②：FD001，零也是需要证据的结论", n=18)
    _bullets(s, 1.68, 5.2, 15.0, 7.5, [
        ("基线本身就有 14 项越限、1 项无数据，等级不为绿", {"bold": True}),
        ("受热稳约束限制：元件 Line.line_ln_lv_001 负载率达 136.1645%", {}),
        ("因此可开放容量为 0.0 kW —— 不是“很小”，而是“没有余量”", {"bold": True}),
        ("先报告基线越限，再谈新增容量；顺序颠倒会掩盖既有问题", {}),
        ("这类结论同样需要证据：越限清单、元件、负载率、规则依据", {}),
    ], size=14.5, gap=12)
    _rect(s, 17.6, 5.2, 14.7, 9.0, fill=BAND_LIGHT, line=BROWN)
    _text(s, 18.1, 5.5, 13.8, 8.4, [
        ("为什么把“0”单独讲一页", {"size": 15, "bold": True, "color": BROWN}),
        ("工程上最容易被忽略的就是“接不了”的结论：", {"size": 13,
                                          "space_before": 10}),
        ("· 容易被写成“需进一步研究”而搁置", {"size": 13, "space_before": 6}),
        ("· 容易被误答成“可以先接一部分”", {"size": 13, "space_before": 6}),
        ("· 容易被当成数据问题而不深究", {"size": 13, "space_before": 6}),
        ("本系统的处理：给出越限元件、负载率与规则依据，"
         "并明确“不可开放”", {"size": 13, "space_before": 12, "bold": True}),
    ])
    _takeaway(s, "能说清“为什么接不了”的系统，才值得用它来说“能接多少”。",
              BROWN, top=15.6)

    # ---------- 19 算例 FD016
    s = _blank(prs)
    _head(s, "计算案例③：FD016，多场景多断面的一次批量评估", n=19)
    _table(s, 1.47, 5.2, 30.9, [
        ["项", "结果", "项", "结果"],
        ["规模", "16 节点合成算例", "物理容量", "515.625 kW"],
        ["场景 × 断面", "3 场景 × 12 时间片", "管理容量", "417.65625 kW"],
        ["约束条目", "420 条全部通过", "绑定约束", "电压越限（bus-lv-002b 的 B 相）"],
        ["无数据项", "0", "单次耗时", "约 5.6 秒"],
    ], [4.6, 10.4, 5.0, 10.9], row_h=1.85, head_size=12.5, body_size=12)
    _text(s, 1.68, 13.6, 30.4, 2.2, [
        ("多场景不是“多算几次”，而是取最不利断面作为结论依据："
         "同一容量在某些断面可行、在另一些不可行时，以最不利断面为准。",
         {"size": 13.5}),
        ("这正是人工测算最费时、也最容易偷懒的一步——"
         "只算一个“典型日”往往给出偏乐观的结论。", {"size": 13.5, "space_before": 8}),
    ])
    _takeaway(s, "批量与最不利断面，是承载力评估里最该自动化、也最容易被跳过的一步。",
              BLUE, top=16.4)

    # ---------- 20 效率提升① 环节对比
    s = _blank(prs)
    _head(s, "结论与展望①：效率提升——逐环节看哪些动作被程序接走",
          "同样的七个环节，对比“人工做法”与“智能体做法”，"
          "差别不在算得快，而在“不重复、不遗漏、可复核”。", n=20)
    _table(s, 1.47, 5.1, 30.9, [
        ["环节", "人工做法", "智能体做法"],
        ["数据准备", "从营销、PMS、调度、用采分别导出，手工对齐台账",
         "输入包统一校验（字段、单位、缺失、越界），问题在开算前暴露"],
        ["口径确认", "翻标准、问同事，口径散落在个人经验里",
         "规则包带审批号与生效日期，换口径即换凭证"],
        ["约束建模", "只做电压或只做载流，凭经验取舍",
         "五类约束全部编制为可执行规则，取最小值"],
        ["容量搜索", "手工试算几个点，再外推",
         "二分搜索，精度 10 kW，单算例约 11 次迭代"],
        ["约束定位", "知道越限，不知道卡在哪一相、裕度多少",
         "定位到元件、相位、字段，给出裕度数值"],
        ["复算与验收", "人工复核，漏检与返工难以避免",
         "全新目录 + 全新进程自动复算，9 项验收四态判定"],
        ["报告与追溯", "整理 Word，依据与数字对不上",
         "12 章报告 + 证据索引 + 哈希链，结论可逐项回链"],
    ], [4.6, 12.2, 14.1], row_h=1.42, head_size=12.5, body_size=11.5)
    _takeaway(s, "七个环节里，有六个可以完全交给程序；"
                 "剩下一个是“判据能不能用”，仍由人决定。", PRIMARY, top=16.9)

    # ---------- 21 效率提升② 四个维度
    s = _blank(prs)
    _head(s, "结论与展望②：效率提升的四个维度",
          "不喊“提升 X%”——先把被消除的动作说清楚，再说它省在哪。", n=21)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 1, "场景处理效率", [
        "浪费：一次只算一个场景，换断面重来一遍",
        "对策：多场景多断面一次批量（FD016：3 × 12）",
        "价值：取最不利断面成为默认动作，而不是可选项",
    ], BLUE)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 2, "工具操作效率", [
        "浪费：在表格、脚本、仿真软件之间来回搬数",
        "对策：输入统一接入、求解与提取自动完成",
        "价值：人只需确认口径与判据，不做搬运",
    ], BROWN)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 3, "经验复用效率", [
        "浪费：口径、边界、整改思路依赖个人",
        "对策：规则包、技能卡 17 张、整改建议库固化",
        "价值：同一问题给出同一套判据，换人不换口径",
    ], GREEN)
    _card(s, CARD_X[0], CARD_T + 4.55, CARD_W, CARD_H - 1.6, 4, "结果复核效率", [
        "浪费：逐项人工核对，“看起来合理”的数字被放过",
        "对策：独立复算 + 9 项验收矩阵常开",
        "价值：缺产物记“无数据”，不给未经核验的结论",
    ], RED)
    _rect(s, CARD_X[1], CARD_T + 4.55, CARD_W * 2 + 2.0, CARD_H - 1.6,
          fill=BAND_LIGHT, line=PRIMARY)
    _text(s, CARD_X[1] + 0.55, CARD_T + 4.85, CARD_W * 2 + 0.9,
          CARD_H - 2.2, [
        ("为什么不用统一的百分比来讲效率", {"size": 15, "bold": True,
                              "color": PRIMARY}),
        ("因为不同单位的数据基础、场景数量、复核要求差别很大，"
         "任何“提升 60%”都会变成不可核对的宣传数字。", {"size": 13,
                                     "space_before": 10}),
        ("本系统能给出的可靠说法是：七个环节里六个可自动化、"
         "单算例评估约 6 秒、验收由 9 项固定指标判定。", {"size": 13,
                                      "space_before": 8}),
    ])

    # ---------- 22 效率提升③ 分工
    s = _blank(prs)
    _head(s, "结论与展望③：效率来自分工，不是让模型替人判断",
          "把可固化的交给程序，把要负责的留给人。", n=22)
    _table(s, 1.47, 5.2, 30.9, [
        ["事项", "由谁负责", "依据"],
        ["数据提取与校验", "程序", "输入包规则、字段与单位校验"],
        ["潮流与短路计算", "引擎（OpenDSS / pandapower）", "引擎版本与配置记录"],
        ["容量搜索与约束定位", "程序", "规则包 + 二分精度"],
        ["复算与验收判定", "程序", "验收矩阵 9 项、四态规则"],
        ["判据能否采用", "专业人员", "标准、项目批准文件、审批号"],
        ["结论能否对外说", "专业人员", "单位流程与责任划分"],
    ], [8.6, 10.6, 11.7], row_h=1.55, head_size=13, body_size=12)
    _takeaway(s, "角色变化：从逐次操作工具，转为维护口径与验收标准、"
                 "处理异常、审核关键结论。", GREEN, top=15.9)

    # ---------- 23 应用边界与未实现
    s = _blank(prs)
    _head(s, "结论与展望④：应用边界——已实现、部分实现、未实现",
          "业务链八段闭环的覆盖情况：已实现 1 段、部分实现 6 段、未实现 1 段。",
          n=23)
    _table(s, 1.47, 5.1, 30.9, [
        ["业务环节", "状态", "说明"],
        ["数据收集与治理", "部分实现", "不直连营销/PMS/D5000/用采/GIS，靠导出后整理"],
        ["约束建模", "部分实现", "5 类已实现；谐波、电压波动、保护适应性未纳入"],
        ["承载力计算", "部分实现", "单算例已实现；台区→线路→主变→区域逐级未实现"],
        ["红黄绿分级", "已实现", "按阈值输出三级，定位主导限制因素"],
        ["结果发布与公示", "未实现", "只产出内部报告，不生成对外公示口径"],
        ["业务应用（接入）", "部分实现", "能答“接不接得下”；无工单与答复意见书"],
        ["提升措施与改造", "部分实现", "有建议库；无改造后的定量校核"],
        ["动态更新与闭环", "部分实现", "可重跑；无变更检测与增量触发"],
    ], [6.6, 4.4, 19.9], row_h=1.24, head_size=12.5, body_size=11.5)
    _text(s, 1.68, 16.3, 30.4, 0.9,
          ["说明：未实现的环节在报告与问答中均标注“未实现”，系统不代做，"
           "也不在宣传中模糊处理。"], size=12, color=RED, bold=True)

    # ---------- 24 结论
    s = _blank(prs)
    _head(s, "结论与展望⑤：四个维度回答本项目做到了什么", n=24)
    _card(s, CARD_X[0], CARD_T, CARD_W, CARD_H, 1, "算得对", [
        "数值全部来自 OpenDSS，双引擎交叉校核",
        "五类约束逐条校核，取最小值",
        "取最不利相、最不利断面作为结论",
    ], BLUE)
    _card(s, CARD_X[1], CARD_T, CARD_W, CARD_H, 2, "验得住", [
        "全新目录、全新进程独立复算，8/8 一致",
        "9 项验收矩阵四态判定，缺产物记无数据",
        "20 个自检套件，失败如实报告",
    ], BROWN)
    _card(s, CARD_X[2], CARD_T, CARD_W, CARD_H, 3, "说得清", [
        "12 章报告 + 业务流覆盖 + 证据索引",
        "绑定约束定位到元件、相位、裕度",
        "哈希链证明记录未改（不证明结论正确）",
    ], GREEN)
    _rect(s, 1.47, CARD_T + 4.9, 30.9, 3.6, fill=BAND_LIGHT, line=PRIMARY)
    _text(s, 2.0, CARD_T + 5.15, 30.0, 3.1, [
        ("用得上：底座是开源版 DeepSeek Harness，插件 tool-hc-agent 把 7 项能力"
         "注册为工具；在从源码构建的 Harness 上已跑通真实会话调用。",
         {"size": 13.5}),
        ("下一步按优先级：逐级承载力与上级约束串接 → 改造措施定量校核 → "
         "发布口径与对外表格（需省公司规范）→ 变更检测与版本对比。",
         {"size": 13.5, "space_before": 10}),
    ])

    # ---------- 25 结尾
    s = _blank(prs)
    _rect(s, 0, 0, PAGE_W, PAGE_H, fill=PRIMARY, shape=MSO_SHAPE.RECTANGLE)
    _text(s, 2.6, 6.4, 28.6, 2.2, ["配网承载力智能体"], size=44, color=WHITE,
          bold=True)
    _text(s, 2.6, 8.9, 28.6, 3.4, [
        ("数值由引擎产生，复算由独立进程执行，依据随结果一同保留；", {"size": 18,
                                               "color": WHITE}),
        ("判据不满足时不出正式结论，未实现的环节如实标注。", {"size": 18,
                                        "color": WHITE, "space_before": 10}),
        ("工程判据的采用与结论的最终判断，由专业人员确认。", {"size": 18,
                                        "color": RGBColor(0xCF, 0xE2, 0xF0),
                                        "space_before": 16, "bold": True}),
    ], size=18, color=WHITE)
    _page_no(s, 25)

    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"已生成：{path}")
    print(f"页数：{len(Presentation(path).slides)}")
