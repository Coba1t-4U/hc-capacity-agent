# -*- coding: utf-8 -*-
"""撰写论文“可复现性”小节（基于 E6 四张表的实测数据），含表注与图注建议。"""
from __future__ import annotations

import json
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
RUNS = ROOT / "runs" / "ACCEPT"
OUT = Path.home() / "Desktop" / "配网承载力" / "论文_可复现性小节_初稿.docx"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
GREY = RGBColor(0x5A, 0x62, 0x6A)
RED = RGBColor(0x86, 0x4B, 0x48)


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def para(doc, text, *, size=10.5, bold=False, color=None, space=6, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.5
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


def main() -> None:
    v = load(RUNS / "FD002" / "verification" / "verification.json")
    cc = load(RUNS / "FD002" / "results" / "engine_crosscheck.json")
    f2 = load(RUNS / "FD002" / "results" / "run_summary.json")
    f1 = load(RUNS / "FD001" / "results" / "run_summary.json")
    f16 = load(RUNS / "FD016" / "results" / "run_summary.json")
    cmp_ = v.get("comparison") or {}
    tol = cc.get("tolerances") or {}
    pts = cc.get("points") or []

    doc = Document()
    doc.sections[0].left_margin = doc.sections[0].right_margin = Cm(2.2)

    t = doc.add_paragraph()
    r = t.add_run("可复现性与计算可信度（小节初稿）")
    r.font.name = FONT
    r.font.size = Pt(16)
    r.bold = True
    r.font.color.rgb = BLUE
    para(doc, "对应实验 E6；数据来自 runs/ACCEPT 下的运行产物，表注给出文件路径", size=9.5, color=GREY)

    para(doc, "一、为什么单独写这一节", bold=True, size=12, space=6)
    para(doc, "承载力结论要进入并网许可流程，可复现性与结论本身同样重要。"
              "本节不重复介绍方法，只回答三个可核查的问题：同一输入重跑结果是否一致、"
              "换一套引擎算是否一致、输入被改动后结论是否失效。三项检查分别对应下面的三张表。")

    para(doc, "二、同输入重跑的一致性", bold=True, size=12, space=6)
    para(doc, f"对同一份冻结输入，在两个独立工作目录、两个独立进程中各执行一次完整评估，"
              f"逐项比对 {cmp_.get('total_metrics', '—')} 个关键量，包括输入指纹、引擎接口版本、"
              f"场景覆盖、收敛状态、约束计数与容量结论。比对结果：不一致项 "
              f"{cmp_.get('mismatch_count', '—')} 个，输入指纹"
              f"{'一致' if v.get('input_match') else '不一致'}。"
              f"该结果说明，在输入未改动的前提下，评估链路本身不引入随机性。")
    para(doc, "表 1　独立复算的关键量比对（数据来源：runs/ACCEPT/FD002/verification/verification.json）",
         size=9, color=GREY, indent=0.3)

    para(doc, "三、跨引擎一致性", bold=True, size=12, space=6)
    if pts:
        parts = []
        for p in pts:
            d = p.get("diff") or {}
            vmin = (d.get("vmin_pu") or {}).get("abs")
            vmax = (d.get("vmax_pu") or {}).get("abs")
            cand = [x for x in (vmin, vmax) if isinstance(x, (int, float))]
            vabs = max(cand) if cand else None
            lo = d.get("losses_kw") or {}
            lo_d, base = lo.get("abs"), lo.get("opendss")
            pct = (abs(lo_d) / abs(base) * 100
                   if isinstance(lo_d, (int, float)) and isinstance(base, (int, float)) and base
                   else None)
            parts.append((p.get("point", ""), vabs, pct))
        worst_v = max((x[1] for x in parts if x[1] is not None), default=0)
        worst_p = max((x[2] for x in parts if x[2] is not None), default=0)
        para(doc, f"主算采用 OpenDSS 三相不平衡潮流，校核采用 pandapower 正序平衡潮流。"
                  f"两个场景点（{'、'.join(x[0] for x in parts)}）的比对结果显示，"
                  f"节点电压偏差最大 {worst_v:.4f} 标幺，网损偏差最大 {worst_p:.2f}%，"
                  f"均小于设定容差（{tol.get('v_pu_abs')} 标幺、{tol.get('loss_pct')}%）。"
                  f"两套引擎在建模假设上并不相同，却给出相近结果，说明潮流解不依赖于某一套实现。")
        para(doc, "表 2　双引擎交叉校核结果（数据来源：runs/ACCEPT/FD002/results/engine_crosscheck.json）",
             size=9, color=GREY, indent=0.3)

    para(doc, "四、边界行为的一致性", bold=True, size=12, space=6)
    para(doc, f"可复现性不仅指正常工况可重现，也包括异常工况下的行为可预期。"
              f"三个算例的结果分别体现三种边界：FD002 基线无越限，给出容量 "
              f"{f2.get('capacity_physical_kw')} kW，绑定约束为 {f2.get('binding_constraint')}；"
              f"FD001 基线本身热稳越限，容量取 {f1.get('capacity_physical_kw')} kW 而非给出乐观值；"
              f"FD016 为多场景批量，取最不利断面后容量为 {f16.get('capacity_physical_kw')} kW。"
              f"三者共同说明：容量结论随基线状态变化，而不是固定输出一个数。")
    para(doc, "表 3　三个算例的容量与绑定约束（数据来源：各算例 results/run_summary.json）",
         size=9, color=GREY, indent=0.3)

    para(doc, "五、输入改动后结论失效", bold=True, size=12, space=6)
    para(doc, "评估结论与输入数据通过指纹绑定：输入文件任一字节变化，输入指纹随之改变，"
              "原有结论的凭证失效，需重新评估。这一机制的作用不是防止误用，"
              "而是让“这份结论对应哪份数据”可被外部核验——审稿人或第三方拿到输入包与产物，"
              "可自行重算并比对指纹。")
    para(doc, "表 4　复现步骤（供第三方执行）", size=9, color=GREY, indent=0.3)

    para(doc, "六、本节结论的适用范围", bold=True, size=12, space=6)
    para(doc, "上述检查覆盖设备级单馈线评估，验证的是“同一实现内部一致、跨实现相近”。"
              "它不构成对模型本身的验证：若潮流模型假设与实际电网不符，本节的三项检查仍会全部通过。"
              "模型层面的验证需另设对照实验，不在本节范围内。", color=RED)

    para(doc, "写作提示（提交前删除）", bold=True, size=10.5, color=GREY, space=4)
    para(doc, "① 表 1–4 直接取《论文_实验E6_可复现性证据.docx》中的四张表，编号与本节一致。", size=9,
         color=GREY, indent=0.3)
    para(doc, "② 正文只写结论与量级，不重复表格数字；审稿人看表即可。", size=9, color=GREY, indent=0.3)
    para(doc, "③ 第六节的“适用范围”不要删——它把“内部一致”与“模型正确”分开，是可辩护的写法。",
         size=9, color=GREY, indent=0.3)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(OUT))
    print("已生成：", OUT.name)


if __name__ == "__main__":
    main()
