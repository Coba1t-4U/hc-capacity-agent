"""engine_host.py — 桌面端的计算后端子进程（行分隔 JSON over stdio）。

**它是独立进程，且这是刻意的**：看门狗判定引擎中毒时会 `os._exit(70)`，
只有进程边界能清理卡在原生调用里的线程。若引擎跑在 GUI 进程内，那个退出
会把界面一起带走。所以：

    GUI ──spawn/监督/必要时重启──▶ engine_host ──▶ OpenDSS

没有 HTTP、没有端口、没有 Web 框架：请求在 stdin 上按行读，应答在 stdout
上按行写。stdout **只允许**协议行，任何库的 print 都会被父进程当成普通日志
转发，因此本文件里不得有裸 print。

协议
----
请求： ``{"cmd": "<name>", "args": {...}, "tag": "<唯一串>"}``
应答： ``{"kind": "result", "tag": "<同串>", "ok": true, ...}``
就绪： ``{"kind": "ready", "channel": "...", "diagnostics": {...}}``
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict

from hc_agent import paths

# 打包后 __file__ 指向 _internal，必须走 paths 统一解析
_PROJECT_ROOT = paths.bundle_root()


def _out(obj: Dict[str, Any]) -> None:
    """唯一写出口。任何其他东西写 stdout 都会污染协议。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _log(text: str) -> None:
    """日志一律走 stderr，父进程转发给界面。"""
    sys.stderr.write(str(text).rstrip() + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 命令实现
def cmd_ping(_args: dict) -> dict:
    return {"ok": True, "pong": True, "pid": os.getpid()}


def cmd_diagnostics(_args: dict) -> dict:
    """汇总运行时诊断。**每段独立 try** —— 诊断接口自身报错会掩盖被诊断
    的问题，因此取不到就返回 error 字段，而不是整体失败。"""
    out: Dict[str, Any] = {"service": "hc-agent-host", "pid": os.getpid(),
                           "python": sys.version.split()[0]}
    try:
        from hc_agent.runtime import crashlog
        out["crashlog"] = crashlog.diagnostics()
    except Exception as exc:
        out["crashlog"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from hc_agent.runtime import watchdog
        out["watchdog"] = watchdog.diagnostics()
    except Exception as exc:
        out["watchdog"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from hc_agent import __version__
        out["agent_version"] = __version__
    except Exception:
        pass
    try:
        from hc_agent.desktop import runs as runs_mod
        # 「最近一次运行」必须挑**有结论**的那一次。
        # 只取目录最新会让没有 run_outcome.json 的运行（如研究型 RM001）占位，
        # 界面于是显示"还没有跑过任务"，而右侧明明写着"运行记录 4" —— 自相矛盾
        # （实测踩过）。
        all_runs = runs_mod.list_runs()
        chosen = next((r for r in all_runs
                       if r.get("exit_code") is not None), None)
        out["latest_run"] = (chosen or (all_runs[0] if all_runs else {})).get(
            "run_id", "")
        if chosen:
            out["state"] = runs_mod.read_state(chosen["path"]) or {}
            out["last_stage"] = out["state"].get("_persisted_stage", "(无痕迹)")
            out["run_outcome"] = runs_mod.read_outcome(chosen["path"]) or {}
        else:
            out["last_stage"] = "(无痕迹)"
    except Exception as exc:
        out["runs_error"] = f"{type(exc).__name__}: {exc}"
    return {"ok": True, "diagnostics": out}


def cmd_channels(_args: dict) -> dict:
    try:
        from hc_agent.engines import engine_channels
        health = engine_channels.probe_all()
        ch, info = engine_channels.select_channel(fallback=True)
        return {"ok": True, "channels": {k: v.to_dict() for k, v in health.items()},
                "selected": ch, "selection": info}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_runs(_args: dict) -> dict:
    try:
        from hc_agent.desktop import runs as runs_mod
        return {"ok": True, "runs": runs_mod.list_runs()}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_run_detail(args: dict) -> dict:
    try:
        from hc_agent.desktop import runs as runs_mod
        d = runs_mod.run_detail(str(args.get("run_id", "")))
        return {"ok": True, "detail": d}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_run_task(args: dict) -> dict:
    """执行一次承载力评估（在子进程里跑，界面保持响应）。

    这是桌面端相对"只读看板"的关键增益：**能真正发起计算**。
    """
    try:
        from hc_agent.desktop import runs as runs_mod
        return runs_mod.execute_task(
            input_dir=str(args.get("input_dir", "")),
            output_dir=str(args.get("output_dir", "")),
            on_progress=lambda stage, status: _out(
                {"kind": "progress", "stage": stage, "status": status}))
    except Exception as exc:
        return {"ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()}


def cmd_read_text(args: dict) -> dict:
    """读运行目录内的文本产物（报告、明细表等），**限制在 runs/ 内**。"""
    try:
        from hc_agent.desktop import runs as runs_mod
        text = runs_mod.read_artifact(str(args.get("run_id", "")),
                                      str(args.get("rel", "")))
        return {"ok": True, "text": text}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def cmd_shutdown(_args: dict) -> dict:
    _out({"kind": "result", "ok": True, "shutdown": True})
    raise SystemExit(0)


COMMANDS: Dict[str, Callable[[dict], dict]] = {
    "ping": cmd_ping,
    "diagnostics": cmd_diagnostics,
    "channels": cmd_channels,
    "runs": cmd_runs,
    "run_detail": cmd_run_detail,
    "run_task": cmd_run_task,
    "read_text": cmd_read_text,
    "shutdown": cmd_shutdown,
}


def dispatch(req: dict) -> dict:
    cmd = str(req.get("cmd", ""))
    tag = req.get("tag")
    fn = COMMANDS.get(cmd)
    if fn is None:
        return {"kind": "result", "tag": tag, "ok": False,
                "error": f"未知命令 {cmd}"}
    try:
        res = fn(req.get("args") or {})
    except SystemExit:
        raise
    except Exception as exc:
        res = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
               "traceback": traceback.format_exc()}
    res.setdefault("ok", True)
    res["kind"] = "result"
    res["tag"] = tag
    return res


def main() -> int:
    # 先把可写目录建好：GUI 与引擎子进程会同时首次创建同一个数据目录，
    # 交给带重试的容错函数处理竞态，避免第一次运行就因 PermissionError 失败
    # （打包后实测踩过：GUI 已创建上层目录的瞬间，子进程建子目录报 WinError 5）。
    paths.ensure_writable(paths.data_root(), paths.logs_root(),
                          paths.runs_root())

    # 崩溃可观测必须在任何可能崩溃的初始化**之前**安装
    from hc_agent.runtime import crashlog, watchdog
    crashlog.install()
    try:
        watchdog.install(require_supervisor=True,
                         supervisor_present=bool(
                             os.environ.get("HC_WATCHDOG_SUPERVISOR")))
    except Exception as exc:
        crashlog.record_text(f"看门狗安装失败: {exc}", kind="manual")

    # 预选引擎通道，并把它作为"就绪"信号报给父进程
    channel, diag = "", {}
    try:
        from hc_agent.engines import engine_channels
        channel, info = engine_channels.select_channel(fallback=True) or ("", {})
        diag = {"selection": info,
                "capability": (info or {}).get("capability")}
    except Exception as exc:
        diag = {"error": f"{type(exc).__name__}: {exc}"}
    _out({"kind": "ready", "channel": channel or "", "diagnostics": diag,
          "pid": os.getpid()})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            _out({"kind": "result", "ok": False, "error": "请求不是合法 JSON",
                  "raw": line[:200]})
            continue
        _out(dispatch(req))
    return 0


if __name__ == "__main__":
    sys.exit(main())
