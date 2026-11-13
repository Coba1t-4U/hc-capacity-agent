# -*- coding: utf-8 -*-
"""生成《实验计划：从方案到可投稿》，把附录 D 的缺口转成可执行任务。

设计前提（按现有条件）：
  · 工具只有 OpenDSS + pandapower（无 PSASP / PSD-BPA）
  · 无实测区域数据，可构造算例并在文中标注
  · 已有可复现机制（独立复算 + 双引擎互校）可直接整理成实验
"""
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

OUT = Path.home() / "Desktop" / "配网承载力" / "论文_实验计划_从方案到可投稿.docx"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)

EXPS = [
    ("E1", "三层算例构造（主—配—微）", "创新点 1、3",
     "构造一个含主网等值、配网馈线、微网单元的三层算例，作为全部实验的统一基座",
     "IEEE 123 节点（配网层）+ 主网等值源 + 2 个微网单元（储能+光伏）",
     "OpenDSS 建模；潮流失配网层，主网用等值阻抗/戴维南",
     "三层算例可收敛；母线数、总负荷、电源容量表",
     "潮流收敛且节点电压在 0.95–1.05 pu；三层容量口径可互相换算",
     "2 周", "IEEE 123 无微网结构 → 自行增加微网单元，文中注明为改造算例"),
    ("E2", "非加性对照与误差界校核", "创新点 1（核心）",
     "验证“逐级裁剪 / 简单求和 / 边界聚合”三法给出的可开放容量不同，并给出误差界",
     "E1 的三层算例，构造 3 组场景：单点接入、两点接入、三点接入",
     "三种算法各算一遍（逐级裁剪、直接求和、本文边界聚合）",
     "三法容量值、相对偏差、误差界上界与实际偏差",
     "偏差可复现；实测偏差 ≤ 理论误差界；单点 vs 多点差异随耦合强度单调变化",
     "2 周", "边界聚合实现复杂 → 先做两点接入的解析特例，再推广"),
    ("E3", "跨层 DRO 与单层 DRO 对比", "创新点 2",
     "证明跨层级风险语义一致处理会改变容量结论（否则创新点 2 无必要性）",
     "E1 算例 + 三层不同样本长度的历史场景（可构造：短样本 200、中 1000、长 5000）",
     "Wasserstein 球（L1 与 L∞ 联合约束），单层 vs 分层各求一次容量",
     "容量值、置信水平 90/95/99% 下的容量区间、求解时间",
     "同一置信水平下两法容量差异 > 3%，且分层法在小样本层更保守",
     "2 周", "无真实长序列 → 用构造场景并在文中注明；差异不显著则改述为“方法必要性讨论”"),
    ("E4", "与投影类方法的对比", "创新点 3（核心）",
     "对照 Yang 2026 的可行域投影思路，说明“下层可行域内生”时该法不直接适用",
     "E1 算例；按其原文思路实现简化版投影（一次性投影，不随容量决策更新）",
     "两法各求容量；记录投影法在容量变化时的重投影次数",
     "容量差异、重投影次数、迭代次数、耗时",
     "本文法迭代次数显著少于“每次重投影”方案；容量结论一致（差 ≤2%）",
     "2 周", "原文算法细节不全 → 只实现其“一次性投影”简化版，文中说明为简化复现"),
    ("E5", "分解算法收敛性实验", "创新点 3",
     "给出迭代映射的收敛证据（不动点存在性、收敛速度）",
     "E1 算例 + 不同耦合强度（联络线容量上限 3 档）",
     "记录每次迭代的上下层边界差、目标值",
     "迭代次数、残差曲线、是否振荡",
     "3 档耦合强度下均收敛；迭代次数 ≤ 20；无持续振荡",
     "1 周", "出现振荡 → 加入松弛因子并报告，作为算法改进点"),
    ("E6", "可复现性实验（已具备，需整理）", "创新点 4",
     "把智能体已有的复算与互校证据整理成论文可用的实验表",
     "现成产物：FD002/FD001/FD016 运行目录",
     "八项关键量比对、OpenDSS×pandapower 互校、口径绑定失效演示",
     "八项差异、电压/网损容差、凭证失效触发条件",
     "八项均一致；电压差 ≤0.03 pu、网损差 ≤10%；改数据后凭证失效",
     "1 周", "无需新算例，仅整理；注意脱敏（不含客户信息）"),
]

MILESTONES = [
    ["阶段", "周次", "内容", "交付物", "可投稿性"],
    ["M1", "W1–W2", "E1 三层算例；E6 整理现有证据", "算例文件 + 复现实验表", "可投短文（工程实现）"],
    ["M2", "W3–W4", "E2 非加性对照与误差界", "三法对比表 + 误差界图", "创新点 1 立住"],
    ["M3", "W5–W6", "E3 跨层 DRO 对比", "单层 vs 分层容量对比", "创新点 2 立住"],
    ["M4", "W7–W8", "E4 投影法对比；E5 收敛性", "对比表 + 收敛曲线", "创新点 3 立住，达一区投稿门槛"],
]

RISKS = [
    ["风险", "触发条件", "应对"],
    ["三层算例构造被质疑“人造”", "审稿人要求实测数据",
     "文中明确标注改造来源；补充 IEEE 123 原始算例的对照结果，证明方法与算例无关"],
    ["非加性差异不显著", "E2 中三法偏差 <2%",
     "提高耦合强度（缩短联络线容量、增大分布式渗透率）；仍不显著则收窄主张为“特定耦合条件下”"],
    ["DRO 对比差异小", "E3 中差异 <3%",
     "改述为“方法必要性讨论”，把创新点 2 降级为分析性贡献，不主张容量提升"],
    ["投影法无法复现", "E4 无原文细节",
     "只做“一次性投影”简化实现，并明确声明为简化复现，不做逐位对比"],
    ["时间不足", "W6 仍未完成 E3",
     "按 M1+M2 投工程类期刊（如 MPCE），理论部分留待下一稿"],
]

MIN_SET = [
    ["最小可投稿集", "支撑内容", "预计周次"],
    ["E1 + E6", "算例与可复现机制", "3 周"],
    ["E1 + E2 + E6", "创新点 1、4 + 算例", "5 周"],
    ["E1 + E2 + E3 + E6", "创新点 1、2、4", "7 周"],
    ["全部六项", "四条创新点齐备，达一区门槛", "8 周"],
]


def para(doc, text, *, size=10.5, bold=False, color=None, space=5, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.35
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def table(doc, rows, widths, size=8):
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
            r = c.paragraphs[0].add_run(val)
            r.font.name = FONT
            r.font.size = Pt(size)
            r.bold = (i == 0)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build() -> Path:
    doc = Document()
    doc.sections[0].left_margin = doc.sections[0].right_margin = Cm(2.0)

    _t = doc.add_paragraph()
    _t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _r = _t.add_run("实验计划：从方案到可投稿")
    _r.font.name = FONT; _r.font.size = Pt(19); _r.bold = True
    _r.font.color.rgb = BLUE
    para(doc, "六个实验 · 八周 · 明确每周交什么、达不到怎么办", size=10.5,
         color=GREY, space=12)

    para(doc, "一、这份计划解决什么问题", size=13, bold=True, color=BLUE, space=6)
    para(doc, "方案现在的方法与表述已基本成形，短板在验证：三个创新点都有“方法”，但都没有“实验”。"
              "审稿人不会因为数学定义漂亮就接受结论，需要看到三件事——"
              "① 与现有方法的差异能被数值复现；② 差异不是算例偶然；③ 结果可复现。"
              "本计划按这三件事拆成六个实验。")

    para(doc, "二、前提假设（不满足则计划需调整）", size=13, bold=True, color=BLUE, space=6)
    para(doc, "1. 计算工具限于 OpenDSS 与 pandapower，不使用 PSASP / PSD-BPA。", size=10)
    para(doc, "2. 无区域实测数据，实验以公开算例与构造算例为基础，并在文中明确标注。", size=10)
    para(doc, "3. 已有可复现机制（独立复算、双引擎互校）可直接转为实验证据。", size=10)
    para(doc, "4. 单人投入，每周可用时间按 20 小时计。", size=10)

    para(doc, "三、六个实验", size=13, bold=True, color=BLUE, space=6)
    for code, name, target, goal, data, method, metrics, accept, dur, risk in EXPS:
        para(doc, f"{code}　{name}（对应{target}，{dur}）", size=11, bold=True, space=3)
        para(doc, f"目的：{goal}", size=9.5, indent=0.4, space=2)
        para(doc, f"数据与算例：{data}", size=9.5, indent=0.4, space=2)
        para(doc, f"方法：{method}", size=9.5, indent=0.4, space=2)
        para(doc, f"输出指标：{metrics}", size=9.5, indent=0.4, space=2)
        para(doc, f"验收标准：{accept}", size=9.5, indent=0.4, space=2)
        para(doc, f"风险与退路：{risk}", size=9.5, indent=0.4, color=RED, space=6)

    para(doc, "四、里程碑", size=13, bold=True, color=BLUE, space=6)
    table(doc, MILESTONES, [1.6, 2.2, 5.6, 4.6, 4.2], size=8.5)

    para(doc, "五、最小可投稿集（按能拿出的时间选）", size=13, bold=True, color=BLUE, space=6)
    table(doc, MIN_SET, [5.0, 8.0, 3.0], size=9)
    para(doc, "建议：先做到 M2（5 周），此时创新点 1 与 4 有实验支撑，可投工程类期刊；"
              "再补 E3–E5 冲一区。不要等六个实验全做完再写，写作与实验应并行。", size=9.5,
         color=GREY)

    para(doc, "六、风险清单", size=13, bold=True, color=BLUE, space=6)
    table(doc, RISKS, [4.4, 4.6, 8.4], size=8.5)

    para(doc, "七、每周固定动作（避免计划漂移）", size=13, bold=True, color=BLUE, space=6)
    para(doc, "周一：跑当周实验，结果落到统一目录（含输入哈希与脚本版本）。", size=10)
    para(doc, "周三：把结果填进论文对应小节，不写“待补”，写不出结论就承认结论不成立。", size=10)
    para(doc, "周五：更新一张图表；图表编号在正文中被引用（没被引用的图不算成果）。", size=10)
    para(doc, "每周结束：在方案末尾追加三行——本周完成什么、卡在哪、下周是否调整计划。", size=10)

    para(doc, "八、完成标准（可投稿的判据）", size=13, bold=True, color=BLUE, space=6)
    para(doc, "① 三个创新点各有 ≥1 张实验表或曲线，且图注写明算例、参数与对比对象。", size=10)
    para(doc, "② 所有数值可从产物重跑得到；文中引用数据时给出产物文件名。", size=10)
    para(doc, "③ 结论段落的每一句，都能在实验表中找到对应行。", size=10)
    para(doc, "④ 与竞品（Yang 2026、Tan 2026）的差异有数值证据，而非文字断言。", size=10)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    return OUT


if __name__ == "__main__":
    p = build()
    print("已生成：", p)
    print(f"大小：{p.stat().st_size / 1024:.0f} KB")
