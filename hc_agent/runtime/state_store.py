"""state_store.py — 状态增量落盘（移植自天枢一号 _persist_state）。

解决的问题：进程被 Kill 或原生库崩溃时，本工程只剩 checkpoints.json —— 它是
**累积列表且非原子写**，可能留下半截 JSON，比没有文件更难排查（解析失败又变
成"不知道发生了什么"）。

本模块提供：

  * ``write_json_atomic`` —— ``.tmp`` → ``fsync`` → ``os.replace`` 的原子写；
  * ``StateStore`` —— 每次检查点推进时落一份 ``state.json`` 遗言
    （``_persisted_stage`` / ``_persisted_at`` / ``_persisted_at_str``）。

**两种文件都保留，各有用途**：
  * ``checkpoints.json`` —— 累积历史列表，给人看进程轨迹；
  * ``state.json``       —— 最新遗言，给机器读"死在哪一步"。

> 注意：本工程原有的 ``evidence.write_json`` 用的是 ``Path.write_text``——
> 非原子、无 fsync。原子写在此单独实现，并已由 evidence 复用。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from hc_agent.errors import EXIT_INTERRUPTED, STATUS_INTERRUPTED


def write_json_atomic(path, obj: Any, *, fsync: bool = True) -> Path:
    """原子写 JSON：先写临时文件，fsync 后 replace。

    ``os.replace`` 在 Windows 与 POSIX 上都是原子的，因此读方看到的要么是旧
    完整文件、要么是新完整文件，**不会看到半截**。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=_default)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        if fsync:
            try:
                os.fsync(fh.fileno())
            except OSError:
                # 某些文件系统不支持 fsync；退化为普通写，不阻断
                pass
    os.replace(str(tmp), str(p))
    return p


def _default(o: Any) -> Any:
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if isinstance(o, Path):
        return str(o)
    return str(o)


class StateStore:
    """任务状态遗言。

    每个检查点写一次，**整体 try 包裹** —— 观测设施绝不能成为故障源。
    """

    def __init__(self, run_dir, *, task_id: str = "", run_id: str = "",
                 agent_version: str = "") -> None:
        self.path = Path(run_dir) / "state.json"
        self.task_id = task_id
        self.run_id = run_id
        self.agent_version = agent_version
        self._stage = ""
        self._history_path = Path(run_dir) / "state_history.jsonl"

    def persist(self, stage: str, *, status: str = "",
                extra: Optional[Dict[str, Any]] = None) -> bool:
        """写入最新遗言。返回是否成功（失败不抛异常）。"""
        self._stage = stage
        payload: Dict[str, Any] = {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "agent_version": self.agent_version,
            "status": status,
            "_persisted_stage": stage,
            "_persisted_at": time.time(),
            "_persisted_at_str": time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime()),
        }
        if extra:
            payload.update(extra)
        ok = False
        try:
            write_json_atomic(self.path, payload)
            ok = True
        except Exception:
            pass
        # 追加一行历史：便于事后看"阶段推进顺序"，与最新遗言互补。
        # 同样失败不影响主流程。
        try:
            with open(self._history_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(
                    {"stage": stage, "status": status,
                     "at": payload["_persisted_at_str"]},
                    ensure_ascii=False) + "\n")
        except Exception:
            pass
        return ok

    def mark_interrupted(self, reason: str,
                         *, stage: str = "") -> bool:
        """主动标记为 interrupted（**没来得及下结论**）。

        与"判失败"的区别是本工程新增的语义，来自天枢一号的论证：
          * ``SYSTEM_FAILED`` 类排查看错误码；
          * ``INTERRUPTED`` 排查看崩溃日志与最后落盘阶段；
          * 若归入 unknown，看板会把基础设施问题混进业务分布，
            成功率的分子分母都被污染。
        """
        return self.persist(stage or self._stage or "(未知)",
                            status=STATUS_INTERRUPTED,
                            extra={"exit_code": EXIT_INTERRUPTED,
                                   "interrupt_reason": reason})

    @staticmethod
    def read(path) -> Optional[Dict[str, Any]]:
        """读取遗言；解析失败返回 None（并**明确区别于"没有文件"**）。"""
        p = Path(path)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def last_stage(path) -> str:
        """取最后一次落盘阶段；无遗言时返回 '(无痕迹)'。

        这正是"崩在哪一步"的答案来源 —— 天枢一号在 run_metrics 里也是这么
        反推 interrupted 阶段的。
        """
        data = StateStore.read(path)
        if not data:
            return "(无痕迹)"
        return str(data.get("_persisted_stage") or "(无痕迹)")
