"""chat_history.py — 问答历史持久化（会话 + 消息）。

## 为什么要单独一个库

知识库那套 `store.py` 存的是知识、文档、索引任务，语义完全不同。
把聊天记录塞进去会让"删库"和"删会话"互相牵连。所以单独一份 SQLite：
`<数据目录>/chat.sqlite`。

## 表结构

```
session  会话（标题、创建/更新时间、消息数）
message  消息（角色、内容、引擎、出处、报告路径）
```

## 标题怎么来

不给用户增加"先起个名字"的负担：**用第一条用户消息自动命名**（截断到 28 字）。
之后用户改名也支持（`rename`）。

## 为什么要存 engine / source / report_path

重开会话时要能**原样恢复**当时的回答：
* `engine` 决定气泡上显示 `DeepSeek` 还是 `本地规则`；
* `source` 是出处（产物的相对路径）；
* `report_path` 决定要不要恢复"打开计算报告"按钮。
只存纯文本的话，历史消息看起来会"比当时少了东西"。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    seq        INTEGER NOT NULL DEFAULT 0,
    starred    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    engine      TEXT DEFAULT '',
    source      TEXT DEFAULT '',
    report_path TEXT DEFAULT '',
    report_label TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON message(session_id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

#: 自动标题的最大长度（再长就截断，避免侧栏撑爆）
TITLE_MAX = 28


def _now() -> str:
    """ISO 时间戳（带微秒），**仅用于显示**。

    ⚠ 不能用它排序！Windows 上 `datetime.now()` 的实际粒度约 **15.6 ms**，
    微秒位是量化出来的。连续两次操作（建会话 + 立刻发消息）会落在
    同一时间量子内、时间戳完全相同，`ORDER BY updated_at DESC` 就退化成
    任意顺序，表现为"最新会话不在最上面""重开应用选错会话"。

    排序改用 `session.seq`（持久化的单调序号），见 `_bump_seq()`。
    """
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="microseconds")


def _short(text: str, n: int = TITLE_MAX) -> str:
    t = " ".join(str(text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


class ChatHistory:
    """会话与消息的读写。线程安全（索引线程不碰它，但问答在后台线程写）。"""

    def __init__(self, path=None) -> None:
        if path is None:
            from hc_agent import paths
            path = paths.data_root_safe() / "chat.sqlite"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """老库补列（`seq` 排序号 / `starred` 星标），避免升级后功能缺失。"""
        try:
            cols = {r[1] for r in
                    self._conn.execute("PRAGMA table_info(session)")}
            if "seq" not in cols:
                self._conn.execute(
                    "ALTER TABLE session ADD COLUMN seq INTEGER DEFAULT 0")
            if "starred" not in cols:
                self._conn.execute(
                    "ALTER TABLE session ADD COLUMN starred INTEGER DEFAULT 0")
            # 回填：按 rowid 还原一个合理的历史顺序
            self._conn.execute(
                "UPDATE session SET seq=rowid WHERE seq IS NULL OR seq=0")
        except Exception:
            pass

    def _bump_seq(self, c, sid: str) -> int:
        """把某会话的序号推到全局最大 +1，并返回它。

        为什么不用时间戳：见 `_now()` 的说明 —— 系统时钟精度不够。
        序号存在 meta 表里，随会话一起持久化，重启后仍单调。
        """
        row = c.execute("SELECT v FROM meta WHERE k='seq'").fetchone()
        try:
            n = int(row[0]) + 1 if row else 1
        except Exception:
            n = 1
        # 兜底：万一 meta 丢了但 session.seq 更大，取两者最大
        mx = c.execute("SELECT COALESCE(MAX(seq),0) FROM session").fetchone()
        try:
            n = max(n, int(mx[0]) + 1)
        except Exception:
            pass
        c.execute("INSERT INTO meta(k,v) VALUES('seq',?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))
        c.execute("UPDATE session SET seq=? WHERE id=?", (n, sid))
        return n

    @contextmanager
    def _tx(self):
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

    # ------------------------------------------------------------ 会话
    def list_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM message m "
                "WHERE m.session_id=s.id) AS msg_count "
                "FROM session s "
                "ORDER BY s.starred DESC, s.seq DESC, s.rowid DESC LIMIT ?",
                (limit,))
            return [dict(r) for r in cur.fetchall()]

    def get_session(self, sid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM session WHERE id=?",
                                     (sid,))
            r = cur.fetchone()
            return dict(r) if r else None

    def create_session(self, title: str = "") -> Dict[str, Any]:
        sid = "s_" + uuid.uuid4().hex[:12]
        t = _now()
        with self._tx() as c:
            c.execute("INSERT INTO session(id,title,created_at,updated_at,seq)"
                      " VALUES(?,?,?,?,0)", (sid, title or "", t, t))
            self._bump_seq(c, sid)
        return self.get_session(sid) or {"id": sid, "title": title}

    def set_starred(self, sid: str, starred: bool = True) -> bool:
        """标记/取消标记某个会话（星标）。

        **不动 seq**：标记不是"活动"，不该把它当成"刚用过"；
        排序里单独用 `starred DESC` 把星标会话排到最上面即可。
        """
        with self._tx() as c:
            cur = c.execute("UPDATE session SET starred=? WHERE id=?",
                            (1 if starred else 0, sid))
            return cur.rowcount > 0

    def toggle_starred(self, sid: str) -> bool:
        """切换星标，返回切换后的状态。"""
        with self._tx() as c:
            row = c.execute("SELECT starred FROM session WHERE id=?",
                            (sid,)).fetchone()
            if row is None:
                return False
            new = 0 if int(row[0] or 0) else 1
            c.execute("UPDATE session SET starred=? WHERE id=?", (new, sid))
            return bool(new)

    def rename(self, sid: str, title: str) -> bool:
        with self._tx() as c:
            cur = c.execute("UPDATE session SET title=?, updated_at=? "
                            "WHERE id=?", (_short(title, 80), _now(), sid))
            if cur.rowcount > 0:
                self._bump_seq(c, sid)
            return cur.rowcount > 0

    def delete(self, sid: str) -> bool:
        with self._tx() as c:
            cur = c.execute("DELETE FROM session WHERE id=?", (sid,))
            c.execute("DELETE FROM message WHERE session_id=?", (sid,))
            return cur.rowcount > 0

    def latest_session(self) -> Optional[Dict[str, Any]]:
        s = self.list_sessions(limit=1)
        return s[0] if s else None

    def ensure_session(self) -> Dict[str, Any]:
        """打开问答页时用：有最近会话就沿用，没有就新建一个。"""
        s = self.latest_session()
        if s is not None:
            return s
        return self.create_session()

    # ------------------------------------------------------------ 消息
    def list_messages(self, sid: str, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM message WHERE session_id=? "
                "ORDER BY id ASC LIMIT ?", (sid, limit))
            return [dict(r) for r in cur.fetchall()]

    def add_message(self, sid: str, role: str, content: str, *,
                    engine: str = "", source: str = "",
                    report_path: str = "", report_label: str = "") -> None:
        t = _now()
        with self._tx() as c:
            c.execute(
                "INSERT INTO message(session_id,role,content,engine,source,"
                "report_path,report_label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (sid, role, content, engine, source, report_path,
                 report_label, t))
            c.execute("UPDATE session SET updated_at=? WHERE id=?", (t, sid))
            self._bump_seq(c, sid)
            # 首条用户消息 → 自动当标题（用户不必先起名）
            row = c.execute(
                "SELECT COUNT(*) AS n FROM message WHERE session_id=? "
                "AND role='user'", (sid,)).fetchone()
            if row and row["n"] == 1:
                c.execute("UPDATE session SET title=? WHERE id=? AND "
                          "(title='' OR title IS NULL)",
                          (_short(content), sid))

    def message_count(self, sid: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM message WHERE session_id=?",
                (sid,)).fetchone()
            return int(row[0]) if row else 0

    def clear_messages(self, sid: str) -> int:
        with self._tx() as c:
            cur = c.execute("DELETE FROM message WHERE session_id=?", (sid,))
            c.execute("UPDATE session SET updated_at=? WHERE id=?",
                      (_now(), sid))
            self._bump_seq(c, sid)
            return cur.rowcount

    # ------------------------------------------------------------ 统计
    def overview(self) -> Dict[str, int]:
        with self._lock:
            def one(sql: str) -> int:
                r = self._conn.execute(sql).fetchone()
                return int(r[0]) if r else 0
            return {
                "sessions": one("SELECT COUNT(*) FROM session"),
                "messages": one("SELECT COUNT(*) FROM message"),
            }
