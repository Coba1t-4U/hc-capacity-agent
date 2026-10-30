"""crashlog.py — 崩溃可观测（移植自天枢一号 runtime/crashlog.py）。

本工程原来的困境：长任务崩溃后只剩"进程没了"，查不到死在哪一步、什么异常。

设计要点（均照搬天枢一号，且理由成立）：

  1. **绝不吞异常**。三个钩子记录之后**仍调用原钩子**，否则会改变宿主程序的
     既有行为（把可观测性做成副作用，是引入 bug 而不是修复）。
  2. **每条都 fsync**。进程可能在写下一条之前瞬间消失；fsync 保证缓冲区里的
     那条不丢 —— 这正是"崩在段错误前一个瞬间"要防的。
  3. **只保留一代**（`.1`）。崩溃日志是"最近发生了什么"的线索，多代轮转本身
     就占磁盘；长期留存应由外部收集。
  4. **启用公告不写崩溃日志**。否则后端每启动一次就多一条疑似异常记录，
     用户会以为反复出问题。

**必须诚实声明的局限**：Python 层**抓不到原生段错误**。本模块只能接住 Python
异常；进程被 Kill 或原生库崩溃时，可观测性靠的是"退出码 + 最后落盘的 state.json
遗言"，**不是**崩溃日志里的 traceback。本工程没有引入 faulthandler / minidump。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

_SEP = "=" * 78
_SUB = "-" * 78

_VALID_KINDS = ("excepthook", "thread", "unraisable", "manual", "watchdog")

DEFAULT_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB

_ENV_PATH = "HC_CRASH_LOG"
_ENV_MAX = "HC_CRASH_LOG_MAX_BYTES"

_state: Dict[str, Any] = {
    "installed": False,
    "path": None,
    "max_bytes": DEFAULT_MAX_BYTES,
    "seq": 0,          # records_written：记录意图（写盘前自增）
    "write_errors": 0,  # 写盘失败次数
    "by_kind": {},
    "prev_hooks": {},
}


def _env_max_bytes() -> int:
    raw = os.environ.get(_ENV_MAX)
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


def default_path() -> Path:
    explicit = os.environ.get(_ENV_PATH)
    if explicit:
        return Path(explicit).expanduser().resolve()
    # 放在工程 runs/_logs/ 下，与运行产物同处一地，便于一起打包取证
    here = Path(__file__).resolve()
    return (here.parents[2] / "runs" / "_logs" / "crash.log").resolve()


def _rotate_if_needed(path: Path, max_bytes: int, incoming: int) -> None:
    """单代轮转。改名失败**不阻塞记录** —— 宁可超限，也不能丢掉这条线索。"""
    try:
        if not path.exists():
            return
        size = path.stat().st_size
        if size + incoming <= max_bytes:
            return
        try:
            os.replace(str(path), str(path) + ".1")
        except OSError:
            pass
    except OSError:
        pass


def _append_block(block: str) -> None:
    """唯一写盘点：mkdir → 轮转 → append → flush → fsync。"""
    path = _state["path"]
    if path is None:
        return
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = block.encode("utf-8", errors="replace")
        _rotate_if_needed(p, int(_state["max_bytes"]), len(data))
        with open(p, "ab") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                # 某些文件系统不支持 fsync；失败不阻断记录
                pass
    except Exception:
        # 观测设施绝不能成为故障源：写不进去就计数，然后继续
        _state["write_errors"] += 1


def _format(kind: str, exc_type: Any, exc: Any, tb: Any,
            extra: Optional[Dict[str, Any]] = None) -> str:
    lines = [
        _SEP,
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 崩溃记录  kind={kind}  "
        f"pid={os.getpid()}  thread={threading.current_thread().name}",
        f"异常类型：{getattr(exc_type, '__name__', str(exc_type))}",
        f"异常信息：{exc}",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"{k}：{v}")
    lines.append(_SUB)
    try:
        lines.extend("".join(
            traceback.format_exception(exc_type, exc, tb)).rstrip().splitlines())
    except Exception:
        lines.append("(traceback 格式化失败)")
    lines.append(_SEP)
    lines.append("")
    return "\n".join(lines) + "\n"


def record(exc_type: Any, exc: Any, tb: Any, *, kind: str = "manual",
           extra: Optional[Dict[str, Any]] = None) -> None:
    """记录一条崩溃记录。未知 kind 归入 manual（不让归类错误本身变成问题）。"""
    k = kind if kind in _VALID_KINDS else "manual"
    _state["seq"] += 1
    _state["by_kind"][k] = _state["by_kind"].get(k, 0) + 1
    _append_block(_format(k, exc_type, exc, tb, extra))


def record_text(text: str, *, kind: str = "watchdog",
                extra: Optional[Dict[str, Any]] = None) -> None:
    """记录一段已格式化的文本（看门狗决策等非异常事件）。"""
    k = kind if kind in _VALID_KINDS else "manual"
    _state["seq"] += 1
    _state["by_kind"][k] = _state["by_kind"].get(k, 0) + 1
    lines = [
        _SEP,
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 崩溃记录  kind={k}  "
        f"pid={os.getpid()}  thread={threading.current_thread().name}",
    ]
    for kk, vv in (extra or {}).items():
        lines.append(f"{kk}：{vv}")
    lines.append(_SUB)
    lines.append(str(text).rstrip())
    lines.append(_SEP)
    lines.append("")
    _append_block("\n".join(lines) + "\n")


# ------------------------------------------------------------------ 钩子
def _excepthook(exc_type, exc, tb):
    try:
        record(exc_type, exc, tb, kind="excepthook",
               extra={"argv": " ".join(sys.argv[:8])})
    finally:
        prev = _state["prev_hooks"].get("excepthook")
        if prev is not None:
            prev(exc_type, exc, tb)


def _thread_hook(args):
    try:
        name = getattr(getattr(args, "thread", None), "name", "?")
        record(args.exc_type, args.exc_value, args.exc_traceback,
               kind="thread", extra={"thread": name})
    finally:
        prev = _state["prev_hooks"].get("thread")
        if prev is not None:
            prev(args)


def _unraisable_hook(args):
    try:
        record(args.exc_type, args.exc_value, args.exc_traceback,
               kind="unraisable",
               extra={"object": repr(getattr(args, "object", None))[:120]})
    finally:
        prev = _state["prev_hooks"].get("unraisable")
        if prev is not None:
            prev(args)


def install(path: Optional[str] = None,
            max_bytes: Optional[int] = None) -> bool:
    """安装崩溃可观测。幂等；已安装时只更新 path/max_bytes。

    **必须在做任何可能崩溃的初始化之前调用**（本工程在 Pipeline.run()
    与 CLI main() 的最开头调用）。
    """
    resolved = Path(path).expanduser().resolve() if path else default_path()
    mb = int(max_bytes) if max_bytes else _env_max_bytes()

    if _state["installed"]:
        _state["path"] = resolved
        _state["max_bytes"] = mb
        return True

    _state["path"] = resolved
    _state["max_bytes"] = mb
    try:
        _state["prev_hooks"]["excepthook"] = sys.excepthook
        sys.excepthook = _excepthook
        if hasattr(threading, "excepthook"):
            _state["prev_hooks"]["thread"] = threading.excepthook
            threading.excepthook = _thread_hook
        if hasattr(sys, "unraisablehook"):
            _state["prev_hooks"]["unraisable"] = sys.unraisablehook
            sys.unraisablehook = _unraisable_hook
        _state["installed"] = True
        return True
    except Exception:
        return False


def diagnostics() -> Dict[str, Any]:
    return {
        "installed": bool(_state["installed"]),
        "path": str(_state["path"]) if _state["path"] else None,
        "max_bytes": int(_state["max_bytes"]),
        "records_written": int(_state["seq"]),
        "write_errors": int(_state["write_errors"]),
        "by_kind": dict(_state["by_kind"]),
        "limitation": ("Python 层抓不到原生段错误；进程被 Kill 或原生库崩溃时，"
                       "可观测性依赖退出码与最后落盘的 state.json"),
    }


def reset_for_tests() -> None:
    """仅供测试：清计数并卸载钩子（不还原线程）。"""
    for key, hook in (("excepthook", "excepthook"),
                      ("thread", "thread"),
                      ("unraisable", "unraisable")):
        prev = _state["prev_hooks"].get(key)
        if prev is None:
            continue
        try:
            if key == "excepthook":
                sys.excepthook = prev
            elif key == "thread" and hasattr(threading, "excepthook"):
                threading.excepthook = prev
            elif key == "unraisable" and hasattr(sys, "unraisablehook"):
                sys.unraisablehook = prev
        except Exception:
            pass
    _state.update({"installed": False, "seq": 0, "write_errors": 0,
                   "by_kind": {}, "prev_hooks": {}})
