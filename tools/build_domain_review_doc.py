# -*- coding: utf-8 -*-
"""生成《配网承载力：术语、文献、现状与业务流》Word 版。

内容来自本会话的核验结果：8 篇文献均经 OpenAlex 核到 DOI；标准可核；
教材与 IEC TS 63276 条款标注为待核，不虚构页码与条款号。
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

OUT = Path.home() / "Desktop" / "配网承载力_术语文献与业务流.docx"
FONT = "微软雅黑"
MONO = "Consolas"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)

REFS = [
    ("Karmaker 等，2026，Challenges and applications of hosting capacity analysis in "
     "DER-rich distribution systems，Applied Energy", "10.1016/j.apenergy.2025.127323"),
    ("Hu 等，2026，输配协同分布式光伏承载力评估（含频率安全与构网支撑），IJEPES",
     "10.1016/j.ijepes.2026.112074"),
    ("Duan 等，2021，输配一体化并行承载力分析，IEEE PESGM",
     "10.1109/pesgm46819.2021.9638067"),
    ("Yang 等，2026，可行域投影算法求解输配协同 AC-OPF，EPSIC",
     "10.1109/epsic70071.2026.11590289"),
    ("Tan 等，2026，计及保护约束的分层最大承载力，ICEAAI",
     "10.1109/iceaai68945.2026.11442460"),
    ("Gu 等，2024，两阶段分布鲁棒承载力评估，Energy Reports",
     "10.1016/j.egyr.2024.01.079"),
    ("Bi 等，2025，分布鲁棒承载力计算方法，IJEPES", "10.1016/j.ijepes.2025.111342"),
    ("Wang 等，2025，深度学习驱动的分布鲁棒联合机会约束承载力，Applied Energy",
     "10.1016/j.apenergy.2025.126130"),
]


def para(doc, text, *, size=10.5, bold=False, color=None, space=6, indent=0,
         align=WD_ALIGN_PARAGRAPH.LEFT, mono=False):
    p = doc.add_paragraph()
    p.alignment = align
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.45
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = MONO if mono else FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def head(doc, text, level=1):
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.name = FONT
        r.font.color.rgb = BLUE if level == 1 else RGBColor(0x1A, 0x1A, 0x1A)


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


def build() -> Path:
    doc = Document()
    s = doc.sections[0]
    s.left_margin = s.right_margin = Cm(2.2)

    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("配网承载力：术语、文献、现状与业务流")
    r.font.name = FONT
    r.font.size = Pt(18)
    r.bold = True
    r.font.color.rgb = BLUE
    para(doc, "技术梳理稿　|　文献均经 OpenAlex 核验 DOI；未核实项已明确标注",
         size=9.5, color=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space=12)

    # 一
    head(doc, "一、书籍与文献", 1)
    para(doc, "国际文献可直接引用的是下表 8 篇，DOI 均可检索验证。它们覆盖三个方向："
              "综述定位（Karmaker）、主配联合与输配协同（Hu、Duan、Yang）、"
              "分层求解（Tan）与分布鲁棒（Gu、Bi、Wang）。")
    table(doc, [["文献", "DOI"]] + [[a, b] for a, b in REFS],
          [11.0, 5.6], size=8)
    para(doc, "标准层面，DL/T 2041-2019《分布式电源接入电网承载力评估导则》给出系统级与"
              "设备级两层定义；GB/T 12325 约束电压偏差；GB/T 15544 规定三相短路电流计算。"
              "这三项本系统已在用。IEC TS 63276:2024 为承载力评估技术规范，"
              "标准本身存在，但其条款内容我未读全文，引用前须核对原文【待核】。")
    para(doc, "教材方面，国内《电力系统分析》《配电网规划》类教材均设分布式电源接入章节，"
              "但具体版本、页码未核实，正文中只宜写“参见相关章节”，不宜给出页码【待核】。",
         color=RED)

    # 二
    head(doc, "二、两个基本术语", 1)
    para(doc, "配电网指变电站 10 kV 出线（或 20 kV、6 kV）至用户计量点之间的网络，"
              "含馈线、开关、配变与台区低压线路。与输电网的差别不在电压等级，而在结构："
              "辐射状或环网开环运行、三相不平衡显著、单相与两相支路并存、量测覆盖低。")
    para(doc, "承载力（hosting capacity）指给定约束下电网可接纳的分布式电源最大容量。"
              "DL/T 2041 把它分成系统级与设备级：前者是区域可消纳的最大装机，"
              "后者是单台配变或单条线路的可接入容量。两层不是同一量的缩放，"
              "层级之间的聚合关系本身就是研究问题。")
    para(doc, "国内还有两个近义说法常与承载力混用：可开放容量（扣除已并网与已受理后的剩余空间）、"
              "可接入容量（单点报装语境）。本系统输出时把物理可开放容量与管理可开放容量分开给，"
              "以避免把设备余量当作对外答复量。")

    # 三
    head(doc, "三、国内外现状与分歧", 1)
    para(doc, "国际路线以 hosting capacity 单一指标为轴，方法重心在不确定性处理——时序仿真、"
              "概率潮流、分布鲁棒优化；评价对象通常是单条馈线或单个变电站，很少涉及行政层级的分级发布。"
              "国内路线以分级管控为轴：按反向负载率划分红黄绿，红区暂停受理、黄区专项论证、绿区正常接入，"
              "并要求逐级评估且下级不高于上级。")
    table(doc, [
        ["分歧点", "国际做法", "国内做法", "后果"],
        ["分级依据", "容量或电压裕度表述", "反向负载率 λ：红 >100%、黄 80–100%、绿 ≤80%",
         "同一馈线两套口径可得出不同颜色"],
        ["层级处理", "单层或主配联合建模", "逐级校核、下级服从上级",
         "联合建模与序贯裁剪不可互换"],
        ["约束边界", "电压、热稳、保护为主", "另加谐波、短路、反送",
         "约束集不同则容量不可比"],
        ["时间尺度", "8760 时序或概率场景", "典型日、典型时段",
         "取最不利断面的严格程度不同"],
        ["不确定性", "DRO、机会约束成体系", "多数仍用确定性场景",
         "置信水平不可跨方法比较"],
    ], [2.6, 4.6, 5.2, 4.2])
    para(doc, "这五处差异是同一地区不同单位算出不同承载力的主要来源，不是计算错误。"
              "在已核文献范围内，没有一篇同时覆盖“多层级边界聚合”“跨层级风险语义一致”"
              "“下层可行域内生”三项；但分层求解与主配联合建模已有工作（Tan 2026、Hu 2026），"
              "因此论文中只能写“在已有路线上更进一步”，不能写“无人研究”。")

    # 四
    head(doc, "四、计算工具与优化工具", 1)
    table(doc, [
        ["工具", "特点", "适用"],
        ["OpenDSS", "三相不平衡原生建模，配网专用", "台区、单相支路、不平衡度"],
        ["pandapower", "Python 生态，正序平衡为主", "快速校核、交叉验证"],
        ["MATPOWER", "输电网研究常用", "主网等值、最优潮流"],
        ["PSASP / PSD-BPA", "国内电力系统分析标准工具", "主网侧稳定与短路校核"],
        ["DIgSILENT PowerFactory", "机电与电磁暂态一体", "暂态、谐波"],
        ["GridLAB-D", "配电与负荷行为联合仿真", "需求响应、时序"],
    ], [4.4, 6.4, 6.0])
    table(doc, [
        ["工具", "用途"],
        ["Gurobi / CPLEX", "混合整数与凸优化，承载力上限搜索"],
        ["Pyomo / CVXPY", "建模层，把约束写成数学形式"],
        ["scipy.optimize", "轻量场景下的单目标搜索"],
        ["ADMM / ATC 实现", "输配分解协同"],
    ], [5.0, 11.8])
    para(doc, "本系统实际使用 OpenDSS 主算、pandapower 交叉校核；容量搜索用二分法而非通用优化器。"
              "原因在于单算例、单目标（容量最大化），精度 10 kW 时二分比调 MILP 更快，"
              "也便于把收敛区间写进报告。")

    # 五
    head(doc, "五、常用公式", 1)
    for name, formula, note in [
        ("反向负载率（分级判据）", "λ = (P_D − P_L) / S_e",
         "P_D 分布式出力，P_L 等效负荷，S_e 设备运行限值；取周期内 λ_max 作为判定依据"),
        ("设备负载率", "β = S / S_N × 100%", "S 实际视在功率，S_N 额定容量"),
        ("三相不平衡度", "ε = |U₂| / |U₁| × 100%", "对称分量法，负序与正序幅值之比"),
        ("容量搜索收敛判据", "|hi − lo| ≤ precision", "本系统 precision = 10 kW，max_iter = 25"),
    ]:
        para(doc, name, bold=True, size=10, space=2)
        para(doc, formula, mono=True, size=11, indent=0.6, space=2)
        para(doc, note, size=9, color=GREY, indent=0.6, space=8)
    para(doc, "电压偏差按 GB/T 12325：10 kV 及以下三相供电 ±7%，220 V 单相 +7%/−10%。"
              "短路电流按 GB/T 15544（等效电压源法）计算后与断路器开断能力比较。")

    # 六
    head(doc, "六、业务流与核心内容", 1)
    para(doc, "实务中承载力评估是闭环八段：基础数据收集与治理 → 评估边界与约束设定 → "
              "承载力计算分析 → 结果分级（红黄绿）→ 结果发布与公示 → 业务应用（接入管理）→ "
              "提升措施与改造 → 动态更新与闭环，更新后回到数据准备。")
    para(doc, "本系统对八段的覆盖是：已实现 1 段（结果分级），部分实现 6 段，"
              "未实现 1 段（结果发布与公示）。发布环节涉及对外口径，须按省级规范另行生成，"
              "系统不自行设定阈值。", color=RED)
    para(doc, "核心内容分四块。约束建模把电压、热稳、反送、不平衡、短路写成可判定的不等式，"
              "并注明出处与生效日期；容量搜索在可行域边界上求最大可接入容量，同时记录首个触发的约束；"
              "瓶颈定位指到具体元件与相别，使整改方向可判断；验证与留痕用独立复算、交叉校核与证据链，"
              "使结论可被第三方复核。")
    para(doc, "前两块决定结论“是多少”，后两块决定结论“能不能用”。国内报告多在前两块着力，"
              "后两块偏弱；国际文献相反——方法严谨，但很少讨论报告形态与责任边界。")

    # 附
    head(doc, "附：证据边界", 1)
    para(doc, "已核实：上表 8 篇文献存在且 DOI 可检索；DL/T 2041-2019、GB/T 12325、"
              "GB/T 15544 为本系统实际使用的标准；IEC TS 63276:2024 标准存在。", size=9.5)
    para(doc, "未核实：IEC TS 63276 的具体条款与分级维度；国内教材的版本与页码；"
              "文中“国际做法/国内做法”的概括来自文献与公开报告的归纳，不是某一份文件的原话。",
         size=9.5, color=RED)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    p = build()
    print("已生成：", p)
    print(f"大小：{p.stat().st_size / 1024:.0f} KB")
