# -*- coding: utf-8 -*-
"""把 E1/E2 的实际进展与失败结论写回实验计划（附录 G），使计划反映真实状态。"""
from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor

SRC = Path.home() / "Desktop" / "配网承载力" / "论文_实验计划_从方案到可投稿.docx"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
RED = RGBColor(0x86, 0x4B, 0x48)
GREY = RGBColor(0x5A, 0x62, 0x6A)


def para(doc, text, *, size=10, bold=False, color=None, space=4, indent=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    p.paragraph_format.line_spacing = 1.3
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.bold = bold
    if color is not None:
        r.font.color.rgb = color
    return p


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


def main() -> None:
    doc = Document(str(SRC))

    h = doc.add_paragraph()
    hr = h.add_run("附录 G　进展记录与风险更新（2026-11-12）")
    hr.font.name = FONT
    hr.font.size = Pt(13)
    hr.bold = True
    hr.font.color.rgb = BLUE

    para(doc, "记录原则：失败的实验也写进来，并写清“卡在哪一步、下一步依赖什么”。"
              "计划只有包含失败记录，才具备指导作用。", size=9.5, color=GREY)

    para(doc, "G.1 里程碑实际状态", bold=True, size=11, space=4)
    table(doc, [
        ["阶段", "计划", "实际", "说明"],
        ["M1", "E1 三层算例 + E6 证据整理", "E6 完成；E1 未通过",
         "E6 四张表可直接进论文；E1 数值不可用"],
        ["M2", "E2 非加性对照", "实验已跑，结论不可用",
         "受引擎缩放能力限制，判定依据不成立"],
    ], [1.8, 6.0, 5.0, 6.0])

    para(doc, "G.2 E1（三层算例）失败记录", bold=True, size=11, space=4)
    para(doc, "已排除并修复：", size=9.5, bold=True)
    para(doc, "① 变压器参数名冲突：本机 OpenDSS 不接受 %Rs / %Xhl，已改用默认阻抗；", size=9.5, indent=0.4)
    para(doc, "② 储能参数名错误：kVArated 不被识别，已移除；", size=9.5, indent=0.4)
    para(doc, "③ 电源母线接错：高压侧误接电路名同名母线，改接 sourcebus 后电压恢复正常；", size=9.5, indent=0.4)
    para(doc, "④ 母线基准未定义：VoltageBases/CalcVoltageBases 需在元件之后执行。", size=9.5, indent=0.4)
    para(doc, "仍未解决：", size=9.5, bold=True, color=RED)
    para(doc, "1000 kW 负荷在主网侧仅体现 697.4 kW（差 30%），已排除电压因素（两侧均 0.999）。"
              "可能为变压器容量/变比与负荷基准组合问题，未定位。", size=9.5, indent=0.4, color=RED)
    para(doc, "结论：E1 算例文件与结构可用，数值不可用于论文；下一步改用 FD002 已验证输入包改造，"
              "不再从零拼 DSS 文本。", size=9.5, indent=0.4)

    para(doc, "G.3 E2（加性检验）失败记录", bold=True, size=11, space=4)
    para(doc, "实测：A（单点）=445.3125 kW，B（单点）=445.3125 kW，C（双点联合）=445.3125 kW，"
              "比值恰为 0.5000。", size=9.5, indent=0.4)
    para(doc, "判定：该结果不构成非加性证据。原因是变体构造按节点均分总装机，而引擎按整场站统一缩放，"
              "三个场景的总装机相同，越限点自然相同。", size=9.5, indent=0.4, color=RED)
    para(doc, "前置条件：需引擎支持“按节点独立控制参与搜索的光伏容量”，否则 E2、E3 均无法得到干净结论。",
         size=9.5, indent=0.4)

    para(doc, "G.4 风险清单更新（新增两条）", bold=True, size=11, space=4)
    table(doc, [
        ["编号", "风险", "影响", "应对"],
        ["R6", "OpenDSS 用法不确定：参数名、母线基准、功率平衡需逐项验证",
         "E1 及后续所有自建算例可能反复返工", "以 FD002 已验证输入包为模板改造；每步加最小验证"],
        ["R7", "容量搜索只支持整场站统一缩放", "E2/E3 的边际与分层对比做不了",
         "给 pv 表加 scale_group 列，仅缩放指定组；这是 E2/E3 的共同前置"],
    ], [1.4, 6.4, 4.6, 6.6])

    para(doc, "G.5 计划调整建议", bold=True, size=11, space=4)
    para(doc, "1. 优先级改为：先补 R7 的引擎能力（1–2 天），再重跑 E2；", size=9.5, indent=0.4)
    para(doc, "2. E1 延后，且改为“FD002 加一层主网等值 + 一层微网”的渐进改造；", size=9.5, indent=0.4)
    para(doc, "3. E6 已完成，可先写“可复现性”小节，不等其他实验；", size=9.5, indent=0.4)
    para(doc, "4. 若 R7 短期无法实现，E2 退化为“定性讨论 + 单点算例对比”，并在文中说明局限。",
         size=9.5, indent=0.4)

    doc.save(str(SRC))
    print("已写回：", SRC.name)
    print("新增：附录 G（里程碑状态 / E1 记录 / E2 记录 / R6 R7 / 计划调整）")


if __name__ == "__main__":
    main()
