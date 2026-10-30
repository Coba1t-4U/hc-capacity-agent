"""store.py — 知识库系统的持久化层（SQLite）。

对齐 ai-localbase 的数据模型：**多知识库**、文档、块、索引任务、会话消息。

## 为什么用 SQLite 而不是 JSON

* 并发安全：界面线程与索引线程会同时读写，JSON 需要自己加锁且易写坏；
* 增量查询：文档列表、任务进度、会话消息都是"按条件取一部分"，
  用 SQL 比全量读 JSON 再过滤干净；
* 崩溃安全：SQLite 有事务与 WAL，进程被杀不会留下半截文件。

## 表结构

```
kb        知识库（可建多个；默认库预置"配网承载力"）
doc       文档（一个文件一行，含解析状态与错误）
chunk     切块（按 kb_id / doc_id 归组）
job       索引任务（异步，含进度与取消标记）
session   会话
message   会话消息（问答历史）
meta      键值杂项（schema 版本、种子标记等）
```
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    is_default  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doc (
    id          TEXT PRIMARY KEY,
    kb_id       TEXT NOT NULL,
    title       TEXT DEFAULT '',
    origin      TEXT DEFAULT 'user',
    kind        TEXT DEFAULT '',
    size        INTEGER DEFAULT 0,
    sha256      TEXT DEFAULT '',
    stored_path TEXT DEFAULT '',
    src_path    TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    error       TEXT DEFAULT '',
    added_at    TEXT NOT NULL,
    FOREIGN KEY (kb_id) REFERENCES kb(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_kb ON doc(kb_id);
CREATE TABLE IF NOT EXISTS chunk (
    id        TEXT PRIMARY KEY,
    kb_id     TEXT NOT NULL,
    doc_id    TEXT NOT NULL,
    ord       INTEGER DEFAULT 0,
    heading   TEXT DEFAULT '',
    breadcrumb TEXT DEFAULT '',
    text      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_kb ON chunk(kb_id);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk(doc_id);
CREATE TABLE IF NOT EXISTS job (
    id         TEXT PRIMARY KEY,
    kb_id      TEXT DEFAULT '',
    kind       TEXT NOT NULL,
    status     TEXT NOT NULL,
    total      INTEGER DEFAULT 0,
    done       INTEGER DEFAULT 0,
    message    TEXT DEFAULT '',
    cancel_req INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,
    title      TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    engine     TEXT DEFAULT '',
    source     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON message(session_id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    h = uuid.uuid4().hex[:12]
    return f"{prefix}{h}" if prefix else h


class Store:
    """线程安全的 SQLite 封装。"""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ---------------------------------------------------------- 基础
    @contextmanager
    def tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    @staticmethod
    def _rows(cur) -> List[Dict[str, Any]]:
        return [dict(r) for r in cur.fetchall()]

    # ---------------------------------------------------------- meta
    def get_meta(self, k: str, default: str = "") -> str:
        with self._lock:
            cur = self._conn.execute("SELECT v FROM meta WHERE k=?", (k,))
            r = cur.fetchone()
            return r["v"] if r else default

    def set_meta(self, k: str, v: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO meta(k,v) VALUES(?,?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # ---------------------------------------------------------- 知识库
    def list_kbs(self) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT k.*, "
                "(SELECT COUNT(*) FROM doc d WHERE d.kb_id=k.id) AS doc_count, "
                "(SELECT COUNT(*) FROM chunk ch WHERE ch.kb_id=k.id) AS chunk_count "
                "FROM kb k ORDER BY k.is_default DESC, k.created_at ASC")
            return self._rows(cur)

    def get_kb(self, kb_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM kb WHERE id=?", (kb_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def kb_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM kb WHERE name=?", (name,))
            r = cur.fetchone()
            return dict(r) if r else None

    def create_kb(self, name: str, description: str = "",
                  *, is_default: bool = False,
                  kb_id: Optional[str] = None) -> Dict[str, Any]:
        kid = kb_id or new_id("kb_")
        t = now()
        with self.tx() as c:
            c.execute("INSERT INTO kb(id,name,description,is_default,"
                      "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                      (kid, name, description, 1 if is_default else 0, t, t))
        return self.get_kb(kid) or {}

    def update_kb(self, kb_id: str, *, name: Optional[str] = None,
                  description: Optional[str] = None) -> bool:
        sets, args = ["updated_at=?"], [now()]
        if name is not None:
            sets.append("name=?")
            args.append(name)
        if description is not None:
            sets.append("description=?")
            args.append(description)
        args.append(kb_id)
        with self.tx() as c:
            cur = c.execute(f"UPDATE kb SET {', '.join(sets)} WHERE id=?", args)
            return cur.rowcount > 0

    def delete_kb(self, kb_id: str) -> Dict[str, Any]:
        """删除知识库（连带文档、块、索引缓存）。默认库不允许删。"""
        kb = self.get_kb(kb_id)
        if kb is None:
            return {"ok": False, "error": "知识库不存在"}
        if kb.get("is_default"):
            return {"ok": False,
                    "error": "默认知识库不能删除（可清空其中的文档）"}
        with self.tx() as c:
            c.execute("DELETE FROM chunk WHERE kb_id=?", (kb_id,))
            c.execute("DELETE FROM doc WHERE kb_id=?", (kb_id,))
            c.execute("DELETE FROM kb WHERE id=?", (kb_id,))
        return {"ok": True, "deleted": kb_id}

    # ---------------------------------------------------------- 文档
    def list_docs(self, kb_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT d.*, (SELECT COUNT(*) FROM chunk ch "
                "WHERE ch.doc_id=d.id) AS chunk_count "
                "FROM doc d WHERE d.kb_id=? ORDER BY d.added_at ASC", (kb_id,))
            return self._rows(cur)

    def get_doc(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM doc WHERE id=?", (doc_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def add_doc(self, kb_id: str, *, title: str, origin: str = "user",
                kind: str = "", size: int = 0, sha256: str = "",
                stored_path: str = "", src_path: str = "",
                status: str = "pending") -> Dict[str, Any]:
        did = new_id("doc_")
        with self.tx() as c:
            c.execute(
                "INSERT INTO doc(id,kb_id,title,origin,kind,size,sha256,"
                "stored_path,src_path,status,added_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (did, kb_id, title, origin, kind, size, sha256,
                 stored_path, src_path, status, now()))
            c.execute("UPDATE kb SET updated_at=? WHERE id=?", (now(), kb_id))
        return self.get_doc(did) or {}

    def set_doc_status(self, doc_id: str, status: str, error: str = "") -> None:
        with self.tx() as c:
            c.execute("UPDATE doc SET status=?, error=? WHERE id=?",
                      (status, error, doc_id))

    def delete_doc(self, doc_id: str) -> Dict[str, Any]:
        d = self.get_doc(doc_id)
        if d is None:
            return {"ok": False, "error": "文档不存在"}
        with self.tx() as c:
            c.execute("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
            c.execute("DELETE FROM doc WHERE id=?", (doc_id,))
            c.execute("UPDATE kb SET updated_at=? WHERE id=?",
                      (now(), d["kb_id"]))
        return {"ok": True, "deleted": doc_id,
                "stored_path": d.get("stored_path") or ""}

    # ---------------------------------------------------------- 块
    def replace_chunks(self, kb_id: str, doc_id: str,
                       chunks: Iterable[Dict[str, Any]]) -> int:
        rows = list(chunks)
        with self.tx() as c:
            c.execute("DELETE FROM chunk WHERE doc_id=?", (doc_id,))
            c.executemany(
                "INSERT INTO chunk(id,kb_id,doc_id,ord,heading,breadcrumb,text)"
                " VALUES(?,?,?,?,?,?,?)",
                [(f"{doc_id}#{i}", kb_id, doc_id, i,
                  ch.get("heading", ""), ch.get("breadcrumb", ""),
                  ch.get("text", "")) for i, ch in enumerate(rows)])
        return len(rows)

    def list_chunks(self, kb_id: str, doc_id: str = "") -> List[Dict[str, Any]]:
        with self._lock:
            if doc_id:
                cur = self._conn.execute(
                    "SELECT * FROM chunk WHERE doc_id=? ORDER BY ord", (doc_id,))
            else:
                cur = self._conn.execute(
                    "SELECT * FROM chunk WHERE kb_id=? ORDER BY doc_id, ord",
                    (kb_id,))
            return self._rows(cur)

    def clear_chunks(self, kb_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM chunk WHERE kb_id=?", (kb_id,))

    # ---------------------------------------------------------- 任务
    def add_job(self, kind: str, *, kb_id: str = "", total: int = 0,
                message: str = "") -> Dict[str, Any]:
        jid = new_id("job_")
        with self.tx() as c:
            c.execute("INSERT INTO job(id,kb_id,kind,status,total,done,"
                      "message,created_at) VALUES(?,?,?,?,?,?,?,?)",
                      (jid, kb_id, kind, "queued", total, 0, message, now()))
        return self.get_job(jid) or {}

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM job WHERE id=?", (job_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def list_jobs(self, kb_id: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            if kb_id:
                cur = self._conn.execute(
                    "SELECT * FROM job WHERE kb_id=? ORDER BY created_at DESC "
                    "LIMIT ?", (kb_id, limit))
            else:
                cur = self._conn.execute(
                    "SELECT * FROM job ORDER BY created_at DESC LIMIT ?",
                    (limit,))
            return self._rows(cur)

    def update_job(self, job_id: str, **kw) -> None:
        if not kw:
            return
        allowed = {"status", "total", "done", "message", "cancel_req",
                   "started_at", "finished_at"}
        sets, args = [], []
        for k, v in kw.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            args.append(v)
        if not sets:
            return
        args.append(job_id)
        with self.tx() as c:
            c.execute(f"UPDATE job SET {', '.join(sets)} WHERE id=?", args)

    def cancel_requested(self, job_id: str) -> bool:
        j = self.get_job(job_id)
        return bool(j and j.get("cancel_req"))

    # ---------------------------------------------------------- 会话
    def create_session(self, title: str = "") -> Dict[str, Any]:
        sid = new_id("s_")
        t = now()
        with self.tx() as c:
            c.execute("INSERT INTO session(id,title,created_at,updated_at)"
                      " VALUES(?,?,?,?)", (sid, title or "新会话", t, t))
        return self.get_session(sid) or {}

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM session WHERE id=?", (sid,))
            r = cur.fetchone()
            return dict(r) if r else None

    def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM message m "
                "WHERE m.session_id=s.id) AS msg_count "
                "FROM session s ORDER BY s.updated_at DESC LIMIT ?", (limit,))
            return self._rows(cur)

    def delete_session(self, sid: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM message WHERE session_id=?", (sid,))
            c.execute("DELETE FROM session WHERE id=?", (sid,))

    def add_message(self, sid: str, role: str, content: str,
                    *, engine: str = "", source: str = "") -> None:
        t = now()
        with self.tx() as c:
            c.execute("INSERT INTO message(session_id,role,content,engine,"
                      "source,created_at) VALUES(?,?,?,?,?,?)",
                      (sid, role, content, engine, source, t))
            c.execute("UPDATE session SET updated_at=? WHERE id=?", (t, sid))
            # 首条用户消息用它当会话标题，便于识别
            cur = c.execute("SELECT COUNT(*) AS n FROM message "
                            "WHERE session_id=? AND role='user'", (sid,))
            row = cur.fetchone()
            if row and row["n"] == 1:
                c.execute("UPDATE session SET title=? WHERE id=? AND "
                          "(title='' OR title='新会话')",
                          (content[:28], sid))

    def list_messages(self, sid: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM message WHERE session_id=? "
                "ORDER BY id ASC LIMIT ?", (sid, limit))
            return self._rows(cur)

    # ---------------------------------------------------------- 统计
    def overview(self) -> Dict[str, Any]:
        with self._lock:
            def one(sql: str) -> int:
                r = self._conn.execute(sql).fetchone()
                return int(r[0]) if r else 0
            return {
                "kbs": one("SELECT COUNT(*) FROM kb"),
                "docs": one("SELECT COUNT(*) FROM doc"),
                "chunks": one("SELECT COUNT(*) FROM chunk"),
                "sessions": one("SELECT COUNT(*) FROM session"),
                "messages": one("SELECT COUNT(*) FROM message"),
                "jobs": one("SELECT COUNT(*) FROM job"),
            }
