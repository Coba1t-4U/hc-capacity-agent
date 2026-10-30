"""backend.py — 桌面端对计算后端的**进程内受管**调用。

**为什么不让 GUI 直接 import 引擎**：
看门狗的契约是"引擎中毒时 `os._exit(70)` 请求外层重启"。如果引擎跑在 GUI
进程里，那个 `os._exit` 会把**整个界面**一起杀掉 —— 界面消失得毫无痕迹，
比不退出更糟。所以：

    桌面端(GUI)  ──spawn/监督──▶  后端子进程(engine_host)  ──▶ OpenDSS

    桌面端是"外层重启者"，退出码 70 的契约在这里才成立。

**通信走管道，不走 HTTP**：子进程用行分隔 JSON 在 stdin/stdout 上应答。
这样整个应用没有 HTTP 服务器、没有端口、没有浏览器 —— 是原生桌面程序。

GUI 必须保持响应，因此调用是**异步**的：请求交给工作线程，结果用轮询队列
回主线程（Tk 不是线程安全的，只能在主线程碰控件）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hc_agent import paths

#: 看门狗"请求重启"退出码（与 hc_agent/errors.py 一致）
WATCHDOG_EXIT_CODE = 70

#: 重启风暴熔断
RESTART_WINDOW_S = 60.0
RESTART_MAX = 4
RESTART_DELAY_S = 1.2

_PROJECT_ROOT = paths.bundle_root()


class Backend:
    """受管后端子进程。

    状态机：stopped → starting → ready → (crashed → restarting)* → stopped
    """

    def __init__(self, *, on_event: Optional[Callable[[str, dict], None]] = None,
                 root: Optional[Path] = None) -> None:
        self.root = Path(root or _PROJECT_ROOT)
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._restarts: List[float] = []
        self._stopping = False
        self._on_event = on_event or (lambda ev, p: None)
        # 完成后待回调的请求（只由主线程 drain 消费）
        self._done: "queue.Queue[dict]" = queue.Queue()
        # tag → 该请求专属的结果队列。
        # **不能**让所有调用者共享一个队列再各自挑走自己的应答：那样必须在
        # 不匹配时把消息放回去，而主线程的 drain() 会把放回去的消息当成
        # 无主消息丢掉 —— 调用方于是永远等不到应答，最后超时（实测踩过：
        # 三个并发请求里只有最先发的那个成功）。
        self._waiters: Dict[str, "queue.Queue[dict]"] = {}
        self._waiters_lock = threading.Lock()
        self._unsolicited: "queue.Queue[dict]" = queue.Queue()
        self._pending = 0
        self.ready = False
        self.last_error = ""
        self.channel = ""
        self.diagnostics: Dict[str, Any] = {}

    # ---------------------------------------------------------- 诊断
    @staticmethod
    def _trace(msg: str) -> None:
        """把启动过程写到文件。

        windowed（无控制台）打包后 stderr 可能完全不可见，启动失败表现为
        "界面一直显示未获取"而无任何线索。这条文件日志是唯一可靠的自证手段。
        """
        try:
            from hc_agent import paths
            paths.ensure_writable(paths.logs_root())
            p = paths.logs_root() / "backend-trace.log"
            with open(p, "a", encoding="utf-8") as fh:
                import time as _t
                fh.write(f"{_t.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    # ---------------------------------------------------------- 进程管理
    @staticmethod
    def _child_argv() -> List[str]:
        """构造启动 engine_host 的命令行。

        **打包后不能再用 `-m`**：此时 `sys.executable` 是 GUI 的 exe，
        `exe -m hc_agent.engine_host` 会**再启动一个界面**而不是引擎宿主
        （而且它会继承 GUI 的 main 入口，直接进入 tkinter 主循环）。
        所以打包后改用 `--engine-host` 开关，由 exe 的入口分派到引擎宿主。
        """
        if paths.is_frozen():
            return [sys.executable, "--engine-host"]
        return [sys.executable, "-u", "-m", "hc_agent.engine_host"]

    def start(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            self._stopping = False
            if os.environ.get("HC_TRACE"):
                self._trace("start() 进入")
            env = dict(os.environ)
            # 告诉看门狗"外层有重启者"，否则它按设计拒绝启用自愈退出
            env["HC_WATCHDOG_SUPERVISOR"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            # 桌面端不通过 HTTP，明确关掉 Web 服务
            env.pop("HC_WEB_HOST", None)
            env.pop("HC_WEB_PORT", None)
            argv = self._child_argv()
            early_root = str(self.root)
            if os.environ.get("HC_TRACE"):
                self._trace(f"Popen argv={argv} cwd={early_root}")
            try:
                self._proc = subprocess.Popen(
                    argv,
                    cwd=early_root, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True, bufsize=1, encoding="utf-8",
                    errors="replace",
                    # Windows 下不要弹出控制台窗口
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self._trace(f"Popen 失败 {self.last_error}")
                self._emit("error", {"message": self.last_error})
                return False
        self._trace("子进程已创建，开始读取输出")
        threading.Thread(target=self._pump_stdout, daemon=True,
                         name="hc-host-stdout").start()
        threading.Thread(target=self._pump_stderr, daemon=True,
                         name="hc-host-stderr").start()
        threading.Thread(target=self._watch_exit, daemon=True,
                         name="hc-host-watch").start()
        self._emit("starting", {})
        return True

    def _pump_stdout(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            self._trace("_pump_stdout: 无 stdout，直接返回")
            return
        self._trace("_pump_stdout: 开始读取")
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if os.environ.get("HC_TRACE"):
                self._trace(f"stdout 收到: {line[:120]}")
            try:
                msg = json.loads(line)
            except Exception:
                # 不是协议行（比如第三方库的 print）→ 当普通日志转发
                self._emit("log", {"text": line})
                continue
            kind = msg.get("kind", "result")
            if kind == "ready":
                self.ready = True
                self.channel = msg.get("channel", "")
                self.diagnostics = msg.get("diagnostics", {})
                self._emit("ready", msg)
            elif kind == "progress":
                self._emit("progress", msg)
            elif kind == "result":
                self._route_result(msg)
            else:
                self._emit(kind, msg)

    def _route_result(self, msg: dict) -> None:
        """把应答投递给**发起该请求的**等待者。"""
        tag = msg.get("tag")
        with self._waiters_lock:
            q = self._waiters.get(tag) if tag else None
        if q is not None:
            q.put(msg)
        else:
            # 没有等待者（比如 shutdown 的应答）→ 留给 drain 处理
            self._unsolicited.put(msg)

    def _pump_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                self._emit("log", {"text": line})

    def _watch_exit(self) -> None:
        proc = self._proc
        if not proc:
            return
        code = proc.wait()
        self.ready = False
        if self._stopping:
            self._emit("stopped", {"code": code})
            return
        by_watchdog = (code == WATCHDOG_EXIT_CODE)
        now = time.time()
        self._restarts = [t for t in self._restarts
                          if now - t < RESTART_WINDOW_S]
        if len(self._restarts) >= RESTART_MAX:
            self._emit("error", {
                "message": (f"后端反复退出（近 {RESTART_WINDOW_S:.0f} 秒 "
                            f"{len(self._restarts)} 次），已停止自动重启。\n"
                            f"请查看日志目录后重试。"),
                "fatal": True})
            return
        self._restarts.append(now)
        self._emit("restarting", {
            "code": code, "by_watchdog": by_watchdog,
            "message": ("检测到计算引擎异常，正在自动恢复…" if by_watchdog
                        else f"后端进程意外退出（退出码 {code}），正在重启…")})
        time.sleep(RESTART_DELAY_S)      # 等旧进程释放 OpenDSS 句柄
        if not self._stopping:
            self.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                self.call_sync("shutdown", timeout=3)
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None
        self.ready = False

    # ---------------------------------------------------------- 调用
    def _send(self, payload: dict) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None or not proc.stdin:
            raise RuntimeError("后端未运行")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def call_async(self, cmd: str, args: Optional[dict] = None,
                   on_done: Optional[Callable[[dict], None]] = None,
                   timeout: float = 120.0) -> None:
        """在后台线程发起调用，完成后回调（回调在主线程轮询里执行）。"""
        self._pending += 1

        def _work():
            try:
                res = self._call_blocking(cmd, args or {}, timeout)
            except Exception as exc:
                res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            res["_callback"] = on_done
            self._done.put(res)

        threading.Thread(target=_work, daemon=True,
                         name=f"hc-call-{cmd}").start()

    def _call_blocking(self, cmd: str, args: dict, timeout: float) -> dict:
        tag = f"{cmd}-{time.time_ns()}"
        q: "queue.Queue[dict]" = queue.Queue()
        with self._waiters_lock:
            self._waiters[tag] = q
        try:
            self._send({"cmd": cmd, "args": args, "tag": tag})
            try:
                return q.get(timeout=max(0.1, timeout))
            except queue.Empty:
                return {"ok": False, "error": f"{cmd} 超时（{timeout:g}s）",
                        "tag": tag}
        finally:
            with self._waiters_lock:
                self._waiters.pop(tag, None)

    def call_sync(self, cmd: str, args: Optional[dict] = None,
                  timeout: float = 30.0) -> dict:
        return self._call_blocking(cmd, args or {}, timeout)

    def drain(self) -> None:
        """由 GUI 主线程定期调用：执行已完成请求的回调。"""
        for src in (self._done, self._unsolicited):
            while True:
                try:
                    msg = src.get_nowait()
                except queue.Empty:
                    break
                if src is self._done:
                    self._pending = max(0, self._pending - 1)
                cb = msg.pop("_callback", None)
                if cb:
                    try:
                        cb(msg)
                    except Exception as exc:
                        self._emit("error", {"message":
                                             f"回调异常: {type(exc).__name__}: {exc}"})

    def _emit(self, event: str, payload: dict) -> None:
        try:
            self._on_event(event, payload)
        except Exception:
            pass
