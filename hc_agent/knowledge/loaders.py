"""loaders.py — 把各类文件读成纯文本。

支持：md / txt / markdown / csv / xlsx / docx / pdf。
**取不到就如实返回错误**，不返回空字符串假装成功 ——
否则知识库里会出现"看不见的空文档"。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Tuple

TEXT_SUFFIXES = (".md", ".txt", ".markdown", ".rst", ".log")
TABLE_SUFFIXES = (".csv", ".tsv")
EXCEL_SUFFIXES = (".xlsx", ".xlsm")
DOCX_SUFFIXES = (".docx",)
PDF_SUFFIXES = (".pdf",)

SUPPORTED_SUFFIXES = (TEXT_SUFFIXES + TABLE_SUFFIXES + EXCEL_SUFFIXES
                      + DOCX_SUFFIXES + PDF_SUFFIXES)

_MIME_HINT = {
    ".md": "Markdown 文档", ".txt": "纯文本", ".markdown": "Markdown 文档",
    ".csv": "表格", ".tsv": "表格", ".xlsx": "Excel 工作簿",
    ".docx": "Word 文档", ".pdf": "PDF 文档",
}


class ExtractError(Exception):
    """文件读不出来（缺依赖 / 格式不支持 / 内容为空）。"""


def _parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 --- 包裹的极简 front-matter。"""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    meta: Dict[str, Any] = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k == "tags":
            meta[k] = [t.strip() for t in v.strip("[]").split(",") if t.strip()]
        else:
            meta[k] = v
    return meta, body.lstrip("\n")


# ---------------------------------------------------------------- 各类型
def _read_text(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ExtractError("编码无法识别（试过 UTF-8 / GBK / Latin-1）")


def _read_csv(p: Path) -> str:
    raw = _read_text(p)
    delim = "\t" if p.suffix.lower() == ".tsv" else ","
    try:
        rows = list(csv.reader(io.StringIO(raw), delimiter=delim))
    except Exception as exc:
        raise ExtractError(f"CSV 解析失败：{exc}") from exc
    if not rows:
        raise ExtractError("表格为空")
    return _rows_to_text(rows)


def _rows_to_text(rows: List[List[Any]]) -> str:
    """把表格转成"列名: 值"的文本行，便于检索命中具体数值。"""
    if not rows:
        return ""
    header = [str(c or "").strip() for c in rows[0]]
    out: List[str] = []
    has_header = any(header)
    body = rows[1:] if has_header else rows
    for i, row in enumerate(body, start=1):
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue
        if has_header:
            parts = [f"{h}: {v}" for h, v in zip(header, cells) if h and v]
            out.append(f"第 {i} 行 " + "；".join(parts))
        else:
            out.append(" | ".join(c for c in cells if c))
    if has_header:
        out.insert(0, "列：" + "、".join(h for h in header if h))
    return "\n".join(out)


def _read_xlsx(p: Path) -> str:
    try:
        import openpyxl
    except ImportError as exc:
        raise ExtractError("缺少 openpyxl，无法读取 Excel") from exc
    try:
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
    except Exception as exc:
        raise ExtractError(f"Excel 打开失败：{exc}") from exc
    parts: List[str] = []
    try:
        for ws in wb.worksheets:
            parts.append(f"## 工作表 {ws.title}")
            rows: List[List[Any]] = []
            for r in ws.iter_rows(values_only=True):
                rows.append(list(r))
            txt = _rows_to_text(rows)
            parts.append(txt if txt.strip() else "（空表）")
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return "\n\n".join(parts)


def _read_docx(p: Path) -> str:
    try:
        import docx
    except ImportError as exc:
        raise ExtractError("缺少 python-docx，无法读取 Word") from exc
    try:
        d = docx.Document(str(p))
    except Exception as exc:
        raise ExtractError(f"Word 打开失败：{exc}") from exc
    parts: List[str] = []
    for para in d.paragraphs:
        t = (para.text or "").strip()
        if not t:
            continue
        style = (getattr(para.style, "name", "") or "")
        if style.startswith("Heading"):
            lvl = "".join(ch for ch in style if ch.isdigit()) or "1"
            parts.append("#" * min(6, int(lvl)) + " " + t)
        else:
            parts.append(t)
    for ti, table in enumerate(d.tables, start=1):
        parts.append(f"\n### 表 {ti}")
        for row in table.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractError(
            "缺少 pypdf，无法读取 PDF。可执行 pip install pypdf 后重试") from exc
    try:
        reader = PdfReader(str(p))
    except Exception as exc:
        raise ExtractError(f"PDF 打开失败：{exc}") from exc
    parts: List[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(f"## 第 {i} 页\n{t}")
    if not parts:
        raise ExtractError(
            "PDF 没有可提取的文本层（可能是扫描件，需要 OCR，本版不支持）")
    return "\n\n".join(parts)


def extract(path) -> Tuple[str, Dict[str, Any]]:
    """读取文件，返回 (正文, 元信息)。失败抛 ExtractError。"""
    p = Path(path)
    if not p.is_file():
        raise ExtractError(f"文件不存在：{p}")
    suffix = p.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        raw = _read_text(p)
        meta, body = _parse_front_matter(raw)
    elif suffix in TABLE_SUFFIXES:
        body, meta = _read_csv(p), {}
    elif suffix in EXCEL_SUFFIXES:
        body, meta = _read_xlsx(p), {}
    elif suffix in DOCX_SUFFIXES:
        body, meta = _read_docx(p), {}
    elif suffix in PDF_SUFFIXES:
        body, meta = _read_pdf(p), {}
    else:
        raise ExtractError(
            f"不支持 {suffix or '无扩展名'}；支持："
            + "、".join(SUPPORTED_SUFFIXES))
    if not body.strip():
        raise ExtractError("文件内容为空")
    meta = dict(meta or {})
    meta.setdefault("_kind", _MIME_HINT.get(suffix, suffix))
    meta.setdefault("_suffix", suffix)
    return body, meta
