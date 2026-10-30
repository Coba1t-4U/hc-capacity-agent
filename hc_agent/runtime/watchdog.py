"""watchdog.py — 引擎看门狗与自愈（移植自天枢一号 runtime/watchdog.py）。

**要解决的现实问题（本工程实测过的同类故障）**：
  进程内 OpenDSS 原生库一旦进入坏状态，`Solution.Solve()` 可能**永不返回**。
  此时 Python 层没有任何机会执行代码——没有异常、没有超时、没有日志，
  整个进程就是"卡住不动"。纯 Python 的超时机制（`signal` / `thread.join`）
  **无法中断卡在原生调用里的线程**。

**对策 —— 两条互补判据**：

  判据 1（主，事件驱动）：任务因引擎错误失败时，立刻**再问引擎一句**
      ``Version``。秒回 ⇒ 只是算例慢/单次失败，**绝不重启**；
      超时 ⇒ 判"引擎中毒" ⇒ 触发重启。这是区分「算例慢」与「引擎中毒」的
      唯一可靠手段——同一个超时现象有两种成因，只能靠复检分辨。

  判据 2（兜底，定时器）：每 5 秒检查"是否有任务持有时间超过 300 秒"。
      主线程被原生库彻底卡住时，只有这条能救。

**恢复方式：不在进程内重启，而是 ``os._exit(70)`` 交给外层。**
  线程卡在原生调用里时 Python 无法强杀它，只有**进程边界**能清理资源。
  70 号退出码由 Electron 桌面壳监听并重启后端。

**前置条件（必须成立，否则本模块有害无益）**：
  必须有"外层重启者"。若本工程以一次性 CLI 方式运行而无守护者，
  使用 ``trigger_restart()`` 只会让任务静默消失——**比不退出更糟**。
  ``install()`` 会检查该条件并在不满足时明确拒绝，而不是默默照做。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from hc_agent.errors import (
    ERR_DSS_DEPENDENCY_LOAD_FAILED,
    ERR_DSS_RESOURCE_EXHAUSTED,
    ERR_DSS_TIMEOUT,
    ERR_DSS_UNKNOWN,
    WATCHDOG_EXIT_CODE,
)

# ------------------------------------------------------------ 可调常量
# 默认值全部照搬天枢一号实测值（探测 5s / 复检 15s / 宽限 5s / 卡死 300s /
# 熔断窗口 30min / 熔断上限 2 次），可用环境变量覆盖。
_INTERVAL_S = float(os.environ.get("HC_WATCHDOG_INTERVAL_S", "5"))
_PROBE_TIMEOUT_S = float(os.environ.get("HC_WATCHDOG_PROBE_TIMEOUT_S", "15"))
_PROBE_GRACE_S = float(os.environ.get("HC_WATCHDOG_PROBE_GRACE_S", "5"))
_TASK_STUCK_S = float(os.environ.get("HC_WATCHDOG_TASK_STUCK_S", "300"))
_EXIT_CODE = int(os.environ.get("HC_WATCHDOG_EXIT_CODE", str(WATCHDOG_EXIT_CODE)))
_BREAKER_WINDOW_S = float(os.environ.get("HC_WATCHDOG_BREAKER_WINDOW_S", "1800"))
_BREAKER_MAX = int(os.environ.get("HC_WATCHDOG_BREAKER_MAX", "2"))
_ENABLED = (os.environ.get("HC_WATCHDOG_ENABLED", "1").strip().lower()
            not in ("0", "false", "off", "no"))

# 只有这些错误码才值得花十几秒去复检引擎。
# **过滤条件必须保留**：业务失败（输入阻断、基线违规等）也去复检的话，
# 每个失败任务都白等 20 秒，而且毫无信息量。
_REPORTABLE_CODES = (
    ERR_DSS_TIMEOUT,
    ERR_DSS_DEPENDENCY_LOAD_FAILED,
    ERR_DSS_RESOURCE_EXHAUSTED,
    ERR_DSS_UNKNOWN,
)

_state: Dict[str, Any] = {
    "enabled": _ENABLED,
    "started": False,
    "probes": 0,
    "probe_failures": 0,
    "triggers": 0,
    "breaker_blocked": 0,
    "last_probe": "",
    "last_reason": "",
    "last_trigger_at": None,
    "exit_calls": [],
    "history_path": None,
    "restart_supervisor": False,
    "lock_provider": None,
    "thread": None,
}

_lock = threading.Lock()


# ------------------------------------------------------------ 任务锁登记
# 本工程原本没有"当前任务持有时长"的概念。看门狗的兜底判据需要一个可查询的
# 接缝；这里提供最小实现（15 行），由 Pipeline 在开始时登记、结束时释放。
_task_lock = threading.Lock()
_current_task: Dict[str, Any] = {"locked": False, "task_id": None, "since": None}


def acquire_task(task_id: str) -> None:
    with _task_lock:
        _current_task["locked"] = True
        _current_task["task_id"] = task_id
        _current_task["since"] = time.time()


def release_task() -> None:
    with _task_lock:
        _current_task["locked"] = False
        _current_task["task_id"] = None
        _current_task["since"] = None


def current_task_lock() -> Dict[str, Any]:
    with _task_lock:
        return dict(_current_task)


# --------------------------------------------------------------- 探针
def _probe(adapter: Any) -> Tuple[Optional[bool], str]:
    """复检引擎是否还活着。返回 (True/False/None, 说明)。

    None 表示**无法判定**（没有可用的适配器/接口不符）——此时绝不重启。
    """
    if adapter is None:
        return None, "无适配器，无法判定"
    probe_fn = getattr(adapter, "probe", None)
    if not callable(probe_fn):
        return None, "适配器未提供 probe() 接口，无法判定"

    # 双层超时：内层给适配器自己的超时，外层再用线程兜底。
    # 两者都要有 —— 线程卡在原生调用里时，内层超时根本执行不到。
    budget = _PROBE_TIMEOUT_S + _PROBE_GRACE_S
    box: Dict[str, Any] = {}

    def _run() -> None:
        # 必须捕获 BaseException 而非 Exception：SystemExit / KeyboardInterrupt
        # 派生自 BaseException，只捕 Exception 会让它们穿透本线程，
        # 使 box 留空 → 被误判为"无法判定"（从而静默放过一个已经坏掉的引擎）。
        # 实测：探针内 raise SystemExit 时，except Exception 捕获不到。
        try:
            box["ok"] = probe_fn(timeout_s=_PROBE_TIMEOUT_S)
        except BaseException as exc:
            box["ok"] = False
            box["err"] = f"探针异常 {type(exc).__name__}: {exc}"

    t = threading.Thread(target=_run, name="engine-watchdog-probe", daemon=True)
    t.start()
    t.join(budget)
    if t.is_alive():
        # 线程仍活着 ⇒ 引擎调用确实卡死。这比"超时返回"更严重。
        return False, f"复检超过 {budget:.1f} 秒未返回"
    if "err" in box:
        # 探针抛出 = 引擎已不可用（不是"无法判定"）。
        # 与"没有探针接口"不同：接口存在但调用就炸，说明引擎坏了。
        return False, str(box["err"])
    ok = box.get("ok")
    if ok is None:
        return None, "探针未返回结论"
    return bool(ok), ("引擎健康" if ok else "引擎无响应")


def note_engine_failure(adapter: Any, detail: str = "") -> Dict[str, Any]:
    """任务因引擎错误失败时调用：复检并决定是否重启。

    **调用时机有严格要求**：必须在释放电路/上下文**之前**调用，
    否则复检对象已失效 → 假阳性重启。

    三态返回：
      * ``action="kept-running"``  —— 复检健康，只是这次慢/单次失败，**不重启**
      * ``action="restart"``       —— 复检无响应，判中毒，触发重启
      * ``action="unavailable"``   —— 无法判定，**不重启**
    """
    with _lock:
        _state["probes"] += 1
        _state["last_probe"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        ok, why = _probe(adapter)
    except BaseException as exc:
        # 探针**永不抛出**：可观测/自愈机制自身出错，不能反过来搞垮任务。
        # 同样用 BaseException —— SystemExit 等不能从这里逃出去。
        ok, why = None, f"复检异常: {type(exc).__name__}: {exc}"

    if ok is None:
        return {"action": "unavailable", "reason": why, "detail": detail}
    if ok:
        return {"action": "kept-running", "reason": why, "detail": detail}

    with _lock:
        _state["probe_failures"] += 1
    decision = trigger_restart(f"引擎复检无响应：{why}",
                               extra={"detail": detail})
    return {"action": "restart" if decision.get("exited") else "breaker-open",
            "reason": why, "detail": detail, "trigger": decision}


def is_reportable_error(code: Optional[str]) -> bool:
    """该错误码是否值得触发引擎复检（避免业务失败白等 20 秒）。"""
    return bool(code) and code in _REPORTABLE_CODES


# ------------------------------------------------------------- 熔断与退出
def _default_history_path() -> Path:
    here = Path(__file__).resolve()
    return here.parents[2] / "runs" / "_logs" / "watchdog_restarts.json"


def _read_history() -> list:
    """读重启历史。解析失败当空列表 —— **宁可少一次熔断计数，
    也不能因此拒绝重启**（重启是救命的，熔断只是防风暴的）。"""
    p = _state.get("history_path") or _default_history_path()
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        stamps = data.get("restart_at") or []
        return [float(x) for x in stamps if isinstance(x, (int, float))]
    except Exception:
        return []


def _append_history(now: float) -> None:
    p = _state.get("history_path") or _default_history_path()
    try:
        stamps = (_read_history() + [float(now)])[-20:]
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        Path(p).write_text(json.dumps({"restart_at": stamps}),
                           encoding="utf-8")
    except Exception:
        # 写失败不影响退出决策
        pass


def _breaker_open(now: float) -> Tuple[bool, int]:
    recent = [t for t in _read_history() if now - t < _BREAKER_WINDOW_S]
    return len(recent) >= _BREAKER_MAX, len(recent)


def _do_exit(code: int, reason: str) -> None:
    """真正的退出动作。测试里会被替换掉。"""
    try:
        sys_stderr(f"看门狗：{reason}；以退出码 {code} 退出，交由外层重启")
    except Exception:
        pass
    os._exit(code)


# 可替换的退出函数：测试需要拦截"进程真的退出"这件事
_exit_fn: Callable[[int, str], None] = _do_exit


def set_exit_hook(fn: Optional[Callable[[int, str], None]]) -> None:
    """测试用：替换退出动作。传 None 恢复默认。"""
    global _exit_fn
    _exit_fn = fn or _do_exit


def sys_stderr(text: str) -> None:
    import sys
    print(text, file=sys.stderr, flush=True)


def trigger_restart(reason: str,
                    extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """请求重启：先查熔断，未熔断则退出进程。

    熔断逻辑防的是**重启风暴**：如果重启解决不了（例如 Python 环境损坏、
    端口被长期占用），继续重启只会让界面永远转圈。达到上限就停下并说清
    去哪看日志，把问题摆到台面上。
    """
    now = time.time()
    with _lock:
        _state["triggers"] += 1
        _state["last_reason"] = reason
        _state["last_trigger_at"] = now

    open_, recent = _breaker_open(now)
    if open_:
        with _lock:
            _state["breaker_blocked"] += 1
        return {"exited": False, "breaker_open": True, "recent": recent,
                "reason": reason,
                "note": f"近 {_BREAKER_WINDOW_S:.0f} 秒内已重启 {recent} 次"
                        f"（上限 {_BREAKER_MAX}），放弃重启"}

    _append_history(now)
    with _lock:
        _state["exit_calls"].append({"at": now, "reason": reason})
    _exit_fn(_EXIT_CODE, reason)
    return {"exited": True, "breaker_open": False, "recent": recent,
            "reason": reason, "exit_code": _EXIT_CODE}


# ------------------------------------------------------------- 兜底定时器
def _check_once() -> None:
    """兜底判据：任务持有超时视为引擎卡死。"""
    try:
        info = current_task_lock()
    except Exception:
        return
    # 没锁 / 信息残缺 → **不误判**
    if not info.get("locked") or not info.get("since"):
        return
    held = time.time() - float(info["since"])
    if held >= _TASK_STUCK_S:
        trigger_restart(
            f"任务 {info.get('task_id')} 已持有 {held:.0f} 秒未结束"
            f"（引擎调用连超时机制都未生效）",
            extra={"task_id": info.get("task_id")})


def _loop() -> None:
    while _state["enabled"]:
        time.sleep(_INTERVAL_S)
        if not _state["enabled"]:
            break
        _check_once()


def install(*, require_supervisor: bool = True,
            supervisor_present: Optional[bool] = None,
            history_path: Optional[str] = None) -> bool:
    """启动看门狗。幂等；线程起不来则静默降级返 False。

    ``require_supervisor``：是否需要"外层有重启者"。默认 True ——
    没有重启者时退出进程只会让任务静默消失，**比不退出更糟**，
    因此默认拒绝安装而不是照做。
    """
    if not _state["enabled"]:
        return False

    has_supervisor = (_state.get("restart_supervisor")
                      if supervisor_present is None else bool(supervisor_present))
    if require_supervisor and not has_supervisor:
        sys_stderr(
            "看门狗未启动：未检测到外层重启者（需 Electron 壳或等效守护）。\n"
            "  原因：引擎中毒时只能靠退出进程来清理资源，若无外层重启，\n"
            "        任务会静默消失，比不退出更糟。\n"
            "  如需在无守护环境下强制启用，设 HC_WATCHDOG_REQUIRE_SUPERVISOR=0。")
        _state["started"] = False
        return False

    with _lock:
        if _state["started"]:
            if history_path:
                _state["history_path"] = history_path
            return True
        _state["history_path"] = history_path or _default_history_path()
        try:
            t = threading.Thread(target=_loop, name="engine-watchdog",
                                 daemon=True)
            t.start()
            _state["thread"] = t
            _state["started"] = True
        except Exception:
            _state["started"] = False
            return False

    # 启用公告打到 stdout，**明确不写崩溃日志** —— 否则每启动一次就多一条
    # 疑似异常记录，用户会以为反复出问题。真正落崩溃日志的只有「决策」。
    sys_stderr(f"看门狗已启动：间隔 {_INTERVAL_S:g}s，复检超时 "
               f"{_PROBE_TIMEOUT_S:g}s，卡死阈值 {_TASK_STUCK_S:g}s，"
               f"熔断 {_BREAKER_MAX} 次 / {_BREAKER_WINDOW_S:g}s")
    return True


def shutdown() -> None:
    _state["enabled"] = False


def diagnostics() -> Dict[str, Any]:
    now = time.time()
    open_, recent = _breaker_open(now)
    return {
        "enabled": bool(_state["enabled"]),
        "started": bool(_state["started"]),
        "probes": _state["probes"],
        "probe_failures": _state["probe_failures"],
        "triggers": _state["triggers"],
        "breaker_blocked": _state["breaker_blocked"],
        "last_probe": _state["last_probe"],
        "last_reason": _state["last_reason"],
        "last_trigger_at": _state["last_trigger_at"],
        "breaker_open": open_,
        "restarts_in_window": recent,
        "restart_supervisor": bool(_state.get("restart_supervisor")),
        "exit_calls": len(_state["exit_calls"]),
        "params": {
            "interval_s": _INTERVAL_S,
            "probe_timeout_s": _PROBE_TIMEOUT_S,
            "probe_grace_s": _PROBE_GRACE_S,
            "task_stuck_s": _TASK_STUCK_S,
            "exit_code": _EXIT_CODE,
            "breaker_max": _BREAKER_MAX,
            "breaker_window_s": _BREAKER_WINDOW_S,
        },
    }


def reset_for_tests() -> None:
    """仅供测试：清计数（不还原线程）。"""
    with _lock:
        _state.update({
            "enabled": _ENABLED, "started": False, "probes": 0,
            "probe_failures": 0, "triggers": 0, "breaker_blocked": 0,
            "last_probe": "", "last_reason": "", "last_trigger_at": None,
            "exit_calls": [], "thread": None,
        })
    release_task()
