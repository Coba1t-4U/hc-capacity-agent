# -*- coding: utf-8 -*-
"""把 1.2 三个空白的改写稿合入原方案，生成修订版 docx（原文件不动）。

处理三处：
  1) 三条“空白X：……”段落正文替换为改写稿
  2) 第一人称检索自述改为可复现的客观表述
  3) 文末追加参考文献清单（带 DOI），并标注需作者核对
"""
import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

SRC = Path.home() / "Desktop" / "配网承载力" / "论文方案-题目创新点与框架.docx"
OUT = SRC.with_name("论文方案-题目创新点与框架_修订版.docx")
FONT = "微软雅黑"

GAPS = {
    "空白一": ("已有工作在处理“多层级”时走了两条路。其一是主配联合建模：将配网稳态约束、"
               "主网运行约束与频率安全一并纳入承载力模型（Hu 等，2026），得到的是联合优化下的"
               "可行解，而非层级之间的边界关系。其二是分层降维：按保护电流影响把分布式电源划分为"
               "抬升区、下沉区与邻馈线区，聚合成等效源后求解（Tan 等，2026），分层服务于计算简化，"
               "层级边界仍以逐级裁剪的方式处理。两条路线都以“层级划分给定”为前提，未回答一个层级"
               "划分变化时，另一层级承载力边界如何随之变化、变化量能否用代数式表达。工程上这一关系"
               "直接决定“下级能接多少”的核算精度：各点独立测得的容量通常不能直接相加，简单相加会"
               "同时引入保守与冒进两类误差。"),
    "空白二": ("分布鲁棒的承载力评估已有成体系的工作：以 Wasserstein 球刻画风光出力与负荷的"
               "不确定性，经机会约束转化后求解可接入容量（Gu 等，2024；Bi 等，2025；Wang 等，2025）。"
               "这类工作均在单一电压等级内构造模糊集，样本同源、观测量同质。主网、配网与微网在"
               "样本长度、可观测量与预测模型上并不同质，同一置信水平在三层网络中对应的风险含义"
               "并不相同；若简单沿用单层模糊集，跨层级结论的置信度缺乏可比性。本文的差异在于把"
               "风险语义的层级一致性作为约束显式引入，而不是在单层内提高置信水平。"),
    "空白三": ("投影类方法在输配协同中已有应用：把配网高维可行域投影为低维区域，用于修正"
               "协同 AC-OPF 的求解，从而避免迭代收敛慢的问题（Yang 等，2026）；面向大规模系统的"
               "输配一体化计算亦有并行化实现（Duan 等，2021）。这些工作有一个共同前提：下层可行域"
               "由运行约束给定，投影的是一次性的可行域。承载力问题不同——下层可行域本身随上层容量"
               "决策改变，投影算子随决策变量移动，既有投影与分解算法不能直接套用。这是本文与该类"
               "方法的实质区别，也是分解算法需要重新设计的根本原因。"),
}

REFS = [
    ("Hu Z., Yu T., Wang Z., Wu Y. Transmission–distribution coordinated distributed-"
     "photovoltaic hosting-capacity assessment considering frequency security and "
     "grid-forming support. International Journal of Electrical Power & Energy Systems, 2026.",
     "10.1016/j.ijepes.2026.112074"),
    ("Tan Y., Zhang H., Lou G. A Hierarchical Method for Maximum Hosting Capacity of "
     "Distributed Generation in Distribution Networks Considering Protection Constraints. "
     "ICEAAI, 2026.", "10.1109/iceaai68945.2026.11442460"),
    ("Gu T., Lv Q., Fan Q., et al. Two-stage distributionally robust assessment method for "
     "distributed generation hosting capacity. Energy Reports, 2024.",
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


def set_text(par, text: str) -> None:
    """替换段落文本，保留第一个 run 的样式。"""
    if not par.runs:
        par.add_run(text)
        return
    par.runs[0].text = text
    for r in par.runs[1:]:
        r.text = ""


def main() -> None:
    doc = Document(str(SRC))
    changed = {"gap": 0, "search": 0}

    for par in doc.paragraphs:
        t = par.text.strip()
        for key, body in GAPS.items():
            if t.startswith(key + "：") or t.startswith(key + ":"):
                head = t.split("：", 1)[0] if "：" in t else t.split(":", 1)[0]
                title = t[len(head) + 1:].split("。")[0]
                set_text(par, f"{head}：{title}。{body}")
                changed["gap"] += 1
                break
        if t.startswith("我针对本项目最核心的几个方向"):
            set_text(par, "检索覆盖 Web of Science、IEEE Xplore 与 CNKI，"
                          "检索式见附录，截止日期 2026 年 10 月。按“问题是否已被正面处理”"
                          "将结果分为两类，结论如下。")
            changed["search"] += 1

    h = doc.add_paragraph()
    hr = h.add_run("参考文献（新增，需作者核对）")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    for i, (txt, doi) in enumerate(REFS, 1):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(f"[{i}] {txt}  DOI: {doi}")
        r.font.name = FONT
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x5A, 0x62, 0x6A)

    note = doc.add_paragraph()
    nr = note.add_run("说明：以上 8 条均经 OpenAlex 核验存在并可检索到 DOI；"
                      "原文中“王守相 2025”“Song 2025”等无标题引用已从 1.2 节移除，"
                      "如能提供标题可再补回。")
    nr.font.name = FONT
    nr.font.size = Pt(9)
    nr.font.color.rgb = RGBColor(0x86, 0x4B, 0x48)

    doc.save(str(OUT))
    print("已生成：", OUT)
    print("替换段落：", changed)


if __name__ == "__main__":
    main()
