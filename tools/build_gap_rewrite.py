# -*- coding: utf-8 -*-
"""生成《1.2 三个空白改写稿 + 竞争工作对照表》，供直接替换论文方案对应章节。

改写原则：
  · 不宣称"无人研究"，改为"已有工作覆盖相邻问题，本文差异在……"
  · 每条主张都挂可核查文献（带 DOI）
  · 保留原方案的三个空白编号与技术主张
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

OUT = Path.home() / "Desktop" / "配网承载力" / "论文方案_1.2三个空白_改写稿.docx"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)

COMPETITORS = [
    ("Hu 等, 2026\nIJEPES",
     "10.1016/j.ijepes.2026.112074",
     "把配网稳态约束、主网运行约束、频率安全与构网支撑统一进承载力模型（主配联合建模）",
     "空白一（部分）",
     "联合建模 ≠ 边界代数聚合；本文给出层级间边界的代数关系与误差界"),
    ("Duan 等, 2021\nPESGM",
     "10.1109/pesgm46819.2021.9638067",
     "输配一体化联合仿真平台，解决大规模 T&D 协同计算的算力问题",
     "空白三（间接）",
     "面向仿真加速；本文面向边界聚合与分解，不涉及 HPC 平台"),
    ("Yang 等, 2026\nEPSIC",
     "10.1109/epsic70071.2026.11590289",
     "将配网高维可行域投影为低维区域，用于修正输配协同 AC-OPF、避免迭代慢",
     "空白三（直接）",
     "投影对象为运行可行域（OPF）；本文的下层可行域随上层容量决策内生变化"),
    ("Tan 等, 2026\nICEAAI",
     "10.1109/iceaai68945.2026.11442460",
     "按保护电流影响把 IIDG 分为抬升区/下沉区/邻馈线区，聚合成等效源后求最大承载力",
     "空白一（直接）",
     "分层依据为保护影响、目的是降维求解；本文处理层级间边界的非加性聚合"),
]


def para(doc, text, *, size=10.5, bold=False, color=None, space=6, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.45
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = bold
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
    t.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            c = t.cell(i, j)
            c.width = Cm(widths[j])
            r = c.paragraphs[0].add_run(val)
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = (i == 0)
    doc.add_paragraph()


def build() -> Path:
    doc = Document()
    s = doc.sections[0]
    s.left_margin = s.right_margin = Cm(2.0)

    para(doc, "论文方案 1.2 节改写稿", size=18, bold=True, color=BLUE,
         space=4)
    para(doc, "三个技术空白的重新表述 · 附竞争工作对照与文献清单", size=10.5,
         color=GREY, space=12)

    doc.add_heading("", level=1)
    head(doc, "一、改写说明（先看这四条）", 1)
    para(doc, "1. 原稿用“无人研究/缺失”类断言支撑空白，检索后发现至少三篇相邻工作，"
              "该表述不可再用；本稿改为“已有工作覆盖相邻问题，本文差异在于……”。", size=10)
    para(doc, "2. 每条主张后附可核查文献（DOI 可直接检索），不再出现无标题、无出处的作者-年份引用。", size=10)
    para(doc, "3. 三个空白的编号与技术主张不变，只调整“前人做到哪一步”的边界描述。", size=10)
    para(doc, "4. 原稿的“王守相 2025”“Song 2025”未能定位到具体文献，本稿已改为不依赖单一作者的表述；"
              "若你能提供标题，可再补回。", size=10, color=RED)

    head(doc, "二、竞争工作对照表", 1)
    rows = [["文献", "DOI", "实际做了什么", "触及哪个空白", "本文的差异"]]
    for a, b, c, d, e in COMPETITORS:
        rows.append([a, b, c, d, e])
    table(doc, rows, [3.0, 4.2, 5.4, 2.2, 5.0], size=8)

    head(doc, "三、1.2 改写稿（可直接替换原文）", 1)
    para(doc, "1.2 仍然空白的三个点", bold=True, size=11.5, space=8)

    para(doc, "空白一：多层级承载力边界的非加性耦合，缺少可计算、可校核的定量分解。", bold=True)
    para(doc, "已有工作在处理“多层级”时走了两条路。其一是主配联合建模：将配网稳态约束、"
              "主网运行约束与频率安全一并纳入承载力模型（Hu 等，2026），"
              "得到的是联合优化下的可行解，而非层级之间的边界关系。其二是分层降维："
              "按保护电流影响把分布式电源划分为抬升区、下沉区与邻馈线区，聚合成等效源后求解"
              "（Tan 等，2026），分层服务于计算简化，层级边界仍以逐级裁剪的方式处理。"
              "两条路线都以“层级划分给定”为前提，未回答一个层级划分变化时，"
              "另一层级承载力边界如何随之变化、变化量能否用代数式表达。"
              "工程上这一关系直接决定“下级能接多少”的核算精度：各点独立测得的容量通常不能直接相加，"
              "简单相加会同时引入保守与冒进两类误差。", indent=0.4)

    para(doc, "空白二：跨层级风险语义一致的分布鲁棒承载力评估。", bold=True)
    para(doc, "分布鲁棒的承载力评估已有成体系的工作：以 Wasserstein 球刻画风光出力与负荷的不确定性，"
              "经机会约束转化后求解可接入容量（Gu 等，2024；Bi 等，2025；Wang 等，2025）。"
              "这类工作均在单一电压等级内构造模糊集，样本同源、观测量同质。"
              "主网、配网与微网在样本长度、可观测量与预测模型上并不同质，"
              "同一置信水平在三层网络中对应的风险含义并不相同；"
              "若简单沿用单层模糊集，跨层级结论的置信度缺乏可比性。"
              "本文的差异在于把风险语义的层级一致性作为约束显式引入，而不是在单层内提高置信水平。", indent=0.4)

    para(doc, "空白三：下层可行域内生条件下的分布式边界协同算法。", bold=True)
    para(doc, "投影类方法在输配协同中已有应用：把配网高维可行域投影为低维区域，"
              "用于修正协同 AC-OPF 的求解，从而避免迭代收敛慢的问题（Yang 等，2026）；"
              "面向大规模系统的输配一体化计算亦有并行化实现（Duan 等，2021）。"
              "这些工作有一个共同前提：下层可行域由运行约束给定，投影的是一次性的可行域。"
              "承载力问题不同——下层可行域本身随上层容量决策改变，"
              "投影算子随决策变量移动，既有投影与分解算法不能直接套用。"
              "这是本文与该类方法的实质区别，也是分解算法需要重新设计的根本原因。", indent=0.4)

    head(doc, "四、可核查文献清单（按本文出现顺序）", 1)
    refs = [
        ("Hu Z., Yu T., Wang Z., Wu Y. Transmission–distribution coordinated "
         "distributed-photovoltaic hosting-capacity assessment considering frequency "
         "security and grid-forming support. International Journal of Electrical Power "
         "& Energy Systems, 2026.", "10.1016/j.ijepes.2026.112074"),
        ("Tan Y., Zhang H., Lou G. A Hierarchical Method for Maximum Hosting Capacity of "
         "Distributed Generation in Distribution Networks Considering Protection "
         "Constraints. ICEAAI, 2026.", "10.1109/iceaai68945.2026.11442460"),
        ("Gu T., Lv Q., Fan Q., et al. Two-stage distributionally robust assessment method "
         "for distributed generation hosting capacity. Energy Reports, 2024.",
         "10.1016/j.egyr.2024.01.079"),
        ("Bi T., Mao B., Liu H., et al. A distributionally robust calculation method for "
         "distribution network hosting capacity. IJEPES, 2025.",
         "10.1016/j.ijepes.2025.111342"),
        ("Wang Z., Jia Y., Han X., et al. Deep learning-based distributionally robust joint "
         "chance constrained hosting capacity. Applied Energy, 2025.",
         "10.1016/j.apenergy.2025.126130"),
        ("Yang L., Chen J. Y., Huang Y., Wang H. Feasible Region Projection Algorithm for "
         "Transmission and Distribution Networks Coordinated AC-OPF Problem. EPSIC, 2026.",
         "10.1109/epsic70071.2026.11590289"),
        ("Duan N., Huang C., Sun C.-C., Donde V. Parallel Hosting Capacity Analysis for "
         "Integrated Transmission and Distribution Planning. IEEE PESGM, 2021.",
         "10.1109/pesgm46819.2021.9638067"),
        ("Karmaker A. K., Du Y., Yang J., et al. Challenges and applications of hosting "
         "capacity analysis in DER-rich distribution systems. Applied Energy, 2026.",
         "10.1016/j.apenergy.2025.127323"),
    ]
    for i, (txt, doi) in enumerate(refs, 1):
        para(doc, f"[{i}] {txt}  DOI: {doi}", size=9, space=4)

    head(doc, "五、需要在正文其余部分同步处理的三处", 1)
    para(doc, "① 检索自述：原文“我针对……做了检索”属过程叙述，建议改为“检索覆盖 Web of Science 与 "
              "IEEE Xplore，检索式如下”，并给出检索式与截止日期，便于复现。", size=10)
    para(doc, "② 创新点表述：删除“首次”“填补空白”“国际领先”，改为“与上述工作相比，本文在……方面更进一步”。", size=10)
    para(doc, "③ 数字声明：文中若出现性能提升比例，需注明对照基线与实验条件；无基线者删除。", size=10)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    p = build()
    print("已生成：", p)
    print(f"大小：{p.stat().st_size / 1024:.0f} KB")
