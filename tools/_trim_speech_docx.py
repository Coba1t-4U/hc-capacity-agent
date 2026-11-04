# -*- coding: utf-8 -*-
"""在生成的 docx 上做句子级删减，把讲稿从 2223 字压到约 1850 字（8 分钟）。

为什么在 docx 上删而不是改生成脚本：
  讲稿正文在脚本里是多行字符串拼接，按源码做精确替换容易错位；
  在 docx 上按「整句」删除更可控，改完能立刻数出每段字数。
只删展开说明的句子，**不动任何数字、界面对应关系和段落结构**。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document

DOC = Path(r"C:\Users\Chen\Desktop\配网承载力\配网承载力智能体_演示讲稿.docx")

#: 要整句删掉的片段（正则，容忍标点差异）
CUTS = [
    r"，也能按页面看细节。",
    r"模型可以说“被电压卡住”，但\s*1\.1008\s*这个数必须出自引擎。",
    r"，左下是知识库与配置",
    r"口径不跟结果固化，三个月后就说不清用的哪一版。",
    r"公共连接点与单个用户限值也不同。",
    r"过去的常见情况是算完、报告写完才发现单位错了；",
    r"系统会区分“算了一次潮流”和“出了承载力结论”。",
    r"但真正有用的是下一句。",
    r"只给一个容量数字，这个判断做不出来。",
    r"DL/T\s*2041\s*对系统级与设备级承载力的区分，就是本系统口径的来源。",
    r"过去这一步最费时，也最容易被简化成“只算一个典型日”。",
    r"所以效率提升的本质是分工变化：人从逐次操作工具，转向维护口径、处理异常、审核结论。",
    r"最后说清能做什么、不能做什么。",
]


def spoken(doc: Document) -> tuple[int, dict[str, int]]:
    inside = False
    total = 0
    per: dict[str, int] = {}
    cur = ""
    for p in doc.paragraphs:
        t = p.text.strip()
        if t.startswith("二、讲稿"):
            inside = True
            continue
        if t.startswith("三、备答"):
            break
        if not inside or not t or t.startswith("图") or t.startswith("（"):
            continue
        if t.startswith("【"):
            cur = t.split("】")[0].replace("【", "")
            per[cur] = 0
            continue
        per[cur] = per.get(cur, 0) + len(t)
        total += len(t)
    return total, per


def main() -> int:
    doc = Document(str(DOC))
    before, per_before = spoken(doc)
    hits = 0
    for pat in CUTS:
        rx = re.compile(pat)
        for p in doc.paragraphs:
            if not rx.search(p.text):
                continue
            new_text = rx.sub("", p.text)
            if not p.runs:
                continue
            p.runs[0].text = new_text
            for r in p.runs[1:]:
                r.text = ""
            hits += 1
            break

    doc.save(str(DOC))

    doc2 = Document(str(DOC))
    after, per_after = spoken(doc2)
    print(f"  命中句数: {hits}")
    print(f"  字数: {before} → {after}（删 {before - after}）")
    print(f"  估算时长: {after / 230:.1f} 分钟（230 字/分）")
    print()
    for k in per_before:
        a, b = per_before.get(k, 0), per_after.get(k, 0)
        flag = "" if b <= a else "  ← 变长了？"
        print(f"  {k:<24} {a:>4} → {b:>4}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
