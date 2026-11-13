# -*- coding: utf-8 -*-
"""生成《论文方案_最终版》：
  · 删除重复段落（篇幅预估那一处）
  · 追加三张核对表：创新点×空白映射、验证要素、文献引用忠实性
  · 追加一份“投稿前必查清单”（打勾式）
不改动正文技术表述。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor

SRC = Path.home() / "Desktop" / "配网承载力" / "论文方案_完整修订版_v3.docx"
OUT = SRC.with_name("论文方案_最终版.docx")
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)

MAP_ROWS = [
    ["创新点", "对应的空白", "方法要素", "验证手段", "状态"],
    ["1 边界非加性耦合与分解—修正表达",
     "空白一",
     "容量—注入映射 r=Φ(k,u;ξ,η)；容量可行集 ℋ；边界代数表达",
     "非加性算例对照（逐级裁剪 vs 边界聚合）；误差界校核",
     "方法与表达已定义；算例待补"],
    ["2 跨层级风险语义一致的 DRO",
     "空白二",
     "参数化层级模糊集（Wasserstein + 概率置信集合）；各层可行性判据",
     "单层 vs 跨层消纳容量对比；置信水平敏感性",
     "框架清晰；异质性数据待补"],
    ["3 下层可行域内生的边界协同",
     "空白三",
     "联络线交换功率区间耦合；分解结构与凸性条件；不动点分析",
     "与 Yang 2026 投影法对比；迭代收敛性实验",
     "差异点明确；对比实验待做"],
    ["4 可复现结论产出机制",
     "（差异化亮点）",
     "数据指纹双层哈希；口径绑定凭证 B={F,v_calc,Θ,γ}；三层隔离复算",
     "本智能体实测：独立复算八项比对、双引擎互校",
     "已有工程实现与产物"],
]

VERIFY_ROWS = [
    ["验证要素", "现状", "缺口", "补齐方式"],
    ["对照基线", "Yang 2026 投影法、Tan 2026 分层法可作基线",
     "尚未实现其方法复现", "按原文公式复现，或引用其公开算例结果"],
    ["误差容限", "OpenDSS×pandapower：电压 0.03 pu、网损 10%",
     "边界聚合误差界未量化", "给出误差界推导 + 数值校核"],
    ["算例规模", "IEEE 123（132 母线）、单馈线算例",
     "缺多层级联合算例", "构造主—配—微三层算例，或改造 IEEE 123 分层"],
    ["性能比例", "文中出现提升类表述", "无对照对象与条件",
     "补对照条件，或删除相关表述"],
]

CITE_ROWS = [
    ["文献", "本文引用的结论", "需核对什么", "状态"],
    ["Hu 2026", "主配联合建模，未给边界代数关系", "摘要已述联合建模；需确认是否含边界表达", "摘要已核，全文待核"],
    ["Tan 2026", "按保护影响分区聚合，分层为降维", "摘要已述分区聚合；确认是否讨论层级边界", "摘要已核，全文待核"],
    ["Gu 2024 / Bi 2025 / Wang 2025",
     "均在单一电压等级内构造模糊集", "三篇是否均单层、是否涉及跨层级", "待核"],
    ["Yang 2026", "投影运行可行域，非容量边界", "确认投影对象与是否假设下层可行域给定", "摘要已核，全文待核"],
    ["Duan 2021", "输配一体化并行计算", "确认无边界聚合内容", "摘要已核"],
    ["Karmaker 2026", "综述，用于第一章开篇", "确认其覆盖范围与本文定位不冲突", "待核"],
]

CHECKLIST = [
    "[ ] 三个空白均有 ≥1 篇竞品文献对照，且文中已说明差异",
    "[ ] 四条创新点与三个空白一一对应（创新点 4 为亮点，不占空白）",
    "[ ] 每条创新点都有“方法 + 验证”两项，缺一不可",
    "[ ] 全文无“首次/填补空白/国际领先/显著提升/大幅”类表述",
    "[ ] 所有性能比例均注明对照对象与实验条件",
    "[ ] 参考文献 8 条 DOI 可打开，作者/年份/期刊与文中一致",
    "[ ] 引用结论与原文摘要/引言一致（尤其 Gu/Bi/Wang 三篇）",
    "[ ] 检索式与截止日期已写入附录 A",
    "[ ] 术语与口径按附录 B 统一（承载力/可开放容量/层级）",
    "[ ] 无重复段落、无空引号、无占位语",
]


def para(doc, text, *, size=10, bold=False, color=None, space=4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def table(doc, rows, widths, size=8):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    for _style in ("Light Grid Accent 1", "Table Grid", "Normal Table"):
        try:
            t.style = _style
            break
        except KeyError:
            continue
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = t.cell(i, j)
            c.width = Cm(widths[j])
            r = c.paragraphs[0].add_run(val)
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = (i == 0)
    doc.add_paragraph()


def main() -> None:
    doc = Document(str(SRC))

    # 删除重复段落（保留首次出现）
    seen: set[str] = set()
    removed = 0
    for par in list(doc.paragraphs):
        t = par.text.strip()
        if len(t) < 20:
            continue
        if t in seen:
            par._element.getparent().remove(par._element)
            removed += 1
        else:
            seen.add(t)

    h = doc.add_paragraph()
    hr = h.add_run("附录 C　创新点与三个空白的映射核对表")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    hr.font.color.rgb = BLUE
    para(doc, "用途：确认“每个空白都有创新点承接、每条创新点都有验证手段”。空白缺承接或创新点缺验证，均为结构缺陷。",
         size=9, color=GREY)
    table(doc, MAP_ROWS, [3.6, 2.0, 5.2, 4.6, 3.2])

    h = doc.add_paragraph()
    hr = h.add_run("附录 D　验证要素核对表")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    hr.font.color.rgb = BLUE
    table(doc, VERIFY_ROWS, [2.4, 4.6, 4.0, 5.6])

    h = doc.add_paragraph()
    hr = h.add_run("附录 E　参考文献引用忠实性核对表")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    hr.font.color.rgb = BLUE
    para(doc, "说明：8 条文献均已通过 OpenAlex 核验“存在且可检索 DOI”；"
              "本表核验的是“文中对它的描述是否与原文一致”，此项无法由检索完成，须逐篇看原文。",
         size=9, color=RED)
    table(doc, CITE_ROWS, [3.4, 5.0, 5.6, 3.0])

    h = doc.add_paragraph()
    hr = h.add_run("附录 F　投稿前必查清单")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    hr.font.color.rgb = BLUE
    for item in CHECKLIST:
        para(doc, item, size=10, space=3)

    note = doc.add_paragraph()
    nr = note.add_run(f"本版处理：删除重复段落 {removed} 处；其余为附录 C–F 的核对表，未改动正文技术表述。")
    nr.font.name = FONT
    nr.font.size = Pt(9)
    nr.font.color.rgb = RED

    doc.save(str(OUT))
    print("已生成：", OUT)
    print("删除重复段落：", removed)
    print("新增附录：C 映射表 / D 验证表 / E 引用表 / F 清单")


if __name__ == "__main__":
    main()
