# -*- coding: utf-8 -*-
"""在修订版基础上做全篇语言清理，生成《完整修订版》。

只做“可自动判断”的改动，逐条记录，不碰技术主张：
  · 删除元话语（综上所述/值得注意的是/我们认为/让我们…）
  · 删除占位语（尚未收集/待后续补充/因时间所限…）
  · 软化无基线评价词（首次提出→提出；显著提升/大幅提高/国际领先→删除该分句）
  · 追加附录 A 检索式模板、附录 B 术语与口径表
改不动的（创新点映射、验证深度）列入“待人工处理清单”，不擅自改写。
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

SRC = Path.home() / "Desktop" / "配网承载力" / "论文方案-题目创新点与框架_修订版.docx"
OUT = SRC.with_name("论文方案_完整修订版.docx")
FONT = "微软雅黑"

META = re.compile(r"^(综上所述|由此可见|值得注意的是|需要注意的是|我们认为|我们需要|"
                  r"我认为|让我们|不难看出|显而易见)")
PLACEHOLDER = re.compile(r"(尚未收集|待后续补充|暂未开展|因时间所限|此处略|待补充数据|有待进一步收集)")
CLAIM_WORDS = [
    ("首次提出", "提出"),
    ("国际领先", ""),
    ("填补空白", ""),
    ("显著提升", ""),
    ("大幅提高", ""),
    ("大幅提升", ""),
    ("极大地", "明显地"),
]

LOG: list[tuple[str, str, str]] = []


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；！？])", text)
    return [p for p in parts if p.strip()]


def clean(text: str) -> str:
    out = []
    for s in split_sentences(text):
        body = s.strip()
        if not body:
            continue
        if META.match(body):
            LOG.append(("元话语", body[:40], "删除"))
            continue
        if PLACEHOLDER.search(body):
            LOG.append(("占位语", body[:40], "删除"))
            continue
        new = body
        for bad, good in CLAIM_WORDS:
            if bad in new:
                LOG.append(("评价词", bad, f"→{good or '（删除）'}"))
                new = new.replace(bad, good)
        if new.strip("。；！？ "):
            out.append(new)
    return "".join(out)


def set_text(par, text: str) -> None:
    if not par.runs:
        par.add_run(text)
        return
    par.runs[0].text = text
    for r in par.runs[1:]:
        r.text = ""


def add_block(doc, title: str, lines: list[str], size: float = 9.5) -> None:
    h = doc.add_paragraph()
    hr = h.add_run(title)
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    for ln in lines:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(ln)
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)


def main() -> None:
    doc = Document(str(SRC))
    touched = 0
    for par in doc.paragraphs:
        t = par.text
        if len(t.strip()) < 12:
            continue
        new = clean(t)
        if new != t:
            set_text(par, new)
            touched += 1

    add_block(doc, "附录 A　文献检索式与说明", [
        "A.1 检索平台：Web of Science 核心合集、IEEE Xplore、CNKI；检索截止日期 2026 年 10 月。",
        'A.2 英文检索式（示例）：TS=(("hosting capacity" OR "allowable capacity") AND '
        '("multi-level" OR "hierarchical" OR "transmission-distribution") AND '
        '("distribution network" OR "distribution system"))。',
        "A.3 中文检索式（示例）：主题=(承载力 AND (配电网 OR 配网) AND (分层 OR 多层级 OR 主配协同))。",
        "A.4 纳入标准：明确给出承载力边界、可开放容量或接入容量定量结果；含跨电压等级或分层结构。",
        "A.5 排除标准：仅讨论运行调度而未涉及容量边界；无定量算例的综述性短评。",
    ])

    add_block(doc, "附录 B　术语与口径对照表", [
        "B.1 hosting capacity：本文统一译为“承载力”，指在给定约束下可接纳的分布式电源最大容量。",
        "B.2 可开放容量：扣除已并网与已受理容量后的剩余接入空间；与“承载力”区分使用，不混用。",
        "B.3 层级：指主网、配网、微网三级；设备级与系统级为同一层级内的不同评价对象，不称“层级”。",
        "B.4 单位与基准：容量用 kW/MW，电压用标幺值（基准为各级额定电压），负载率用百分数。",
        "B.5 约束口径：电压偏差按 GB/T 12325，短路电流按 GB/T 15544，承载力分级按 DL/T 2041-2019。",
    ])

    add_block(doc, "待人工处理清单（自动化未覆盖）", [
        "H.1 创新点与三个空白的对应关系需逐条核对：每个空白是否都有“方法 + 验证手段”两项支撑。",
        "H.2 验证章节需补：对照基线、误差容限、算例规模；当前多为定性描述。",
        "H.3 凡出现性能提升比例处，须注明对照对象与实验条件；无基线者删除。",
        "H.4 参考文献 8 条仅核验“存在且可检索 DOI”，未被引结论是否与原文一致需逐篇核对。",
    ], size=9)

    doc.save(str(OUT))
    print("已生成：", OUT)
    print("语言层改动段落：", touched)
    print("改动条目：", len(LOG))
    from collections import Counter
    for k, v in Counter(x[0] for x in LOG).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
