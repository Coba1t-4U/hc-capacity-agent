"""chunking.py — 把长文档切成可检索的片段。

## 为什么必须切分

整篇文档做检索有两个硬伤：
  1. 长文档里只有一个段落相关，但整篇被当作一个命中项，摘要会离题；
  2. 词频被全文稀释，短而精准的文档反而排不上来。

切分后每段自带标题路径（如"五类约束 / 电压约束"），
检索命中的是**具体段落**，回答时引用的也是具体段落。

## 切分策略

1. 按 Markdown 标题切成小节（保留标题层级，形成 breadcrumb）
2. 小节超长时按空行切成段落，再按目标长度贪心聚合
3. 相邻块留 overlap，避免把一句话从中间切断后两边都检索不到

表格行不切：一行就是一个完整事实，切开就没有意义了。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

#: 目标块长度（字符）。中文 450 字约等于 2~3 个自然段，检索粒度合适。
TARGET_CHARS = 450
#: 上限：超过就强制切，避免单块过大把词频稀释
MAX_CHARS = 900
#: 相邻块重叠字符数
OVERLAP_CHARS = 80
#: 小于这个长度的块丢弃（通常是孤立标点或空标题）
MIN_CHARS = 24

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    """一个检索单元。"""
    chunk_id: str
    doc_id: str
    text: str
    heading: str = ""              # 最近一级标题
    breadcrumb: str = ""           # 完整标题路径
    order: int = 0
    tokens: List[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        return self.breadcrumb or self.heading or self.doc_id

    def to_dict(self) -> dict:
        return {"chunk_id": self.chunk_id, "doc_id": self.doc_id,
                "heading": self.heading, "breadcrumb": self.breadcrumb,
                "order": self.order, "text": self.text}


def _split_sections(text: str) -> List[tuple]:
    """按标题切小节，返回 [(breadcrumb, heading, body)]。"""
    lines = text.splitlines()
    sections: List[tuple] = []
    stack: List[tuple] = []           # (level, title)
    buf: List[str] = []
    cur_head = ""

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        crumb = " / ".join(t for _, t in stack) if stack else ""
        if body:
            sections.append((crumb, cur_head, body))
        buf = []

    for line in lines:
        m = _HEADING.match(line.strip())
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur_head = title
        else:
            buf.append(line)
    flush()
    return sections


def _pack_paragraphs(paras: List[str], target: int = TARGET_CHARS,
                     hard: int = MAX_CHARS) -> List[str]:
    """把段落贪心聚合成目标长度的块，带重叠。"""
    out: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for p in paras:
        p = p.strip()
        if not p:
            continue
        plen = len(p)
        if cur_len and cur_len + plen + 1 > target:
            out.append("\n".join(cur))
            # 重叠：从上一块尾部取约 OVERLAP_CHARS 的内容带上
            tail = "\n".join(cur)[-OVERLAP_CHARS:]
            cur = [tail, p] if len(tail) > 20 else [p]
            cur_len = sum(len(x) + 1 for x in cur)
        else:
            cur.append(p)
            cur_len += plen + 1
        # 单段就超上限：硬切
        while cur_len > hard and len(cur) == 1:
            big = cur[0]
            out.append(big[:hard])
            cur = [big[hard - OVERLAP_CHARS:]]
            cur_len = len(cur[0])
    if cur:
        tail = "\n".join(cur).strip()
        if tail:
            out.append(tail)
    return out


def chunk_document(doc_id: str, text: str) -> List[Chunk]:
    """把一篇文档切成 Chunk 列表。"""
    chunks: List[Chunk] = []
    sections = _split_sections(text)
    if not sections:
        sections = [("", "", text)]
    for crumb, head, body in sections:
        # 表格行整块保留；其余按段落聚合
        paras = re.split(r"\n\s*\n", body)
        pieces = _pack_paragraphs([p for p in paras if p.strip()])
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < MIN_CHARS:
                continue
            idx = len(chunks)
            # 把标题并进正文，让检索能命中标题里的关键词
            prefix = ""
            if crumb:
                prefix = f"【{crumb}】\n"
            elif head:
                prefix = f"【{head}】\n"
            chunks.append(Chunk(
                chunk_id=f"{doc_id}#{idx}",
                doc_id=doc_id,
                text=prefix + piece,
                heading=head,
                breadcrumb=crumb,
                order=idx))
    # 全文太短导致没有块：整篇作为一个块，避免"有文档却检索不到"
    if not chunks:
        body = text.strip()
        if body:
            chunks.append(Chunk(chunk_id=f"{doc_id}#0", doc_id=doc_id,
                                text=body, heading="", breadcrumb="",
                                order=0))
    return chunks
