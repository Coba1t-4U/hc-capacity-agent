"""dropin.py — 拖入文件/压缩包后到底做什么。

把"拖进来之后发生什么"集中在这里，界面只负责拿到路径并展示结果：

  * 压缩包（.zip）→ 文本类文档入知识库；含 .dss 就解出算例并登记
  * 单个文档（md/txt/csv/docx/pdf/xlsx）→ 入知识库
  * 目录 → 若含 .dss 当作算例登记；否则把里面的文档批量入知识库
  * `.dss` 文件 → 登记成算例

**每一条结果都要能说清"导入了什么、跳过了什么、为什么"**，
不能只回一句"完成"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from hc_agent.io import zipbag

#: 拖进来的东西入库到哪个知识库（没有就建一个）
DROP_KB_NAME = "拖入资料"


def _kb(service=None):
    """取（或建）"拖入资料"知识库。

    `create_kb()` 返回 {"ok":..., "kb": {...}}，不是直接的 kb 字典 ——
    取 id 要注意这层包装（踩过：KeyError 'id'）。
    """
    from hc_agent.knowledge.service import KnowledgeService
    sv = service or KnowledgeService()
    for k in sv.list_kbs():
        if (k.get("name") or "") == DROP_KB_NAME:
            return sv, k["id"]
    created = sv.create_kb(DROP_KB_NAME, "通过拖拽/上传加入的资料")
    inner = created.get("kb") if isinstance(created, dict) else None
    if isinstance(inner, dict) and inner.get("id"):
        return sv, inner["id"]
    if isinstance(created, dict) and created.get("id"):
        return sv, created["id"]
    raise RuntimeError(f"创建知识库失败：{created}")


def _import_texts(sv, kb_id: str, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把抽好的正文写进知识库并触发索引。"""
    imported: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for d in docs:
        try:
            sv.add_text(kb_id, d["name"], d["text"],
                        tags=["拖入"], auto_index=True)
            imported.append({"name": d["name"], "chars": d.get("chars") or
                             len(d["text"])})
        except Exception as exc:
            failed.append({"name": d["name"],
                           "why": f"{type(exc).__name__}: {exc}"[:160]})
    indexed = 0
    if imported:
        try:
            p = sv.index_kb(kb_id, wait=True)
            indexed = getattr(p, "done", 0) or 0
        except Exception:
            indexed = 0
    return {"imported": imported, "failed": failed, "indexed": indexed}


def handle(paths, *, service=None, case_dest=None) -> Dict[str, Any]:
    """处理一次拖入（可多文件）。

    返回的 `message` 是直接可以显示给用户的一句话，
    `details` 是要展开看的明细。**不隐藏跳过项。**
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    items: List[Dict[str, Any]] = []
    total_imported = 0
    case_dirs: List[str] = []
    skipped_total = 0
    errors: List[str] = []

    sv = kb_id = None

    def ensure_kb():
        nonlocal sv, kb_id
        if sv is None:
            sv, kb_id = _kb(service)
        return sv, kb_id

    for raw in paths:
        p = Path(str(raw))
        if not p.exists():
            errors.append(f"路径不存在：{p}")
            continue

        # ---- 压缩包
        if p.is_file() and p.suffix.lower() == ".zip":
            info = zipbag.inspect(p)
            if not info["ok"]:
                errors.append(f"{p.name}：{info['error']}")
                continue
            item = {"path": str(p), "kind": "zip", "name": p.name}
            docs_ok, docs_bad = zipbag.extract_docs(p)
            if docs_ok:
                s, k = ensure_kb()
                res = _import_texts(s, k, docs_ok)
                item["imported"] = res["imported"]
                item["failed"] = res["failed"] + docs_bad
                total_imported += len(res["imported"])
            else:
                item["imported"] = []
                item["failed"] = docs_bad
            item["skipped"] = info["skipped"]
            skipped_total += len(info["skipped"])
            # 含算例就登记
            if info["dss"]:
                dest = case_dest or (Path(str(p)).parent / "_dropped_cases")
                cdir = zipbag.extract_dss(p, dest)
                if cdir is not None:
                    try:
                        from hc_agent.io import dss_registry
                        dss_registry.add_case(str(cdir))
                        case_dirs.append(str(cdir))
                        item["case_dir"] = str(cdir)
                    except Exception as exc:
                        item["case_error"] = f"{type(exc).__name__}: {exc}"
            items.append(item)
            continue

        # ---- 目录
        if p.is_dir():
            has_dss = any(p.glob("*.dss")) or any(p.glob("*.DSS"))
            item = {"path": str(p), "kind": "dir", "name": p.name}
            if has_dss:
                try:
                    from hc_agent.io import dss_registry
                    dss_registry.add_case(str(p))
                    case_dirs.append(str(p))
                    item["case_dir"] = str(p)
                except Exception as exc:
                    item["case_error"] = f"{type(exc).__name__}: {exc}"
            # 目录里的文档也一并入库
            docs = []
            bad = []
            for f in sorted(p.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in zipbag.DOC_EXTS:
                    continue
                if len(docs) >= zipbag.MAX_DOCS:
                    bad.append({"name": str(f.relative_to(p)),
                                "why": "超过单次上限，未处理"})
                    continue
                try:
                    from hc_agent.knowledge import loaders
                    text, _meta = loaders.extract(f)
                    text = (text or "").strip()
                    if text:
                        docs.append({"name": str(f.relative_to(p)),
                                     "text": text, "chars": len(text)})
                    else:
                        bad.append({"name": str(f.relative_to(p)),
                                    "why": "抽不到正文"})
                except Exception as exc:
                    bad.append({"name": str(f.relative_to(p)),
                                "why": f"{type(exc).__name__}: {exc}"[:120]})
            if docs:
                s, k = ensure_kb()
                res = _import_texts(s, k, docs)
                item["imported"] = res["imported"]
                item["failed"] = res["failed"] + bad
                total_imported += len(res["imported"])
            else:
                item["imported"] = []
                item["failed"] = bad
            item["skipped"] = []
            items.append(item)
            continue

        # ---- 单个 .dss 文件：把它所在目录当算例
        if p.suffix.lower() == ".dss":
            parent = p.parent
            try:
                from hc_agent.io import dss_registry
                dss_registry.add_case(str(parent))
                case_dirs.append(str(parent))
                items.append({"path": str(p), "kind": "dss", "name": p.name,
                              "case_dir": str(parent), "imported": [],
                              "failed": [], "skipped": []})
            except Exception as exc:
                errors.append(f"{p.name}：{exc}")
            continue

        # ---- 单个文档
        if p.suffix.lower() in zipbag.DOC_EXTS:
            try:
                from hc_agent.knowledge import loaders
                text, _meta = loaders.extract(p)
                text = (text or "").strip()
                if not text:
                    errors.append(f"{p.name}：抽不到正文（可能是扫描件）")
                    continue
                s, k = ensure_kb()
                res = _import_texts(s, k, [{"name": p.name, "text": text,
                                            "chars": len(text)}])
                total_imported += len(res["imported"])
                items.append({"path": str(p), "kind": "doc", "name": p.name,
                              "imported": res["imported"],
                              "failed": res["failed"], "skipped": []})
            except Exception as exc:
                errors.append(f"{p.name}：{type(exc).__name__}: {exc}")
            continue

        # ---- 其它：明确说不支持
        errors.append(f"{p.name}：不支持的类型（{p.suffix or '无扩展名'}）")

    # ---- 汇总成一句话
    bits: List[str] = []
    if total_imported:
        bits.append(f"已导入知识库 {total_imported} 篇")
    if case_dirs:
        bits.append(f"登记算例 {len(case_dirs)} 个")
    if skipped_total:
        bits.append(f"跳过二进制/不支持 {skipped_total} 个")
    if errors:
        bits.append(f"{len(errors)} 个未处理")
    if not bits:
        message = "没有可导入的内容。"
    else:
        message = "；".join(bits) + "。"
    if case_dirs:
        message += "　可以说「算一下我加的算例」来跑它。"
    if total_imported:
        message += "　现在可以直接问资料里的内容。"

    return {"ok": not errors or bool(total_imported or case_dirs),
            "message": message, "items": items, "errors": errors,
            "imported_count": total_imported, "case_dirs": case_dirs,
            "kb_id": kb_id or ""}
