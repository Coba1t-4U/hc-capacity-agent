# -*- coding: utf-8 -*-
"""给汇报 PPT 补图：3 页智能体截图 + 业务流流程图 + 四层架构图 + 结果页证据图。

图全部用 pptx 形状绘制，不依赖外部绘图库；截图取 tests/_desktop_shots 的真实界面。
"""
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Cm, Pt

DESK = Path.home() / "Desktop" / "配网承载力"
ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
PPT = DESK / "配网承载力智能体_汇报15分钟.pptx"
SHOTS = ROOT / "tests" / "_desktop_shots"
FONT = "微软雅黑"
BLUE = RGBColor(0x01, 0x4C, 0x83)
DARK = RGBColor(0x1F, 0x24, 0x2B)
GREY = RGBColor(0x5A, 0x62, 0x6A)
LIGHT = RGBColor(0xEB, 0xF1, 0xF8)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
W, H = 33.87, 19.05


def tb(slide, x, y, w, h, text, size, bold=False, color=DARK,
       align=PP_ALIGN.LEFT, bg=None):
    if bg is not None:
        box = slide.shapes.add_shape(5, Cm(x), Cm(y), Cm(w), Cm(h))  # rounded rect
        box.fill.solid()
        box.fill.fore_color.rgb = bg
        box.line.color.rgb = BLUE
        box.line.width = Pt(1.0)
        tf = box.text_frame
        tf.word_wrap = True
    else:
        box = slide.shapes.add_textbox(Cm(x), Cm(y), Cm(w), Cm(h))
        tf = box.text_frame
        tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    return box


def arrow(slide, x1, y1, x2, y2):
    ln = slide.shapes.add_connector(1, Cm(x1), Cm(y1), Cm(x2), Cm(y2))
    ln.line.color.rgb = BLUE
    ln.line.width = Pt(1.6)


def blank(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bar = s.shapes.add_shape(1, Cm(0), Cm(0), Cm(W), Cm(0.28))
    bar.fill.solid()
    bar.fill.fore_color.rgb = BLUE
    bar.line.fill.background()
    return s


def head(s, title):
    tb(s, 1.6, 0.9, 30.5, 1.6, title, 26, True, BLUE)
    ln = s.shapes.add_shape(1, Cm(1.6), Cm(2.55), Cm(3.2), Cm(0.10))
    ln.fill.solid()
    ln.fill.fore_color.rgb = BLUE
    ln.line.fill.background()


def note_slide(s, text):
    s.notes_slide.notes_text_frame.text = text


def add_shot(prs, title, img, caption, note):
    s = blank(prs)
    head(s, title)
    pic = s.shapes.add_picture(str(img), Cm(1.8), Cm(3.2), height=Cm(13.1))
    tb(s, 1.8, 16.9, 30.2, 1.2, caption, 15, False, GREY)
    note_slide(s, note)


def add_flow(prs, note):
    s = blank(prs)
    head(s, "业务流：八段闭环（现状：1 段已实现 / 6 段部分实现 / 1 段未实现）")
    steps = [("① 基础数据收集与治理", "算例接入\n字段校验", "部分"),
             ("② 评估边界与约束设定", "五类约束\n规则包", "部分"),
             ("③ 承载力计算分析", "容量搜索\n独立复算", "部分"),
             ("④ 结果分级（红黄绿）", "双口径容量\n等级判定", "已实现"),
             ("⑤ 结果发布与公示", "对外口径\n另行生成", "未实现"),
             ("⑥ 业务应用（接入管理）", "接入校核\n基线/增量", "部分"),
             ("⑦ 提升措施与改造", "按约束\n给方向", "部分"),
             ("⑧ 动态更新与闭环", "数据变更\n重跑比对", "部分")]
    x0, y0, bw, bh, gx, gy = 1.5, 4.2, 7.2, 3.6, 0.55, 2.2
    pos = []
    for i, (name, sub, st) in enumerate(steps):
        col, row = i % 4, i // 4
        x = x0 + col * (bw + gx)
        y = y0 + row * (bh + gy)
        color = DARK if st != "未实现" else RGBColor(0x86, 0x4B, 0x48)
        tb(s, x, y, bw, bh, f"{name}\n{sub}", 13, False, color,
           PP_ALIGN.CENTER, LIGHT)
        tb(s, x, y + bh + 0.05, bw, 0.7, st, 10, False,
           GREY if st != "未实现" else RGBColor(0x86, 0x4B, 0x48), PP_ALIGN.CENTER)
        pos.append((x, y))
    for i in range(3):
        arrow(s, pos[i][0] + bw, pos[i][1] + bh / 2, pos[i + 1][0], pos[i + 1][1] + bh / 2)
    arrow(s, pos[3][0] + bw / 2, pos[3][1] + bh, pos[3][0] + bw / 2, pos[4][1])
    for i in range(4, 7):
        arrow(s, pos[i][0] + bw, pos[i][1] + bh / 2, pos[i + 1][0], pos[i + 1][1] + bh / 2)
    tb(s, x0, 17.6, 30.2, 0.9,
       "⑤ 未实现＝对外发布口径，需按省公司规范另行生成；其余各段部分实现，缺的是多对象与逐级汇总",
       12, False, RGBColor(0x86, 0x4B, 0x48))
    note_slide(s, note)


def add_arch(prs, note):
    s = blank(prs)
    head(s, "智能体架构：四层分工，数值只在引擎层产生")
    layers = [("决策与表达层", "问答 · 报告 · 结论分层（结论/关键结果/依据）· 不产生电气数值"),
              ("校验与证据层", "五类约束逐条判定 · 九项验收四态 · 独立复算 · 证据哈希"),
              ("仿真引擎层", "OpenDSS 0.9.4 主算（三相不平衡）· pandapower 3.5.6 交叉校核"),
              ("感知与输入层", "算例接入 · 字段单位校验 · 数据快照冻结 · 规则包与审批号")]
    y = 3.6
    for name, sub in layers:
        tb(s, 3.0, y, 8.6, 2.5, name, 18, True, WHITE, PP_ALIGN.CENTER, BLUE)
        tb(s, 12.0, y, 19.0, 2.5, sub, 15, False, DARK, PP_ALIGN.CENTER, LIGHT)
        y += 3.1
    arrow(s, 17.8, 15.6, 17.8, 16.4)
    tb(s, 1.6, 16.6, 30, 1.2,
       "硬规则：电气数值只来自引擎，模型不产生也不修改；每条结论都能回链到产物文件",
       14, True, RGBColor(0x86, 0x4B, 0x48))
    note_slide(s, note)


def main():
    prs = Presentation(str(PPT))
    # 幂等：上次已插入的 5 页先删掉，避免重复
    if len(prs.slides) >= 23:
        xml = prs.slides._sldIdLst
        for sid in list(xml)[-5:]:
            prs.part.drop_rel(sid.rId)
            xml.remove(sid)
    add_shot(prs, "智能体界面（一）：问答与配置",
             SHOTS / "59_chat.png",
             "问答页：结论 / 关键结果 / 依据 三层；配置页可查引擎版本与规则包",
             "这是问答页。问题用一句话问，回答分三层：结论、关键结果、依据。"
             "左下角配置页能看到引擎版本、规则包版本和审批号——评审问“你按哪版判据算的”，"
             "这一页就是答案。")
    add_shot(prs, "智能体界面（二）：结果与证据",
             SHOTS / "62_results.png",
             "结果页：双口径容量、绑定约束定位到相、九项验收四态",
             "这是结果页，也是整个系统最核心的一页。左边是容量结论和绑定约束，"
             "能定位到具体节点和相别；右边是九项验收矩阵，四种状态：达标、未达标、无数据、不适用。"
             "报告就是从这里一键生成的。")
    add_shot(prs, "智能体界面（三）：运算过程与算例分析",
             SHOTS / "66_workbench.png",
             "九阶段流水线：冻结输入 → 基线 → 约束 → 搜索 → 复算 → 报告 → 封存",
             "这一页是给复核的人看的：九阶段流水线，每一步有状态和耗时。"
             "哪一步慢、哪一步失败，直接看得出来，而不是“算不出来”四个字。"
               "搜索之后还有一步独立复算，八项关键量逐项比对。")
    add_flow(prs, "这是业务流闭环。八段：数据准备、评估计算、分级发布、接入应用、提升改造、动态更新，"
                  "更新后回到数据准备。我们对照做了覆盖评估：一段完整实现、六段部分实现、一段未实现。"
                  "未实现的是结果发布与公示——那是对外口径，要按省公司规范另行生成，我们没有自己定阈值。")
    add_arch(prs, "这是架构。四层：最下面感知与输入，管算例接入和字段校验；往上是仿真引擎层，"
                  "OpenDSS 主算、pandapower 交叉校核；再往上是校验与证据层，五类约束逐条判定、"
                  "九项验收、独立复算、证据哈希；最上面是决策与表达层，负责问答和报告。"
                  "请特别注意一条硬规则：电气数值只在引擎层产生，上面两层不碰数字。")
    prs.save(str(PPT))
    print("已插入图片页，总页数：", len(Presentation(str(PPT)).slides))


if __name__ == "__main__":
    main()
