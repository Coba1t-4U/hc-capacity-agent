"""jobs.py — 异步索引任务（可查询进度、可取消）。

对齐 ai-localbase 的「批量文档支持异步索引 Job，可查询进度并取消任务」。

## 为什么要异步

建索引要读文件、切块、算词表与 TF-IDF 矩阵。文档多时需数秒到数十秒，
放在界面线程会卡死窗口（Tk 单线程）。所以任务在后台线程跑，
界面轮询 `progress()` 显示进度。

## 取消的语义

取消是**协作式**的：任务每处理完一个文档检查一次 `cancel_req` 标记，
发现被要求取消就停下，把已完成的文档保留、状态记为 cancelled。
不做强杀 —— 强杀会让已写入的块与索引不一致。

## 线程与 Tk

工作线程**绝不碰任何控件**，只写数据库。界面通过轮询数据库拿进度，
避免 Tk 跨线程崩溃。
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hc_agent.knowledge.store import Store, now


@dataclass
class Progress:
    job_id: str
    kind: str
    status: str
    total: int = 0
    done: int = 0
    message: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 100 if self.status in ("done", "failed", "cancelled") else 0
        return max(0, min(100, round(self.done * 100 / self.total)))

    @property
    def running(self) -> bool:
        return self.status in ("queued", "running")

    def to_dict(self) -> Dict[str, Any]:
        return {"job_id": self.job_id, "kind": self.kind,
                "status": self.status, "total": self.total,
                "done": self.done, "percent": self.percent,
                "message": self.message, "running": self.running,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at}


#: 任务函数签名：fn(job_id, report) -> dict
#: report(done=..., total=..., message=...) 用于更新进度
Reporter = Callable[..., None]


class JobManager:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------- 提交
    def submit(self, kind: str, fn: Callable, *, kb_id: str = "",
               total: int = 0, message: str = "") -> Progress:
        job = self.store.add_job(kind, kb_id=kb_id, total=total,
                                 message=message)
        jid = job["id"]

        def _report(**kw) -> None:
            self.store.update_job(jid, **kw)

        def _run() -> None:
            self.store.update_job(jid, status="running", started_at=now())
            try:
                result = fn(jid, _report) or {}
                if self.store.cancel_requested(jid):
                    self.store.update_job(
                        jid, status="cancelled", finished_at=now(),
                        message=str(result.get("message") or "已取消"))
                else:
                    self.store.update_job(
                        jid, status="done", finished_at=now(),
                        message=str(result.get("message") or "完成"),
                        done=int(result.get("done", 0) or 0))
            except Exception as exc:
                self.store.update_job(
                    jid, status="failed", finished_at=now(),
                    message=f"{type(exc).__name__}: {exc}")
                traceback.print_exc()

        th = threading.Thread(target=_run, daemon=True, name=f"kbjob-{jid}")
        with self._lock:
            self._threads[jid] = th
        th.start()
        return self.progress(jid) or Progress(jid, kind, "queued")

    # ---------------------------------------------------------- 查询
    def progress(self, job_id: str) -> Optional[Progress]:
        j = self.store.get_job(job_id)
        if not j:
            return None
        return Progress(job_id=j["id"], kind=j["kind"], status=j["status"],
                        total=int(j.get("total") or 0),
                        done=int(j.get("done") or 0),
                        message=j.get("message") or "",
                        created_at=j.get("created_at") or "",
                        started_at=j.get("started_at") or "",
                        finished_at=j.get("finished_at") or "")

    def list(self, kb_id: str = "", limit: int = 30) -> List[Progress]:
        out: List[Progress] = []
        for j in self.store.list_jobs(kb_id=kb_id, limit=limit):
            out.append(Progress(job_id=j["id"], kind=j["kind"],
                                status=j["status"],
                                total=int(j.get("total") or 0),
                                done=int(j.get("done") or 0),
                                message=j.get("message") or "",
                                created_at=j.get("created_at") or "",
                                started_at=j.get("started_at") or "",
                                finished_at=j.get("finished_at") or ""))
        return out

    def active(self, kb_id: str = "") -> List[Progress]:
        return [p for p in self.list(kb_id=kb_id) if p.running]

    # ---------------------------------------------------------- 取消
    def cancel(self, job_id: str) -> Dict[str, Any]:
        j = self.store.get_job(job_id)
        if not j:
            return {"ok": False, "error": "任务不存在"}
        if j["status"] not in ("queued", "running"):
            return {"ok": False, "error": f"任务已结束（{j['status']}）"}
        self.store.update_job(job_id, cancel_req=1,
                              message="已请求取消，等待当前文档处理完…")
        return {"ok": True, "job_id": job_id}

    def join(self, job_id: str, timeout: float = 120.0) -> None:
        """等某个任务结束（测试与命令行用；界面不要调，会卡界面）。"""
        with self._lock:
            th = self._threads.get(job_id)
        if th is not None:
            th.join(timeout)
