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


def _p(doc, text, *, size=10.5, bold=False, color=None, space=6,
       align=WD_ALIGN_PARAGRAPH.LEFT, indent=0):
    par = doc.add_paragraph()
    par.alignment = align
    par.paragraph_format.space_after = Pt(space)
    par.paragraph_format.line_spacing = 1.45
    if indent:
        par.paragraph_format.left_indent = Cm(indent)
    run = par.add_run(text)
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
        r = par.add_run(text)
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
        ["开场", "0:00–0:30", "智能体主界面（问答页）", "无操作，讲定位", "图 1"],
        ["业务痛点", "0:30–1:40", "配置页：规则包与运行环境", "点左侧「配置」", "图 2"],
        ["功能①算例接入", "1:40–2:20", "算例分析页：算例清单与字段", "点「算例分析」", "图 3"],
        ["功能②一句话下任务", "2:20–3:10", "问答页：结论 + 依据", "输入一句话并发送", "图 1 / 图 4"],
        ["功能③执行轨迹", "3:10–3:50", "运算过程：九阶段状态与耗时", "点「运算过程」", "图 5"],
        ["功能④结果与证据", "3:50–5:00", "结果与证据：容量、绑定约束、验收", "点「结果与证据」", "图 6"],
        ["功能⑤知识支撑", "5:00–5:30", "知识库：31 篇条目与来源状态", "点「知识库」", "图 7"],
        ["成效与价值", "5:30–6:20", "回到结果页（或 PPT 效率页）", "无操作", "图 6"],
        ["边界与下一步", "6:20–6:40", "结果页或 PPT 边界页", "无操作", "—"],
    ], [3.6, 2.4, 6.0, 4.0, 3.0])

    _p(doc, "4. 兜底方案（现场必备）", bold=True)
    for it in [
        "计算偶发卡顿：直接切到「结果与证据」，用已跑好的运行继续讲，说明「这一次已经跑完」；",
        "窗口被遮挡：提前把智能体窗口置于最前，关闭微信/浏览器通知；",
        "投影分辨率低：把界面字号调到 110%，重点看结论与数字，不逐行念表格；",
        "被问倒：明确说「这一点我记下来，会后给答复」，不要现场编数字。",
    ]:
        _p(doc, "· " + it, space=3, indent=0.5)

    doc.add_page_break()

    # ================= 二、讲稿
    _h(doc, "二、讲稿（可直接照读，括号内是动作提示）")

    _p(doc, "【第 1 段】开场　0:00–0:30", bold=True, color=BLUE)
    _rich(doc, [("（画面：智能体主界面，停在问答页，先让观众看一眼）", False, GREY)])
    _p(doc, "各位领导、各位专家，上午好。我汇报的是配网承载力智能体。"
            "一句话说清它做什么：给定一条 10 千伏馈线，算出它还能接入多少分布式电源，"
            "并说清被哪一条约束卡住、凭什么可信。"
            "底座是开源版 DeepSeek Harness，潮流由 OpenDSS 计算；"
            "大模型只做理解与编排，不产生任何电气数值。")

    _p(doc, "【第 2 段】业务痛点　0:30–1:40", bold=True, color=BLUE)
    _rich(doc, [("（动作：点左侧「配置」，画面切到运行环境与规则包）", False, GREY)])
    _p(doc, "先说为什么需要它。真正的门槛不在潮流求解，而在三件事。")
    _p(doc, "第一是口径。同样叫「电压合格」，国标是 0.90 到 1.10 标幺，"
            "项目批准口径可能是正负 7%，公共连接点与单个用户限值也不一样。"
            "口径不随结果固化，两次评估就没法比。"
            "所以判据写在规则包里，并绑定审批号与生效日期，口径一变，旧结论直接失效。",
       indent=0.5)
    _p(doc, "第二是约束。只校核电压、不校核热稳和反送，容量一定偏大。"
            "本系统五类约束逐条校核、取最小值，并记录是哪一条先到限。", indent=0.5)
    _p(doc, "第三是依据。过去报告只有一个容量数字，追问「卡在哪个节点、裕度多少」往往答不上来。",
       indent=0.5)
    _shot(doc, "60_config.png",
          "图 2　配置页：引擎状态、模型版本、规则包版本与运行环境一目了然")

    _p(doc, "【第 3 段】功能①：算例怎么进来　1:40–2:20", bold=True, color=BLUE)
    _rich(doc, [("（动作：点左侧「算例分析」；画面显示算例清单）", False, GREY)])
    _p(doc, "系统支持三种入口：结构化输入包、OpenDSS 原生算例目录、压缩包直接拖入。"
            "这里点一下桌面算例即可接入。接入时先做字段与单位校验，"
            "缺字段、单位不一致、越界都在开算之前暴露，不会算完才发现结果没意义。")
    _shot(doc, "65_dss.png",
          "图 3　算例分析：算例清单与字段校验，开算前把关")

    _p(doc, "【第 4 段】功能②：一句话下任务　2:20–3:10", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「问答」，输入「算一下 FD002 还能接多少光伏，并给我计算报告」，回车）",
                 False, GREY)])
    _p(doc, "这是最自然的使用方式：一句话。系统不会带着默认假设直接出数，"
            "而是先确认算例、规则包版本和口径，条件不足会追问。")
    _p(doc, "结果分三层：结论、关键结果、依据。它不只给容量，还给越限点、过载元件和反向潮流，"
            "最后标注出处——每个数字都能指回某一份产物文件。")
    _shot(doc, "59_chat.png",
          "图 1 / 图 4　问答页：结论、关键结果、依据与出处分层呈现")

    _p(doc, "【第 5 段】功能③：执行轨迹看得见　3:10–3:50", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「运算过程」，画面显示九阶段列表与耗时）", False, GREY)])
    _p(doc, "这一页是给评审看的。九阶段流水线，每一步都有状态和耗时。")
    _p(doc, "两点要强调。第一，先跑基线、再谈新增容量：基线本身越限时，"
            "系统不会给一个乐观数字。第二，容量搜索之后还有独立复算，"
            "换目录换进程重跑，比对不上即判定验证失败，结论不得发布。")
    _shot(doc, "66_workbench.png",
          "图 5　运算过程：九阶段状态与耗时，含独立复算与证据封存")

    _p(doc, "【第 6 段】功能④：结果与证据（重点）　3:50–5:00", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「结果与证据」，停在验收与指标页）", False, GREY)])
    _p(doc, "这是最核心的一页。以验收算例 FD002 为例：物理可开放容量 333.98 千瓦，"
            "按管理裕度折算后 270.53 千瓦。")
    _p(doc, "更关键的是下一句：绑定约束是电压越限，位置在 bus-lv-002 的 C 相，"
            "接入后 1.1008 标幺，上限 1.1，裕度负 0.0008。"
            "这句话直接决定整改方向——不是增容，而是调压。只给一个容量数字，这个判断做不出来。",
       indent=0.5)
    _p(doc, "下面是验收矩阵，九项指标、四种判定。请注意「无数据」：它既不等于通过，"
            "也不等于未达标，而是表示这次没有可判定的产物。本系统不允许把无数据写成达标。")
    _shot(doc, "62_results.png",
          "图 6　结果与证据：容量结论、绑定约束定位、九项验收矩阵")

    _p(doc, "【第 7 段】功能⑤：知识支撑　5:00–5:30", bold=True, color=BLUE)
    _rich(doc, [("（动作：点「知识库」，展示条目与来源状态）", False, GREY)])
    _p(doc, "判据从哪里来要能说清。系统内置 31 篇知识条目，标注来源并区分「已核对」与「待核」，"
            "待核条目只作提示、不作结论依据。DL/T 2041 对系统级与设备级承载力的区分，"
            "就是口径的直接来源。")
    _shot(doc, "61_kb.png",
          "图 7　知识库：条目、来源与核对状态，区分已核对与待核")

    _p(doc, "【第 8 段】成效与价值　5:30–6:20", bold=True, color=BLUE)
    _p(doc, "最后说成效。我不给「提升百分之多少」这类数字，各单位数据基础差别太大，"
            "那种数字经不起核对。我说三个可核对的事实。")
    _p(doc, "第一，环节可自动化。一次评估的七个环节里，六个可以完全交给程序，"
            "剩下一个是「判据能不能用」，仍由人决定。", indent=0.5)
    _p(doc, "第二，算得动。十六节点、三场景、十二个时间片、四百二十条约束，"
            "单次评估三到四秒，并取最不利断面作为依据。", indent=0.5)
    _p(doc, "第三，可复核。验收九项固定判定、双引擎交叉校核，任一不一致就不发布。", indent=0.5)
    _p(doc, "效率提升的本质是分工变化：人从逐次操作工具，转向维护口径、处理异常、审核结论。")

    _p(doc, "【第 9 段】边界与下一步　6:20–6:40", bold=True, color=BLUE)
    _p(doc, "也要说清边界。本版有三件事没做：结果发布与公示没做，逐级计算没做，"
            "谐波、电压波动与继电保护适应性三类约束还没纳入。"
            "这三项在系统里都明确标注「未实现」，不代做。")
    _p(doc, "下一步：一是逐级承载力与上级约束的串接；二是改造措施的定量校核；"
            "三是发布口径，这一项需依据省公司规范，不自行设定阈值。")
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
