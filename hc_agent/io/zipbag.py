"""zipbag.py — 把压缩包 / 文件夹"摊开"成可入库的文档与可分析的算例。

## 为什么要这个

用户经常手上就一个 zip：「参考资料.zip」「某个算例.zip」。让他先解压、
再把文件一个个上传、再单独加算例，是三次操作。这里一次完成：

  拖进 zip → 列出里面有什么 → 能当知识的文本入库 → 含 .dss 就登记成算例
  → 如实报告"导入了什么、跳过了什么"

## 关键原则：**不假装全能**

压缩包里有什么就说什么：
  * 文本型文档（md/txt/csv/docx/pdf/xlsx）→ 抽正文入库
  * `.dss` → 解压成算例目录并登记
  * exe/dll/pyc/图片等 → 一律**跳过并计数**，不能当作可用数据
  * 加密/损坏的包 → 明确报错，不静默返回空
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

#: 可以抽正文的扩展名（与 knowledge.loaders 保持一致）
DOC_EXTS = (".md", ".txt", ".markdown", ".rst", ".log", ".csv", ".tsv",
            ".xlsx", ".xlsm", ".docx", ".pdf")

#: 明确不当知识的（出现时只计数）
BINARY_EXTS = (".exe", ".dll", ".pyd", ".pyc", ".pyo", ".so", ".dylib",
               ".bin", ".dat", ".obj", ".o", ".a", ".lib", ".png", ".jpg",
               ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".mp4", ".mp3",
               ".wav", ".zip", ".7z", ".rar", ".gz", ".tar")

#: 单个文档入库上限（防止一个 200MB 的 pdf 把索引线程拖死）
MAX_DOC_BYTES = 12 * 1024 * 1024
#: 单次最多入库多少篇
MAX_DOCS = 60


def _safe_name(info: zipfile.ZipInfo) -> str:
    """防目录穿越：把 ../ 之类去掉。"""
    name = info.filename.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def inspect(path) -> Dict[str, Any]:
    """看一个压缩包里有什么，不解压。"""
    p = Path(str(path))
    out: Dict[str, Any] = {"path": str(p), "name": p.name, "kind": "zip",
                           "ok": False, "docs": [], "dss": [],
                           "skipped": [], "entries": 0, "uncompressed": 0,
                           "error": ""}
    try:
        with zipfile.ZipFile(p) as z:
            infos = z.infolist()
            out["entries"] = len(infos)
            out["uncompressed"] = sum(i.file_size for i in infos)
            for info in infos:
                if info.is_dir():
                    continue
                name = _safe_name(info)
                if not name:
                    continue
                ext = Path(name).suffix.lower()
                if ext in DOC_EXTS:
                    out["docs"].append({"name": name, "size": info.file_size})
                elif ext == ".dss":
                    out["dss"].append({"name": name, "size": info.file_size})
                else:
                    out["skipped"].append({"name": name, "size": info.file_size,
                                           "why": ("二进制/不可作知识"
                                                   if ext in BINARY_EXTS
                                                   else f"不支持的格式 {ext or '(无扩展名)'}")})
        out["ok"] = True
    except zipfile.BadZipFile:
        out["error"] = "不是有效的 zip（可能已损坏或被加密）"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def extract_docs(path, *, max_docs: int = MAX_DOCS,
                 max_bytes: int = MAX_DOC_BYTES) -> Tuple[List[Dict[str, Any]],
                                                          List[Dict[str, Any]]]:
    """从压缩包里抽出可入库文档的正文。

    返回 (成功列表, 失败列表)。失败列表里带原因，界面要如实显示。
    抽正文失败（扫描版 pdf / 加密 docx）也会进失败列表，不会被当成"空文档"入库。
    """
    from hc_agent.knowledge import loaders

    ok: List[Dict[str, Any]] = []
    bad: List[Dict[str, Any]] = []
    p = Path(str(path))
    try:
        z = zipfile.ZipFile(p)
    except Exception as exc:
        return [], [{"name": p.name, "why": f"打不开：{exc}"}]
    with z:
        infos = [i for i in z.infolist()
                 if not i.is_dir() and _safe_name(i)
                 and Path(_safe_name(i)).suffix.lower() in DOC_EXTS]
        infos.sort(key=lambda i: (-i.file_size, _safe_name(i)))
        for info in infos[:max_docs]:
            name = _safe_name(info)
            if info.file_size > max_bytes:
                bad.append({"name": name,
                            "why": f"文件过大（{info.file_size / 1e6:.1f} MB "
                                   f"> {max_bytes / 1e6:.0f} MB 上限）"})
                continue
            tmp = None
            try:
                import tempfile
                fd, tmp_name = tempfile.mkstemp(
                    suffix=Path(name).suffix.lower())
                tmp = Path(tmp_name)
                with z.open(info) as src:
                    data = src.read(max_bytes + 1)
                with open(fd, "wb") as fh:      # type: ignore[arg-type]
                    fh.write(data)
                text, meta = loaders.extract(tmp)
                text = (text or "").strip()
                if not text:
                    bad.append({"name": name, "why": "抽不到正文（可能是扫描件）"})
                else:
                    ok.append({"name": name, "text": text,
                               "chars": len(text), "meta": meta})
            except Exception as exc:
                bad.append({"name": name,
                            "why": f"{type(exc).__name__}: {exc}"[:160]})
            finally:
                if tmp is not None:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
        if len(infos) > max_docs:
            for info in infos[max_docs:]:
                bad.append({"name": _safe_name(info),
                            "why": f"超过单次上限 {max_docs} 篇，未处理"})
    return ok, bad


def extract_dss(path, dest_root) -> Optional[Path]:
    """把压缩包里的 .dss 算例解出来，返回算例目录（没有则 None）。

    只解 .dss 与 .txt（Master.dss 常用 Redirect 引入子文件，
    漏了子文件算例就缺元件）。目录结构按压缩包内的相对路径保留。
    """
    p = Path(str(path))
    root = Path(str(dest_root))
    try:
        z = zipfile.ZipFile(p)
    except Exception:
        return None
    with z:
        targets = [i for i in z.infolist()
                   if not i.is_dir() and _safe_name(i)
                   and Path(_safe_name(i)).suffix.lower() in (".dss", ".txt")]
        if not targets:
            return None
        stem = "".join(c if (c.isalnum() or c in "-_") else "_"
                       for c in p.stem)[:40] or "dropped"
        out_dir = root / stem
        k = 2
        while out_dir.exists():
            out_dir = root / f"{stem}-{k}"
            k += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        for info in targets:
            name = _safe_name(info)
            if not name:
                continue
            target = out_dir / name
            # 再次防穿越：解析后必须仍在 out_dir 内
            try:
                target.resolve().relative_to(out_dir.resolve())
            except Exception:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src:
                target.write_bytes(src.read())
    return _locate_master_dir(out_dir)


def _locate_master_dir(root: Path) -> Path:
    """在解压出来的目录里找到**真正装着算例**的那一层。

    压缩包常把算例放在子目录里（如 `case/Master.dss`），
    如果直接返回解压根目录，登记表会因为顶层没有 .dss 而认为"不是算例"，
    表现为"拖进去了但没登记上"（实测踩过）。

    优先取含 `Master.dss` 的目录；否则取 .dss 文件最多的目录。
    """
    try:
        cands = [p.parent for p in root.rglob("*")
                 if p.is_file() and p.name.lower() == "master.dss"]
        if cands:
            return sorted(cands, key=lambda d: len(str(d)))[0]
        best: Optional[Path] = None
        best_n = 0
        for d in [root] + [p for p in root.rglob("*") if p.is_dir()]:
            n = len(list(d.glob("*.dss"))) + len(list(d.glob("*.DSS")))
            if n > best_n:
                best, best_n = d, n
        return best or root
    except Exception:
        return root
