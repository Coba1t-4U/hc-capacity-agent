# -*- coding: utf-8 -*-
"""生成《配网承载力智能体评估报告 · 目录大纲》docx。

融合三方：
  1. 用户给的 6 段框架（摘要 / 边界与数据 / 多维结果 / 计算证据 / 分级建议 / 附录）
  2. 政府公开报告的实际表格列设计（永川供电公司 2024-12 承载力评估报告）
  3. 本系统现有两份报告的真实章节与产物（reporting.py / dss_report.py）

每条都标「数据来源 / 产出方式」：能自动生成的、要人工补的、以及尚未实现的，分开写清。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

OUT = Path(r"C:\Users\Chen\Desktop\配网承载力\承载力结果报告_目录大纲.docx")
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)
GREEN = RGBColor(0x1E, 0x6B, 0x3A)


def _p(doc, text, *, size=10.5, bold=False, color=None, space=6,
       align=WD_ALIGN_PARAGRAPH.LEFT, indent=0):
    par = doc.add_paragraph()
    par.alignment = align
    par.paragraph_format.space_after = Pt(space)
    par.paragraph_format.line_spacing = 1.4
    if indent:
        par.paragraph_format.left_indent = Cm(indent)
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


def _table(doc, rows, widths, *, size=9, header=True):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = t.cell(i, j)
            cell.width = Cm(widths[j])
            par = cell.paragraphs[0]
            par.paragraph_format.space_after = Pt(1)
            r = par.add_run(str(val))
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = header and i == 0
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def build() -> Path:
    doc = Document()
    s = doc.sections[0]
    s.left_margin = s.right_margin = Cm(2.2)

    _p(doc, "配网承载力智能体评估报告", size=19, bold=True, color=BLUE,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=2)
    _p(doc, "目录大纲（可据此直接写正文）", size=12.5,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=2)
    _p(doc, "V1.1.0 · 2026 年 11 月", size=9, color=GREY,
       align=WD_ALIGN_PARAGRAPH.CENTER, space=12)

    # ---------------- 0 体例
    _h(doc, "零、报告体例：一份报告、两种形态")
    _p(doc, "本报告采用「对话摘要 + 正文/工作簿」双形态，两者同源，不是两份文件。")
    _table(doc, [
        ["形态", "用途", "长度", "谁看"],
        ["对话摘要", "决策者一眼看完：结论、关键指标、突出发现、需关注项", "≤ 1 屏 / 约 300 字", "领导、评审"],
        ["正文", "结构化说明：边界、方法、结果、证据、建议", "10–20 页", "评审、复核、归档"],
        ["工作簿/附表", "逐条明细：母线、线路、台区、约束明细", "不限，按对象数", "执行人员"],
    ], [3.4, 10.4, 4.2, 4.0], size=9)
    _p(doc, "三者的数字必须完全一致。做法是：摘要与正文都引用同一份结果文件（run_summary.json / "
            "capacity.csv），不各自抄数。", size=9.5, color=GREY)

    # ---------------- 一 摘要
    _h(doc, "一、结论摘要（对应第 1 章）")
    _p(doc, "开篇一句话给结论，随后 3–5 个关键指标，再 3 条以内突出发现，最后需关注项。")
    _table(doc, [
        ["要素", "写什么", "示例（取 FD002 真实值）", "产出方式"],
        ["一句话结论", "等级 + 容量 + 主导约束", "本馈线具备接入空间，物理可开放容量 333.98 kW，受末端 C 相电压约束", "自动"],
        ["关键指标", "等级／可开放容量（双口径）／越限节点数／核心约束",
         "绿；333.98 / 270.53 kW；基线 0 越限；电压", "自动"],
        ["突出发现", "点名对象 + 数值，不写泛泛结论",
         "bus-lv-002 的 C 相电压 1.1008 pu，超上限 0.0008 pu", "自动"],
        ["需关注项", "下一步该做什么、谁来做", "整改方向为调压（无功补偿/有载调压），非增容", "自动 + 人工确认"],
    ], [3.0, 7.4, 9.6, 3.4], size=9)

    # ---------------- 二 边界与数据
    _h(doc, "二、评估边界与数据基础（第 2–3 章）")
    _p(doc, "2.1 评估依据：DL/T 2041-2019《分布式电源接入电网承载力评估导则》、GB/T 12325 等；"
            "项目批文；规则包版本号、审批号、生效日期。")
    _p(doc, "2.2 评估范围：供区、电压等级、变电站/线路/台区清单、时间窗与场景（光伏大发、负荷低谷等）。")
    _p(doc, "2.3 数据来源与质量：营销/PMS/调度/用采/GIS 来源标注；数据快照与指纹；"
            "缺失项与处理方式；质量治理结果（quality_issues）。")
    _p(doc, "2.4 可复现声明：引擎版本（OpenDSS 0.9.4 / DSS C-API 0.14.5）、"
            "次级引擎（pandapower 3.5.6）、规则包版本、输入指纹。"
            "这一条让报告与“某次运行”严格绑定。", space=4)

    # ---------------- 三 多维结果
    _h(doc, "三、多维承载力评估结果（第 4–6 章，报告主体）")
    _p(doc, "按五个维度分层展示，每个维度都要给“结论 + 依据 + 未校核说明”。")
    _table(doc, [
        ["维度", "指标", "本系统现状", "报告写法"],
        ["热稳定与反向负载", "线路/配变负载率、反向负载率、最大可附加容量",
         "✅ 已实现（THERMAL / REVERSE_FLOW）", "给负载率与绑定元件"],
        ["电压质量", "节点电压偏差、越限节点清单、越限时长",
         "✅ 已实现（VOLTAGE，取最不利相）", "给节点+相位+裕度"],
        ["设备与短路", "配变容量、短路电流校核",
         "✅ 已实现（SHORT_CIRCUIT ≤ 20000 A）", "给校核结论"],
        ["电能质量", "谐波、三相不平衡度",
         "◐ 不平衡已实现；谐波未纳入", "不平衡给数值；谐波写“未校核”"],
        ["分级结果", "按等级逐一标注对象",
         "◐ 单算例给等级；全网分级未实现", "单算例给等级，不冒充全网"],
    ], [3.6, 6.6, 6.6, 6.6], size=9)
    _p(doc, "注意：Harmonic 一类“没有数据”的维度，必须写「未校核」并说明原因，"
            "不能留空、更不能写成“通过”。永川供电公司的公开报告就是这么处理的"
            "（“因绝大多数母线节点无谐波监测数据，故未做谐波校核”）。", size=9.5, color=RED)

    # ---------------- 四 证据
    _h(doc, "四、计算证据与可追溯（第 7 章，智能体报告的关键差异）")
    _p(doc, "传统报告只给结论，智能体报告必须能对每个关键数值做“证据回放”。"
            "对摘要里的每条数值，附一个证据面板：")
    _table(doc, [
        ["证据项", "内容", "本系统对应产物"],
        ["所用工具/模型", "引擎与版本、规则包版本、技能卡编号", "config/engine_fingerprint.json、skill_registry.json"],
        ["输入与边界", "输入快照指纹、场景与断面、约束限值", "manifest.json、config/rules.yaml"],
        ["原始输出片段", "引擎日志、母线/元件结果行", "logs/、results/bus_results.csv、element_results.csv"],
        ["校核状态", "独立复算、交叉校核、验收判定", "verification/、results/engine_crosscheck.json"],
        ["一致性校核", "摘要数值 vs 结果文件是否一致", "report_manifest.json（result_hash）"],
        ["时间戳与调用", "每次工具调用时间、参数快照", "audit/events.jsonl"],
    ], [3.6, 9.0, 10.8], size=9)
    _p(doc, "这一章是本报告区别于传统报告的地方，也是评审最容易问的地方："
            "“这个数从哪来”。能点到文件、点到行、点到阶段，就算答上了。", size=9.5, color=GREY)

    # ---------------- 五 分级建议
    _h(doc, "五、分级结果与措施建议（第 8–9 章）")
    _p(doc, "8.1 分级分布：各级对象数量与占比、红区清单及原因、主导约束统计。")
    _p(doc, "8.2 一台一策（仅对红/橙对象）：对象名、已接入、可继续接入、约束原因、建议容量、建议措施。")
    _p(doc, "9.1 按等级给差异化建议：绿—直接接入；黄/橙—无功补偿、储能、柔性控制；红—限制接入或升级改造。")
    _p(doc, "9.2 建议要能落地：措施对应到具体约束（电压→调压；热稳→增容；反送→储能/限出力）。",
       space=4)

    # ---------------- 六 附录
    _h(doc, "六、附录与工作簿（第 10–12 章）")
    _p(doc, "10.1 附表 A 变电站母线：变电站 / 评估母线 / 短路校核 / 电压校核 / 谐波校核 / 等级 / 可承载 Pmax / 可新增")
    _p(doc, "10.2 附表 B 10 kV 线路：线路名 / 是否反送 / 热稳 / 电压 / 等级 / 可开放容量 / 绑定约束")
    _p(doc, "10.3 附表 C 台区：台区名 / 配变容量 / 负载率 / 电压 / 等级 / 可开放容量")
    _p(doc, "10.4 附表 D 约束明细：场景 / 断面 / 元件 / 字段 / 实测 / 限值 / 裕度")
    _p(doc, "11 证据索引：文件清单与哈希、审计事件编号、结果哈希")
    _p(doc, "12 术语与口径说明：承载力、可开放容量（物理/管理）、绑定约束、四态判定")
    _p(doc, "附表列设计取自政府公开报告的实际格式（永川供电公司 2024 年 12 月），"
            "区别是把“校核通过/未校核”写成可判定的四态，而不是只有通过/不通过。",
       size=9.5, color=GREY)

    # ---------------- 七 映射
    _h(doc, "七、与本系统现有报告的映射（写之前先看这张表）")
    _table(doc, [
        ["本大纲章节", "对应 reporting.py 现有章节", "状态", "要补什么"],
        ["1 结论摘要", "（无独立摘要章）", "❌ 缺", "新增摘要页：一句话+4指标+3发现"],
        ["2 评估依据", "1 任务概况（部分）", "◐", "补 DL/T 2041、规则包审批号"],
        ["3 数据与边界", "2 数据与口径", "✅", "—"],
        ["4 多维结果", "3 计算过程 + 4 评估结果", "◐", "拆成五维度分节，逐维给结论"],
        ["5 分级与建议", "4 评估结果（仅等级）＋现有整改库", "❌ 缺", "新增分级分布、一台一策、建议章"],
        ["6 计算证据", "7 独立验证 + 9 证据索引", "◐", "合并为“证据章”，一条数值一个面板"],
        ["7 附录与工作簿", "（部分：附表在 dss_report 里）", "◐", "统一四张附表字段"],
    ], [4.0, 8.4, 2.4, 8.6], size=9)

    # ---------------- 八 依据与待核
    _h(doc, "八、依据核对状态（写进报告前必须分清）")
    _table(doc, [
        ["依据", "核对状态", "说明"],
        ["DL/T 2041-2019 分布式电源接入电网承载力评估导则", "【已核对存在】",
         "国内导则；本系统口径来源；国网公开评估表明确按此编制"],
        ["IEC TS 63276:2024 Guidelines for the hosting capacity evaluation of distribution networks",
         "【已核对存在】",
         "规定 DER 最大外送容量（hosting capacity）评估方法；但“六维度指标 + 绿到红五级评定”"
         "这一具体表述我未核到原文，引用前需查原文条款"],
        ["GB/T 12325 电压偏差", "【已核对】", "本系统电压限值 0.90–1.10 pu 的依据"],
        ["永川供电公司承载力评估报告（2024-12）", "【已核对】",
         "公开可查，本大纲的表格列设计参考其附表 1–3"],
        ["book118《分布式光伏接入配电网承载力评估方案》目录", "【待核】",
         "未核原件；如要引用其章节骨架，需先取得文档确认"],
    ], [9.2, 4.4, 8.0], size=9)

    # ---------------- 九 写作顺序
    _h(doc, "九、建议的写作顺序")
    _p(doc, "1. 先定摘要（第 1 章）：摘要写不出来，说明结果还没想清；")
    _p(doc, "2. 再写第 4–6 章多维结果：这是主体，占篇幅一半；")
    _p(doc, "3. 然后第 7 章证据：把摘要里的每个数点回文件；")
    _p(doc, "4. 再写第 8–9 章分级与建议：建议必须挂在约束上；")
    _p(doc, "5. 最后补第 2–3 章边界数据、附录与术语（这些是“填”，不是“想”）。")

    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"已生成：{path}")
    print(f"大小：{path.stat().st_size / 1024:.0f} KB")
