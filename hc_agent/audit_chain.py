"""audit_chain.py — 哈希链审计日志。

## 为什么承载力评估需要哈希链

承载力结论是要拿去报备、审签、甚至并网许可的。所以除了"结果对不对"，
还得回答"这份结果有没有被改过"。

普通的 JSONL 日志做不到这一点：谁都能改一行而不留痕。
**把每条事件的哈希指向上一条的哈希**，就得到一条链：
改了任意一条，后面所有哈希都对不上，`verify()` 一眼能查出来。

## 与现有 events.jsonl 的关系

本工程原有的 `audit/events.jsonl` 记录"发生了什么"，字段是业务语义的。
这份 `audit/chain.jsonl` 是**新增的完整性层**，不改原有格式 ——
原有查看器、报告、测试都不受影响。

两者可以按 `event_id` 对上，所以既能读懂、又能验真。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="microseconds")


def _digest(payload: Dict[str, Any]) -> str:
    """对事件的**规范化 JSON** 求 sha256。

    必须用 `sort_keys` + 定分隔符：否则同样的内容因为键顺序不同会得到不同哈希，
    校验就会假失败。
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


#: 哈希链能锁住的字段（其余字段只做记录，不参与哈希）
_HASHED_FIELDS = ("seq", "event_id", "run_id", "action", "status",
                  "actor", "created_at", "object_ref", "detail",
                  "previous_hash")


class AuditChain:
    """只追加的哈希链日志。线程安全（一次运行里可能有多个写者）。"""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 内部
    def _last(self) -> Optional[Dict[str, Any]]:
        if not self.path.is_file():
            return None
        last = ""
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip():
                        last = line
        except Exception:
            return None
        if not last:
            return None
        try:
            return json.loads(last)
        except Exception:
            return None

    # ------------------------------------------------------------ 写入
    def append(self, *, action: str, status: str, run_id: str = "",
               actor: str = "hc_agent", object_ref: str = "",
               detail: Optional[Dict[str, Any]] = None,
               event_id: str = "") -> Dict[str, Any]:
        """追加一条事件，返回写入的记录（含 seq / 两个哈希）。"""
        with self._lock:
            prev = self._last() or {}
            seq = int(prev.get("seq") or 0) + 1
            rec: Dict[str, Any] = {
                "seq": seq,
                "event_id": event_id or f"AC-{seq:06d}",
                "run_id": run_id,
                "action": action,
                "status": status,
                "actor": actor,
                "created_at": _now(),
                "object_ref": object_ref,
                "detail": detail or {},
                "previous_hash": str(prev.get("event_hash") or ""),
            }
            core = {k: rec.get(k) for k in _HASHED_FIELDS}
            rec["event_hash"] = _digest(core)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return rec

    # ------------------------------------------------------------ 校验
    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        # 坏行不能被当成"没事"：留一条占位，让 verify 报出来
                        out.append({"_corrupt": line[:120]})
        except Exception:
            pass
        return out

    def verify(self) -> Dict[str, Any]:
        """校验整条链。

        返回 `{"ok", "count", "broken_at", "reason"}`。
        `broken_at` 是第一条对不上的序号（1 起），没断就是 None。
        """
        rows = self.read_all()
        if not rows:
            return {"ok": True, "count": 0, "broken_at": None,
                    "reason": "日志为空"}
        prev_hash = ""
        for i, rec in enumerate(rows, 1):
            if "_corrupt" in rec:
                return {"ok": False, "count": len(rows), "broken_at": i,
                        "reason": "该行不是合法 JSON（被改动或截断）"}
            try:
                core = {k: rec.get(k) for k in _HASHED_FIELDS}
                expect = _digest(core)
            except Exception as exc:
                return {"ok": False, "count": len(rows), "broken_at": i,
                        "reason": f"无法计算哈希：{exc}"}
            if rec.get("event_hash") != expect:
                return {"ok": False, "count": len(rows), "broken_at": i,
                        "reason": "内容与记录的哈希不符（内容被改过）"}
            if str(rec.get("previous_hash") or "") != prev_hash:
                return {"ok": False, "count": len(rows), "broken_at": i,
                        "reason": "previous_hash 与上一条的 event_hash 不匹配"
                                  "（中间被插入或删除过记录）"}
            prev_hash = str(rec.get("event_hash") or "")
        return {"ok": True, "count": len(rows), "broken_at": None,
                "reason": "整条链校验通过"}

    def summary(self) -> Dict[str, Any]:
        """给界面/报告用的一句话摘要。"""
        v = self.verify()
        rows = self.read_all()
        return {
            "ok": v["ok"],
            "count": v["count"],
            "broken_at": v["broken_at"],
            "reason": v["reason"],
            "head_hash": (str(rows[-1].get("event_hash") or "")
                          if rows and "event_hash" in rows[-1] else ""),
            "path": str(self.path),
        }


# ---------------------------------------------------------------- 便捷入口
def chain_for_run(run_dir) -> AuditChain:
    """取某次运行的审计链（`<run>/audit/chain.jsonl`）。"""
    return AuditChain(Path(str(run_dir)) / "audit" / "chain.jsonl")


def seal_run(run_dir, *, run_id: str = "") -> Dict[str, Any]:
    """给一次已有运行"补建"哈希链：把现有 events.jsonl 逐条打链。

    用途：老运行本来没有链，现在可以一键补上（内容取原文，不改原文件）。
    """
    root = Path(str(run_dir))
    chain = chain_for_run(root)
    events = root / "audit" / "events.jsonl"
    if not events.is_file():
        return {"ok": False, "count": 0, "reason": "没有 events.jsonl"}
    if chain.read_all():
        return {"ok": True, "count": len(chain.read_all()),
                "reason": "已有链，未重复补建"}
    n = 0
    try:
        with open(events, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                detail = {k: ev.get(k) for k in
                          ("input_digest", "after_hash", "result", "reason")
                          if ev.get(k) not in (None, "")}
                chain.append(
                    action=str(ev.get("action") or "unknown"),
                    status=str(ev.get("status") or "ok"),
                    run_id=str(ev.get("run_id") or run_id),
                    actor=str(ev.get("actor") or "hc_agent"),
                    object_ref=str(ev.get("object_ref") or ""),
                    detail=detail,
                    event_id=str(ev.get("event_id") or ""))
                n += 1
    except Exception as exc:
        return {"ok": False, "count": n, "reason": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "count": n, "reason": f"已补建 {n} 条事件"}
