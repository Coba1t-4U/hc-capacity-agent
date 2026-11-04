# -*- coding: utf-8 -*-
"""生成《配网承载力智能体 · 演示讲稿（6–7 分钟）》docx。

结构：
  一、展示设计（场景 / 时间轴 / 屏幕分工 / 兜底）
  二、讲稿（含【操作】【画面】标记与时间码）
  三、备答（评审可能追问的 6 个问题）
  四、截图索引（每张图对应哪一段）

截图全部取自智能体本人运行时的真实界面（tools/capture_ui_pages.py 生成）。
"""

from __future__ import annotations

import re

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

SHOTS = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace"
             r"\hc_agent_project\tests\_desktop_shots")
OUT = Path(r"C:\Users\Chen\Desktop\配网承载力\配网承载力智能体_演示讲稿.docx")

FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)



#: 讲稿的口播删减规则（整句删除，只删展开说明，不动数字）。
#: 目的：把讲稿控在 8 分钟左右。改这里就能调整口播长度。
CUTS = [
    r"，也能按页面看细节。",
    r"模型可以说“被电压卡住”，但.*?这个数必须出自引擎。",
    r"，左下是知识库与配置",
    r"口径不跟结果固化，三个月后就说不清用的哪一版。",
    r"公共连接点与单个用户限值也不同。",
    r"过去的常见情况是算完、报告写完才发现单位错了；",
    r"系统会区分“算了一次潮流”和“出了承载力结论”。",
    r"但真正有用的是下一句。",
    r"只给一个容量数字，这个判断做不出来。",
    r"DL/T.*?对系统级与设备级承载力的区分.*?来源。",
    r"过去这一步最费时，也最容易被简化成“只算一个典型日”。",
    r"所以效率提升的本质是分工变化：人从逐次操作工具，转向维护口径、处理异常、审核结论。",
    r"最后说清能做什么、不能做什么。",
]


def _cut(text: str) -> str:
    """按 CUTS 删句。返回值已是最终口播文本。"""
    for pat in CUTS:
        text = re.sub(pat, "", text)
    return text


def _p(doc, text, *, size=10.5, bold=False, color=None, space=6,
       align=WD_ALIGN_PARAGRAPH.LEFT, indent=0):
    par = doc.add_paragraph()
    par.alignment = align
    par.paragraph_format.space_after = Pt(space)
    par.paragraph_format.line_spacing = 1.45
    if indent:
        par.paragraph_format.left_indent = Cm(indent)
    run = par.add_run(_cut(text))
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    return par


def _rich(doc, parts, *, size=10.5, space=6, indent=0):
    """一段里混排（如【操作】用蓝色）。"""
    par = doc.add_paragraph()
    par.paragraph_format.space_after = Pt(space)
    par.paragraph_format.line_spacing = 1.45
    if indent:
        par.paragraph_format.left_indent = Cm(indent)
    for text, bold, color in parts:
        r = par.add_run(_cut(text))
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.bold = bold
        if color is not None:
            r.font.color.rgb = color
    return par


def _h(doc, text, level=1):
    hd = doc.add_heading(text, level=level)
    for r in hd.runs:
        r.font.name = FONT
        r.font.color.rgb = BLUE if level == 1 else RGBColor(0x1A, 0x1A, 0x1A)


def _table(doc, rows, widths, header=True, size=9.5):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.width = Cm(widths[j])
            par = cell.paragraphs[0]
            par.paragraph_format.space_after = Pt(2)
            r = par.add_run(str(val))
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = header and i == 0
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def _shot(doc, name, caption, width=14.6):
    path = SHOTS / name
    if not path.is_file():
        _p(doc, f"[缺图] {name}", color=RED)
        return
    doc.add_picture(str(path), width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _p(doc, caption, size=9, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=10)


def build() -> Path:
    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.4)

    _p(doc, "配网承载力智能体", size=20, bold=True, color=BLUE,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=2)
    _p(doc, "演示讲稿与操作脚本（6–7 分钟）", size=13,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=2)
    _p(doc, "V1.1.0　·　2026 年 11 月", size=9, color=GREY,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=14)

    # ================= 一、展示设计
    _h(doc, "一、展示设计（先说清怎么演）")

    _p(doc, "1. 演示定位：一次「从提问到可追溯结论」的完整走通。", bold=True)
    _p(doc, "不讲功能清单，只演一条主线：给一条 10 kV 馈线，问它还能接多少分布式光伏，"
            "得到容量、被哪条约束卡住、凭什么可信、报告在哪。全程只在智能体一个窗口内完成，"
            "不切浏览器、不切命令行，避免观众注意力被打断。")

    _p(doc, "2. 屏幕分工与操作原则", bold=True)
    doc_paras = [
        "讲述人坐在操作位，左手控制翻页笔（若用 PPT），右手操作鼠标；",
        "每个功能点只做 1 次关键操作，其余靠已算好的结果展示，避免现场等计算；",
        "演示数据全部来自本机固定算例（FD002 验收算例、FD016 十六节点算例），"
        "不现场录入随机数据；",
        "每段结束用一句话落到结论上，不让画面停在「转圈」状态。",
    ]
    for it in doc_paras:
        _p(doc, "· " + it, space=3, indent=0.5)

    _p(doc, "3. 时间轴（总 6 分 40 秒，留 20 秒缓冲）", bold=True)
    _table(doc, [
        ["段落", "时长", "屏幕内容", "操作动作", "对应截图"],
        ["开场：这是个什么系统", "0:00–1:00", "智能体主界面（问答页）", "无操作，讲定位与分工", "图 1"],
        ["业务痛点", "1:00–2:00", "配置页：规则包与运行环境", "点左侧「配置」", "图 2"],
        ["功能①算例接入", "2:00–2:40", "算例分析页：算例清单与字段", "点「算例分析」", "图 3"],
        ["功能②一句话下任务", "2:40–3:30", "问答页：结论 + 依据", "输入一句话并发送", "图 1 / 图 4"],
        ["功能③执行轨迹", "3:30–4:15", "运算过程：九阶段状态与耗时", "点「运算过程」", "图 5"],
        ["功能④结果与证据", "4:15–5:45", "结果与证据：容量、绑定约束、验收", "点「结果与证据」", "图 6"],
        ["功能⑤知识支撑", "5:45–6:15", "知识库：31 篇条目与来源状态", "点「知识库」", "图 7"],
        ["成效与价值", "6:15–7:20", "回到结果页（停在验收矩阵）", "无操作", "图 6"],
        ["能力边界", "7:20–7:50", "结果页", "无操作", "图 6"],
    ], [3.6, 2.2, 6.0, 4.0, 2.8])

    _p(doc, "4. 兜底方案（现场必备）", bold=True)
    for it in [
        "计算偶发卡顿：直接切到「结果与证据」，用已跑好的运行继续讲，说明「这一次已经跑完」；",
        "窗口被遮挡：提前把智能体窗口置于最前，关闭微信/浏览器通知；",
        "投影分辨率低：把界面字号调到 110%，重点看结论与数字，不逐行念表格；",
        "被问倒：明确说「这一点我记下来，会后给答复」，不要现场编数字。",
    ]:
        _p(doc, "· " + it, space=3, indent=0.5)

    _p(doc, "5. 如果只给 6 分 40 秒，按这张表删", bold=True)
    _p(doc, "本稿正文约 1700 字，按每分钟 220 字计约 7 分 45 秒。要压到 6 分 40 秒，"
            "删下面四处共约 240 字即可，不动任何数字：")
    _table(doc, [
        ["删哪一段", "删多少", "保留什么"],
        ["第 1 段：四层分工的展开", "约 80 字", "保留“数值只来自引擎”这一条"],
        ["第 2 段：反送那条的举例", "约 60 字", "保留“五类约束取最小值”"],
        ["第 5 段：哈希链的作用说明", "约 60 字", "保留“独立复算八项比对”"],
        ["第 9 段：不做三项的逐条解释", "约 40 字", "保留“三项未实现，报告如实标注”"],
    ], [8.2, 2.6, 7.6], size=9)

    doc.add_page_break()

    # ================= 二、讲稿
    _h(doc, "二、讲稿（可直接照读，括号内是动作提示）")

    _p(doc, "【第 1 段】开场：这是个什么系统　0:00–1:00", bold=True, color=BLUE)
    _rich(doc, [("（画面：智能体主界面，停在问答页，先让观众看一眼界面，不说话）", False, GREY)])
    _p(doc, "各位领导、各位专家，上午好。我汇报的是配网承载力智能体。")
    _p(doc, "先说它干什么。一条 10 千伏馈线，末端台区报了 400 千瓦分布式光伏，"
            "要回答三件事：能不能接；接不了差在哪；接了之后电压、载流、反向潮流会不会越限。")
    _p(doc, "它是 Windows 本机桌面应用，六个页面：问答、运行总览、容量分析、运算过程、"
            "算例分析、结果与证据，左下是知识库与配置。任务可以一句话发起，也能按页面看细节。",
       indent=0.5)
    _p(doc, "内部四层分工：大模型理解需求、拆任务、条件不足时追问；引擎算数，"
            "潮流由 OpenDSS 算、pandapower 做交叉校核；报告与证据链封存结论与依据；"
            "专业人员确认口径、确认结论能不能对外说。", indent=0.5)
    _p(doc, "一条硬规则：电气数值只来自引擎，模型不产生也不修改。"
            "模型可以说“被电压卡住”，但 1.1008 这个数必须出自引擎。", indent=0.5)
    _p(doc, "底座是开源版 DeepSeek Harness，七项专业能力作为工具注册给它调度。"
            "边界：做评估、做校核、留证据，不写设备、不遥控、不代签并网许可。")

    _p(doc, "【第 2 段】业务痛点：为什么需要它　1:00–2:00", bold=True, color=BLUE)
    _rich(doc, [("（动作：点左侧「配置」，画面切到运行环境、引擎状态与规则包）", False, GREY)])
    _p(doc, "门槛不在潮流求解，而在三件事。")
    _p(doc, "第一是口径。同样说“电压合格”，国标 0.90 到 1.10 标幺，"
            "项目批准口径可能是正负 7%，公共连接点与单个用户限值也不同。"
            "口径不跟结果固化，三个月后就说不清用的哪一版。"
            "所以判据写成规则包，每条带出处、审批号、生效日期；口径一改，旧结论直接失效。", indent=0.5)
    _p(doc, "第二是约束。只算电压、不算热稳和反送，容量一定偏大。"
            "系统五类约束逐条校核、取最小值，并记下哪一条先到限、裕度剩多少。", indent=0.5)
    _p(doc, "第三是依据。过去报告只有一个容量数字，问“为什么只能接 334 千瓦”“卡在哪个节点”，"
            "答不上来；而这个答案决定整改方向——增容还是调压，是两笔完全不同的投资。", indent=0.5)
    _p(doc, "还有一层：大模型会编数字。结论一旦由模型生成就失去可复核性，"
            "所以“数值由引擎产生、结论可回链”是硬约束。")
    _shot(doc, "60_config.png",
          "图 2　配置页：引擎状态、模型版本、规则包版本与运行环境一目了然")

    _p(doc, "【第 3 段】功能①：算例怎么进来　2:00–2:40", bold=True, color=BLUE)
    _rich(doc, [("（动作：点左侧「算例分析」；画面显示算例清单）", False, GREY)])
    _p(doc, "第一步算例接入。三种入口：结构化输入包、OpenDSS 原生算例目录、压缩包拖入；"
            "这里点一下桌面算例就完成接入。")
    _p(doc, "接入同时做字段与单位校验：缺字段、单位不一致、越界、拓扑不连通，都在开算前暴露。"
            "过去的常见情况是算完、报告写完才发现单位错了；现在的原则是问题必须在开算前暴露。",
       indent=0.5)
    _shot(doc, "65_dss.png",
          "图 3　算例分析：算例清单与字段校验，开算前把关")

    _p(doc, "【第 4 段】功能②：一句话下任务　2:40–3:30", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「问答」，输入「算一下 FD002 还能接多少光伏，并给我计算报告」，回车）",
                 False, GREY)])
    _p(doc, "第二步下任务，最自然的方式就是一句话。")
    _p(doc, "系统不会带默认假设直接出数，而是先确认：算例哪个、规则包哪一版、口径按哪个来；"
            "条件不足先追问。", indent=0.5)
    _p(doc, "回答分三层：结论——接不接得下、容量多少；关键结果——分布式出力、总负荷、反向潮流、"
            "最高最低电压、过载元件及负载率、网损；依据——用的哪版规则包、数字出自哪份产物。",
       indent=0.5)
    _p(doc, "注意那行“重要说明”：这是原生算例分析，只做潮流与约束校核，不产出可开放容量。"
            "系统会区分“算了一次潮流”和“出了承载力结论”。")
    _shot(doc, "59_chat.png",
          "图 1 / 图 4　问答页：结论、关键结果、依据与出处分层呈现")

    _p(doc, "【第 5 段】功能③：执行轨迹看得见　3:30–4:15", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「运算过程」，画面显示九阶段列表与各阶段耗时）", False, GREY)])
    _p(doc, "第三步看执行轨迹，这一页给复核的人看。九阶段：冻结输入、校验输入包、构建算例、"
            "基线潮流、约束校核、容量搜索、独立复算、生成报告、封存证据，每步有状态和耗时。")
    _p(doc, "两点设计。一是先跑基线再谈新增容量：基线本身越限时先摆问题，"
            "不给“还能接多少”的乐观数字。二是搜索之后独立复算：换全新目录、全新进程重跑，"
            "比对容量、绑定约束、母线电压、网损等八项，任一项不一致即判定验证失败、不得发布。",
       indent=0.5)
    _shot(doc, "66_workbench.png",
          "图 5　运算过程：九阶段状态与耗时，含独立复算与证据封存")

    _p(doc, "【第 6 段】功能④：结果与证据（重点）　4:15–5:45", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「结果与证据」，停在验收与指标页，先不急着讲右边）", False, GREY)])
    _p(doc, "第四步也是最核心的一步，看结果。以验收算例 FD002 为例："
            "5 个母线、3 条线路、1 台配变、2 个负荷、2 组光伏。")
    _p(doc, "物理可开放容量 333.98 千瓦，按管理裕度折算后 270.53 千瓦，等级为绿。"
            "但真正有用的是下一句。", indent=0.5)
    _p(doc, "绑定约束是电压越限，位置在 bus-lv-002 的 C 相，接入后 1.1008 标幺、上限 1.1、"
            "裕度负 0.0008。意思是：容量不是被线路卡住的，也不是被配变卡住的，"
            "而是被末端 C 相电压卡住的。整改方向应是调压——无功补偿或有载调压配变，"
            "而不是换更粗的导线。只给一个容量数字，这个判断做不出来。", indent=0.5)
    _p(doc, "右边是验收矩阵，九项指标、四种判定：达标、未达标、无数据、不适用。"
            "特别注意“无数据”：它既不是通过也不是未达标，而是这次没有可判定产物。"
            "系统不允许把无数据写成达标。", indent=0.5)
    _p(doc, "这个算例：五项达标、三项不适用、一项无数据。")
    _shot(doc, "62_results.png",
          "图 6　结果与证据：容量结论、绑定约束定位、九项验收矩阵")

    _p(doc, "【第 7 段】功能⑤：知识支撑　5:45–6:15", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「知识库」，展示条目与来源状态）", False, GREY)])
    _p(doc, "第五步看知识支撑。判据从哪来要说清。内置 31 篇条目，覆盖承载力定义、配网拓扑、"
            "新能源涉网要求、电压调节与储能。")
    _p(doc, "每条标来源，分已核对、待核：已核对可作判据引用，待核只作提示、不进结论。"
            "DL/T 2041 对系统级与设备级承载力的区分，就是本系统口径的来源。")
    _shot(doc, "61_kb.png",
          "图 7　知识库：条目、来源与核对状态，区分已核对与待核")

    _p(doc, "【第 8 段】成效与价值　6:15–7:20", bold=True, color=BLUE)
    _p(doc, "最后说成效。不给“提升百分之多少”，各单位数据基础差别太大，经不起核对。说三个事实。")
    _p(doc, "一，环节可自动化。一次评估七个环节，六个可以完全交给程序，"
            "剩下一个是“判据能不能用”，仍由人决定。", indent=0.5)
    _p(doc, "二，算得动。十六节点、三个场景、十二个时间片、四百二十条约束，单次评估三到四秒，"
            "并自动取最不利断面。过去这一步最费时，也最容易被简化成“只算一个典型日”。", indent=0.5)
    _p(doc, "三，可复核。九项验收固定判定、双引擎交叉校核，电压极值差千分之五标幺以内、"
            "网损差零点二千瓦以内，任一项不一致结论不发布。", indent=0.5)
    _p(doc, "所以效率提升的本质是分工变化：人从逐次操作工具，转向维护口径、处理异常、审核结论。")

    _p(doc, "【第 9 段】能力边界　7:20–7:50", bold=True, color=BLUE)
    _p(doc, "最后说清能做什么、不能做什么。")
    _p(doc, "能做的：读算例、跑潮流、扫容量、定约束、出验收、留证据，"
            "并把结论、依据、产物串成可回链的链子。", indent=0.5)
    _p(doc, "不做的三件：结果发布与公示不做，那是对外口径，需按省公司规范另行生成；"
            "逐级计算不做，本版按单个算例评估；谐波、电压波动与继电保护适应性三类约束还没纳入。"
            "这三项在系统里明确标注“未实现”，报告里也写清楚。", indent=0.5)
    _p(doc, "汇报完毕，请各位专家指正。")

    doc.add_page_break()

    # ================= 三、备答
    _h(doc, "三、备答（预判追问，答案都已核实）")
    qa = [
        ("数值是不是大模型算的？",
         "不是。全部电气数值来自 OpenDSS，第二个引擎 pandapower 做交叉校核。"
         "模型只做理解与编排，产物里每个数字都能回链到具体文件。"),
        ("凭什么相信这个容量？",
         "三重：一是五类约束逐条校核取最小值；二是独立复算换目录换进程重跑，"
         "八项关键量比对一致；三是验收矩阵九项判定，缺产物记无数据。"),
        ("和商业仿真软件什么关系？",
         "求解精度靠 OpenDSS，本系统做的是它不做的那部分：判红黄绿、出可开放容量、"
         "定位绑定约束、出验收结论与证据链。"),
        ("口径改了怎么办？",
         "规则包带审批号与生效日期，口径变更后旧结论会被判定失效，必须重跑，"
         "不会出现两个口径的数字混在一起。"),
        ("能不能接生产系统？",
         "当前版本只做只读分析与离线仿真，不连生产库、不写设备、不遥控、不代签并网许可。"
         "数据靠导出后整理成输入包，接入需要另行设计。"),
        ("红黄绿和对外发布的等级一样吗？",
         "不一样。本系统给的是单算例判定结果，用于内部评估与答复支撑；"
         "对外发布的区域等级由省公司按其口径统一发布。"),
    ]
    for i, (q, a) in enumerate(qa, 1):
        _p(doc, f"Q{i}. {q}", bold=True, space=2)
        _p(doc, "A：" + a, space=8, indent=0.4)

    # ================= 四、截图索引
    _h(doc, "四、截图索引（均取自本智能体真实运行）")
    _table(doc, [
        ["图号", "文件", "界面", "对应段落", "讲解要点"],
        ["图 2", "60_config.png", "配置", "第 2 段 业务痛点", "规则包、引擎状态、运行环境"],
        ["图 3", "65_dss.png", "算例分析", "第 3 段 算例接入", "三种入口、开算前字段校验"],
        ["图 1/4", "59_chat.png", "问答", "第 4 段 一句话任务", "结论/关键结果/依据三层、标注出处"],
        ["图 5", "66_workbench.png", "运算过程", "第 5 段 执行轨迹", "九阶段、先基线后容量、独立复算"],
        ["图 6", "62_results.png", "结果与证据", "第 6、8 段 结果与成效", "容量、C 相电压、九项验收四态"],
        ["图 7", "61_kb.png", "知识库", "第 7 段 知识支撑", "31 篇、已核对/待核"],
    ], [1.8, 4.2, 3.0, 4.0, 6.0], size=9)

    _p(doc, "说明：截图由 tools/capture_ui_pages.py 从真实运行的应用窗口截取，"
            "不是示意图；界面上的数字与 run_summary.json 等产物一致。",
       size=9, color=GREY)

    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"已生成：{path}")
    print(f"大小：{path.stat().st_size / 1024:.0f} KB")