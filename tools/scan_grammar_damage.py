# -*- coding: utf-8 -*-
"""扫描删词后产生的语法残缺，输出清单（不改文）。

检测项：
  1) 空引号 “” / ""
  2) 空括号 （） / ()
  3) 悬空虚词：的/是/在/与/和 紧接标点
  4) 重复标点：，， 。。 ；；
  5) 句中连续空格
"""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document

DOC = Path.home() / "Desktop" / "配网承载力" / "论文方案_完整修订版_v2.docx"
OUT = Path.home() / "Desktop" / "配网承载力" / "语法残缺清单.txt"

RULES = [
    ("空引号", re.compile(r"“\s*”|\"\s*\"")),
    ("空括号", re.compile(r"（\s*）|\(\s*\)")),
    ("悬空虚词", re.compile(r"[的是在与和把被][，。；：！？]")),
    ("重复标点", re.compile(r"[，。；：]{2,}")),
    ("句中双空格", re.compile(r"[\u4e00-\u9fa5]\s{2,}[\u4e00-\u9fa5]")),
]


def main() -> None:
    doc = Document(str(DOC))
    hits: list[tuple[int, str, str]] = []
    for i, par in enumerate(doc.paragraphs):
        t = par.text
        if not t.strip():
            continue
        for name, rx in RULES:
            for m in rx.finditer(t):
                s = max(0, m.start() - 22)
                e = min(len(t), m.end() + 22)
                hits.append((i, name, t[s:e].replace("\n", " ")))

    lines = [f"共 {len(hits)} 处", ""]
    for i, name, ctx in hits:
        lines.append(f"[段{i:03d}] {name}：…{ctx}…")
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"命中 {len(hits)} 处，清单已写：{OUT.name}")
    from collections import Counter
    for k, v in Counter(h[1] for h in hits).items():
        print(f"  {k}: {v}")
    for i, name, ctx in hits[:8]:
        print(f"   - [段{i:03d}] {name}: …{ctx[:60]}…")


if __name__ == "__main__":
    main()
