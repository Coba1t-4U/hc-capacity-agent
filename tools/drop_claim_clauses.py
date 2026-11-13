# -*- coding: utf-8 -*-
"""删除含“首次 / 显著 / 大幅”的断言分句，并逐条打印删除前→删除后，便于复核。

删除策略：
  1) 按句切分（。；！？）；命中的句子再按逗号切成分句
  2) 只删含关键词的那个分句；若删后整句不足 8 字，则整句删除
  3) 每次删除都记录原文与新文，输出到控制台与文末“改动记录”
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.shared import Pt, RGBColor

SRC = Path.home() / "Desktop" / "配网承载力" / "论文方案_完整修订版.docx"
OUT = SRC.with_name("论文方案_完整修订版_v2.docx")
FONT = "微软雅黑"
KEYS = ("首次", "显著", "大幅")


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[。；！？])", text) if s.strip()]


def clean_sentence(sent: str) -> tuple[str, bool]:
    """返回（新句子, 是否改动）。"""
    if not any(k in sent for k in KEYS):
        return sent, False
    # 主张句（含“本文”）：只去掉断言词，保留断言本身
    if "本文" in sent:
        ns = sent
        for k in KEYS:
            ns = ns.replace(k, "")
        return ns, True
    clause_sep = "，"
    clauses = sent.split(clause_sep)
    kept = [c for c in clauses if not any(k in c for k in KEYS)]
    new = clause_sep.join(kept)
    # 去掉可能出现的连续标点
    new = new.replace("，，", "，").strip()
    if len(new.strip("。；！？， ")) < 8:
        return "", True
    if not new.endswith(("。", "；", "！", "？")):
        new += sent[-1] if sent[-1] in "。；！？" else "。"
    return new, True


def set_text(par, text: str) -> None:
    if not par.runs:
        par.add_run(text)
        return
    par.runs[0].text = text
    for r in par.runs[1:]:
        r.text = ""


def main() -> None:
    doc = Document(str(SRC))
    log: list[tuple[str, str, str]] = []

    for par in doc.paragraphs:
        t = par.text
        if not any(k in t for k in KEYS):
            continue
        new_parts = []
        changed = False
        for s in sentences(t):
            ns, ch = clean_sentence(s)
            if ch:
                changed = True
                log.append(("删除", s.strip(), ns.strip() or "（整句删除）"))
            new_parts.append(ns)
        if changed:
            set_text(par, "".join(new_parts))

    h = doc.add_paragraph()
    hr = h.add_run("改动记录：删除的断言分句")
    hr.font.name = FONT
    hr.font.size = Pt(12)
    hr.bold = True
    for i, (_, old, new) in enumerate(log, 1):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        r1 = p.add_run(f"[{i}] 删除：{old}")
        r1.font.name = FONT
        r1.font.size = Pt(8.5)
        r1.font.color.rgb = RGBColor(0x86, 0x4B, 0x48)
        r2 = p.add_run(f"　→　保留：{new}")
        r2.font.name = FONT
        r2.font.size = Pt(8.5)
        r2.font.color.rgb = RGBColor(0x33, 0x33, 0x33)

    doc.save(str(OUT))
    print("已生成：", OUT)
    print("删除条数：", len(log))
    for old, new in [(x[1], x[2]) for x in log]:
        print("  -", old[:70])
        print("    →", new[:70])


if __name__ == "__main__":
    main()
