"""service.py — 知识库系统的高层服务（对齐 ai-localbase 的能力面）。

界面只需调这一层，不必关心 SQLite、索引缓存与线程。

## 能力面（对应 ai-localbase 的核心能力）

| ai-localbase | 本实现 |
|---|---|
| 知识库管理：创建、删除、查看文档列表 | `list_kbs/create_kb/delete_kb/list_docs` |
| 文档上传与索引：TXT/MD/PDF/xlsx/csv | `add_files/add_text` → `index_kb` |
| 检索增强问答：向量检索注入上下文 | `search/as_context`（BM25 + TF-IDF hybrid） |
| 聊天记录持久化：SQLite | `new_session/append/list_messages` |
| 配置持久化：本地 JSON | `llm.json`（已有）+ `meta` 表 |
| 批量文档异步索引 Job，可查进度可取消 | `job_manager` + `index_kb` |
| 从文档生成 RAG 评估数据集 | `generate_eval` |

## 索引缓存

每个知识库一份 TF-IDF 索引，落盘在 `.index/kb-<id>-<fp>.npz`。
`fp` 由该库全部块的内容哈希算出 —— 文档一变指纹就变，自动重建，
不需要手动清缓存（但界面仍提供"重建索引"按钮应对异常）。
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hc_agent import paths
from hc_agent.knowledge import loaders
from hc_agent.knowledge.chunking import chunk_document
from hc_agent.knowledge.index import VectorIndex, fingerprint, tokenize
from hc_agent.knowledge.jobs import JobManager, Progress
from hc_agent.knowledge.search import Hit, Searcher
from hc_agent.knowledge.store import Store, new_id, now

#: 默认知识库名（预置配网承载力内容）
DEFAULT_KB_NAME = "配网承载力"
DEFAULT_KB_ID = "kb_capacity"

#: 索引缓存目录
CACHE_DIRNAME = ".index"


def knowledge_root() -> Path:
    return paths.data_root_safe() / "knowledge"


def files_root() -> Path:
    return knowledge_root() / "files"


def cache_root() -> Path:
    return knowledge_root() / CACHE_DIRNAME


def db_path() -> Path:
    return knowledge_root() / "kb.sqlite"


def builtin_dir() -> Path:
    return paths.resource("hc_agent", "knowledge", "builtin")


def _sha(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return ""


class KnowledgeService:
    """知识库系统入口。"""

    def __init__(self, *, store: Optional[Store] = None) -> None:
        knowledge_root().mkdir(parents=True, exist_ok=True)
        files_root().mkdir(parents=True, exist_ok=True)
        cache_root().mkdir(parents=True, exist_ok=True)
        self.store = store or Store(db_path())
        self.jobs = JobManager(self.store)
        self._search_cache: Dict[str, Tuple[str, Searcher]] = {}
        self._ensure_seeded()

    # ---------------------------------------------------------- 种子
    def _ensure_seeded(self) -> None:
        """首次运行：建默认库并把内置的配网承载力文档登记进去。"""
        kb = self.store.get_kb(DEFAULT_KB_ID)
        if kb is None:
            existing = self.store.kb_by_name(DEFAULT_KB_NAME)
            if existing is None:
                self.store.create_kb(
                    DEFAULT_KB_NAME,
                    "预置的配网承载力知识：口径、约束限值、搜索方法、"
                    "场景工况、复算门禁、分级、OpenDSS 边界、术语与常见疑问。"
                    "可继续上传你们自己的规范与经验。",
                    is_default=True, kb_id=DEFAULT_KB_ID)
            else:
                self.store.update_kb(existing["id"], description="默认知识库")
        # 内置文档：按文件名去重登记（不复制，直接引用包内只读路径）
        bd = builtin_dir()
        if not bd.is_dir():
            return
        have = {d["title"] for d in self.store.list_docs(DEFAULT_KB_ID)}
        added = 0
        for p in sorted(bd.glob("*.md")):
            if p.stem in have:
                continue
            self.store.add_doc(
                DEFAULT_KB_ID, title=p.stem, origin="builtin",
                kind="Markdown 文档", size=p.stat().st_size,
                sha256=_sha(p), stored_path="", src_path=str(p),
                status="pending")
            added += 1
        if added:
            # 首次登记后立即建索引，保证开箱可用
            self.index_kb(DEFAULT_KB_ID, wait=True)

    # ---------------------------------------------------------- 知识库
    def list_kbs(self) -> List[Dict[str, Any]]:
        return self.store.list_kbs()

    def get_kb(self, kb_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_kb(kb_id)

    def default_kb_id(self) -> str:
        for k in self.store.list_kbs():
            if k.get("is_default"):
                return k["id"]
        kbs = self.store.list_kbs()
        return kbs[0]["id"] if kbs else ""

    def create_kb(self, name: str, description: str = "") -> Dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "知识库名称不能为空"}
        if self.store.kb_by_name(name) is not None:
            return {"ok": False, "error": f"已存在同名知识库：{name}"}
        kb = self.store.create_kb(name, description)
        return {"ok": True, "kb": kb}

    def delete_kb(self, kb_id: str) -> Dict[str, Any]:
        r = self.store.delete_kb(kb_id)
        if r.get("ok"):
            d = files_root() / kb_id
            shutil.rmtree(d, ignore_errors=True)
            self._search_cache.pop(kb_id, None)
        return r

    # ---------------------------------------------------------- 文档
    def list_docs(self, kb_id: str) -> List[Dict[str, Any]]:
        return self.store.list_docs(kb_id)

    def add_text(self, kb_id: str, title: str, text: str, *,
                 tags: Optional[Sequence[str]] = None,
                 auto_index: bool = True) -> Dict[str, Any]:
        title = (title or "").strip()
        text = (text or "").strip()
        if not title or not text:
            return {"ok": False, "error": "标题与正文都不能为空"}
        d = files_root() / kb_id
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in title)[:40] or "note"
        p = d / f"{safe}.md"
        i = 2
        while p.exists():
            p = d / f"{safe}-{i}.md"
            i += 1
        head = ["---", f"title: {title}"]
        if tags:
            head.append("tags: [" + ", ".join(tags) + "]")
        head.append("---")
        p.write_text("\n".join(head) + "\n\n" + text, encoding="utf-8")
        doc = self.store.add_doc(
            kb_id, title=title, origin="user", kind="Markdown 文档",
            size=p.stat().st_size, sha256=_sha(p), stored_path=str(p),
            src_path=str(p), status="pending")
        if auto_index:
            self.index_kb(kb_id)
        return {"ok": True, "doc": doc}

    def add_files(self, kb_id: str, files: Iterable) -> Dict[str, Any]:
        """上传一批文件：先复制进受管目录，再登记。不自动建索引。

        返回每个文件的结果，界面据此显示"成功 N / 失败 M 及原因"。
        """
        out: List[Dict[str, Any]] = []
        dest_dir = files_root() / kb_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in files:
            sp = Path(src)
            try:
                if not sp.is_file():
                    out.append({"file": sp.name, "ok": False,
                                "error": "文件不存在"})
                    continue
                if sp.suffix.lower() not in loaders.SUPPORTED_SUFFIXES:
                    out.append({"file": sp.name, "ok": False,
                                "error": f"不支持 {sp.suffix}；支持 "
                                         + "、".join(loaders.SUPPORTED_SUFFIXES)})
                    continue
                # 先解析一遍：读不出来就不入库，避免"看不见的空文档"
                try:
                    body, meta = loaders.extract(sp)
                except loaders.ExtractError as exc:
                    out.append({"file": sp.name, "ok": False,
                                "error": str(exc)})
                    continue
                dst = dest_dir / sp.name
                i = 2
                while dst.exists():
                    dst = dest_dir / f"{sp.stem}-{i}{sp.suffix}"
                    i += 1
                shutil.copy2(sp, dst)
                title = str(meta.get("title") or "").strip() or sp.stem
                doc = self.store.add_doc(
                    kb_id, title=title, origin="user",
                    kind=str(meta.get("_kind") or sp.suffix),
                    size=dst.stat().st_size, sha256=_sha(dst),
                    stored_path=str(dst), src_path=str(sp),
                    status="pending")
                out.append({"file": sp.name, "ok": True,
                            "doc_id": doc["id"], "title": title,
                            "chars": len(body)})
            except Exception as exc:
                out.append({"file": sp.name, "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"})
        ok_n = sum(1 for x in out if x.get("ok"))
        return {"ok": ok_n > 0, "added": ok_n,
                "failed": len(out) - ok_n, "items": out}

    def delete_doc(self, doc_id: str) -> Dict[str, Any]:
        d = self.store.get_doc(doc_id)
        r = self.store.delete_doc(doc_id)
        if r.get("ok") and d:
            sp = r.get("stored_path") or ""
            if sp:
                try:
                    Path(sp).unlink(missing_ok=True)
                except Exception:
                    pass
            self._search_cache.pop(d["kb_id"], None)
        return r

    def delete_all_docs(self, kb_id: str) -> Dict[str, Any]:
        n = 0
        for d in self.store.list_docs(kb_id):
            if self.delete_doc(d["id"]).get("ok"):
                n += 1
        self.store.clear_chunks(kb_id)
        return {"ok": True, "deleted": n}

    # ---------------------------------------------------------- 索引
    def index_kb(self, kb_id: str, *, wait: bool = False) -> Progress:
        """提交异步索引任务。

        `wait=True` 会**阻塞到任务结束**。首次灌入内置文档时必须用它：
        否则进程可能在后台线程跑完前就退出，用户第一次打开知识库看到的是空库
        （实测踩过：任务行是 done，但 chunks 为 0）。
        """
        docs = [d for d in self.store.list_docs(kb_id) if d["origin"] != "builtin"
                or Path(d.get("src_path") or "").is_file()]
        p = self.jobs.submit(
            "index", self._do_index, kb_id=kb_id, total=len(docs),
            message=f"准备索引 {len(docs)} 个文档")
        if wait:
            self.jobs.join(p.job_id, timeout=300.0)
            return self.jobs.progress(p.job_id) or p
        return p

    def _do_index(self, job_id: str, report) -> Dict[str, Any]:
        j = self.store.get_job(job_id) or {}
        kb_id = j.get("kb_id") or ""
        docs = self.store.list_docs(kb_id)
        total = len(docs)
        report(total=total, done=0, message=f"共 {total} 个文档")
        done = failed = 0
        for i, d in enumerate(docs, start=1):
            if self.store.cancel_requested(job_id):
                report(message=f"已取消（完成 {done}/{total}）", done=done)
                return {"done": done, "message": f"已取消，完成 {done} 个"}
            src = Path(d.get("stored_path") or d.get("src_path") or "")
            try:
                if not src.is_file():
                    raise loaders.ExtractError(f"源文件已不存在：{src}")
                body, _meta = loaders.extract(src)
                pieces = chunk_document(d["id"], body)
                rows = [{"heading": c.heading, "breadcrumb": c.breadcrumb,
                         "text": c.text} for c in pieces]
                n = self.store.replace_chunks(kb_id, d["id"], rows)
                self.store.set_doc_status(d["id"], "indexed", "")
                done += 1
                report(done=i, message=f"已索引 {d['title']}（{n} 块）")
            except Exception as exc:
                failed += 1
                self.store.set_doc_status(d["id"], "failed",
                                          f"{type(exc).__name__}: {exc}")
                report(done=i, message=f"{d['title']} 失败：{exc}")
        self._search_cache.pop(kb_id, None)
        msg = f"索引完成：成功 {done} / 失败 {failed} / 共 {total}"
        report(done=total, message=msg)
        return {"done": done, "message": msg}

    def rebuild_index(self, kb_id: str) -> Progress:
        self.store.clear_chunks(kb_id)
        self._search_cache.pop(kb_id, None)
        return self.index_kb(kb_id)

    def job_progress(self, job_id: str) -> Optional[Progress]:
        return self.jobs.progress(job_id)

    def list_jobs(self, kb_id: str = "", limit: int = 30) -> List[Progress]:
        return self.jobs.list(kb_id=kb_id, limit=limit)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        return self.jobs.cancel(job_id)

    # ---------------------------------------------------------- 检索
    def _searcher(self, kb_id: str) -> Optional[Searcher]:
        chunks = self.store.list_chunks(kb_id)
        if not chunks:
            return None
        fp = fingerprint([c["id"] + c["text"] for c in chunks])
        hit = self._search_cache.get(kb_id)
        if hit is not None and hit[0] == fp:
            return hit[1]
        # 尝试读磁盘缓存
        cache = cache_root() / f"kb-{kb_id}-{fp}.npz"
        tokens = [tokenize(c["text"]) for c in chunks]
        idx = VectorIndex.load(cache)
        if idx is None or idx.matrix.shape[0] != len(chunks):
            idx = VectorIndex.build(tokens, [c["id"] for c in chunks])
            try:
                idx.save(cache)
                old = sorted(cache_root().glob(f"kb-{kb_id}-*.npz"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
                for p in old[2:]:
                    try:
                        p.unlink()
                        p.with_suffix(".meta.json").unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass
        # Searcher 要 Chunk 形状的对象（有 chunk_id/text/breadcrumb）
        titles = {d["id"]: d["title"] for d in self.store.list_docs(kb_id)}
        objs = [_ChunkView(c, titles.get(c["doc_id"], "")) for c in chunks]
        s = Searcher(objs, idx, tokens)
        self._search_cache[kb_id] = (fp, s)
        return s

    def search(self, query: str, *, kb_id: str = "", top_k: int = 5,
               all_kbs: bool = False) -> List[Hit]:
        ids = ([k["id"] for k in self.store.list_kbs()] if all_kbs
               else [kb_id or self.default_kb_id()])
        per_kb: List[List[Hit]] = []
        for kid in ids:
            s = self._searcher(kid)
            if s is None:
                continue
            hits = []
            for h in s.search(query, top_k=top_k):
                h.kb_id = kid          # type: ignore[attr-defined]
                hits.append(h)
            if hits:
                per_kb.append(hits)
        if not per_kb:
            return []
        if len(per_kb) == 1:
            return per_kb[0][:top_k]
        # 跨库检索：**按库轮转取**，而不是把所有命中混在一起按分数截断。
        # 否则大库（内置 21 篇、156 块）会把小库完全挤掉 ——
        # 用户问"哪些库里有这条"时会得到一个"只有一个库命中"的假象（实测踩过）。
        merged: List[Hit] = []
        depth = max(len(h) for h in per_kb)
        for rank in range(depth):
            for hits in per_kb:
                if rank < len(hits):
                    merged.append(hits[rank])
        # 每库的第一条已经占了靠前位置，其余按分数稳定排序
        head = merged[:len(per_kb)]
        tail = sorted(merged[len(per_kb):], key=lambda x: -x.score)
        return (head + tail)[:top_k]

    def as_context(self, query: str, *, kb_id: str = "", top_k: int = 3,
                   max_chars: int = 2600, all_kbs: bool = False) -> str:
        ids = ([k["id"] for k in self.store.list_kbs()] if all_kbs
               else [kb_id or self.default_kb_id()])
        parts: List[str] = []
        used = 0
        for kid in ids:
            s = self._searcher(kid)
            if s is None:
                continue
            remain = max_chars - used
            if remain < 200:
                break
            ctx = s.context(query, top_k=top_k, max_chars=remain)
            if ctx:
                parts.append(ctx)
                used += len(ctx)
        return "\n\n".join(parts)

    # ---------------------------------------------------------- 会话
    def new_session(self, title: str = "") -> Dict[str, Any]:
        return self.store.create_session(title)

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list_sessions(limit)

    def list_messages(self, sid: str) -> List[Dict[str, Any]]:
        return self.store.list_messages(sid)

    def append(self, sid: str, role: str, content: str, *,
               engine: str = "", source: str = "") -> None:
        self.store.add_message(sid, role, content, engine=engine,
                               source=source)

    def delete_session(self, sid: str) -> None:
        self.store.delete_session(sid)

    # ---------------------------------------------------------- 统计
    def stats(self, kb_id: str = "") -> Dict[str, Any]:
        kid = kb_id or self.default_kb_id()
        docs = self.store.list_docs(kid)
        ov = self.store.overview()
        return {
            "kb_id": kid,
            "docs": len(docs),
            "builtin": sum(1 for d in docs if d["origin"] == "builtin"),
            "user": sum(1 for d in docs if d["origin"] == "user"),
            "chunks": sum(int(d.get("chunk_count") or 0) for d in docs),
            "failed": sum(1 for d in docs if d["status"] == "failed"),
            "kbs": ov["kbs"],
            "all_chunks": ov["chunks"],
            "sessions": ov["sessions"],
            "messages": ov["messages"],
            "root": str(knowledge_root()),
        }

    # ---------------------------------------------------------- 评估集
    def generate_eval(self, kb_id: str = "", *, max_items: int = 30) -> Dict[str, Any]:
        from hc_agent.knowledge.eval import generate_eval as _gen
        return _gen(self, kb_id or self.default_kb_id(), max_items=max_items)


class _ChunkView:
    """把数据库行包装成 Searcher 期望的块对象。"""

    __slots__ = ("chunk_id", "doc_id", "text", "heading", "breadcrumb",
                 "order", "doc_title")

    def __init__(self, row: Dict[str, Any],
                 doc_title: str = "") -> None:
        self.chunk_id = row["id"]
        self.doc_id = row["doc_id"]
        self.text = row["text"]
        self.heading = row.get("heading") or ""
        self.breadcrumb = row.get("breadcrumb") or ""
        self.order = int(row.get("ord") or 0)
        # 所在文档标题：检索时用于"问文献/问定义"这类意图的加权
        self.doc_title = doc_title
