"""web.py — 桌面端后端（供 Electron 壳调用）。

设计要点：
  * **只读**：本服务不提供任何写现场设备的能力，只暴露运行状态与产物读取。
  * **never-fail 诊断**：`/api/diagnostics` 的每一段独立 try —— 诊断接口自身
    报错会掩盖被诊断的问题，因此取不到就如实返回错误字段，而不是整体 500。
  * 无第三方 Web 框架依赖时退化为标准库 http.server，保证"能起来"。

启动：
  python -m hc_agent.web            # 默认 127.0.0.1:8791
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from hc_agent import __version__
from hc_agent import paths
from hc_agent.errors import WATCHDOG_EXIT_CODE

HOST = os.environ.get("HC_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("HC_WEB_PORT", "8791"))

_PROJECT_ROOT = paths.bundle_root()


def _runs_root() -> Path:
    return paths.runs_root()


# 非运行目录：这些是基础设施目录，出现在运行列表里会误导用户
# （实测踩过：`_logs` 被当成一次运行，于是"最近一次运行"取到它、什么也读不到）
_NON_RUN_DIRS = {"_logs", "_archive", "__pycache__"}


def _is_run_dir(p: Path) -> bool:
    """判断一个目录是不是"一次运行"。

    判据：**必须至少有一个运行产物**（run_outcome.json / state.json /
    results/run_summary.json）。空目录或只有日志的目录不算运行。
    """
    if not p.is_dir() or p.name in _NON_RUN_DIRS or p.name.startswith("."):
        return False
    return any((p / f).exists() for f in
               ("run_outcome.json", "state.json",
                "results/run_summary.json", "checkpoints.json"))


def _list_run_dirs() -> List[Path]:
    """列出所有运行目录，含一层嵌套。

    运行产物有两种布局，都要支持：
      * 扁平   —— runs/ACCEPT/            （直接就是一次运行）
      * 分组   —— runs/ACCEPT/FD001/      （一次验收跑多个算例，分目录存放）

    ⚠️ 只认扁平布局会让分组布局整个消失（实测踩过：重建 ACCEPT 为分组布局后
    /api/runs 返回空列表，界面看起来像"从没跑过"）。
    """
    root = _runs_root()
    if not root.exists():
        return []
    found: List[Path] = []
    for d in root.glob("*"):
        if d.name in _NON_RUN_DIRS or d.name.startswith("."):
            continue
        if _is_run_dir(d):
            found.append(d)
            continue
        if d.is_dir():
            # 分组目录：向下找一层（更深层不再递归，避免误收产物子目录）
            for sub in d.glob("*"):
                if _is_run_dir(sub):
                    found.append(sub)
    return sorted(found, key=lambda d: d.stat().st_mtime, reverse=True)


# ------------------------------------------------------------------ 各接口
def api_health() -> Dict[str, Any]:
    return {"status": "ok", "service": "hc-agent",
            "agent_version": __version__,
            "supervisor": bool(os.environ.get("HC_WATCHDOG_SUPERVISOR"))}


def api_diagnostics() -> Dict[str, Any]:
    """汇总诊断。**每段独立 try**，取不到就返回 error 字段而非整体失败。"""
    out: Dict[str, Any] = {"service": "hc-agent", "agent_version": __version__}
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
        out["supervisor"] = {
            "present": bool(os.environ.get("HC_WATCHDOG_SUPERVISOR")),
            "watchdog_exit_code": WATCHDOG_EXIT_CODE,
            "note": "退出码 70 由桌面壳监听并重启后端",
        }
    except Exception as exc:
        out["supervisor"] = {"error": str(exc)}
    try:
        runs = _list_run_dirs()
        # 「最近一次运行」要挑**有结论**的那一次：只取目录最新会让没有
        # run_outcome.json 的运行（如研究型 RM001）占位，界面于是显示
        # "还没有跑过任务"，而运行计数明明不为零 —— 自相矛盾（实测踩过）。
        chosen = next((d for d in runs
                       if (d / "run_outcome.json").exists()), None)
        chosen = chosen or (runs[0] if runs else None)
        if chosen is not None:
            latest = chosen
            st = latest / "state.json"
            if st.exists():
                try:
                    out["state"] = json.loads(st.read_text(encoding="utf-8"))
                except Exception as exc:
                    out["state"] = {"error": f"state.json 解析失败: {exc}"}
            oc = latest / "run_outcome.json"
            if oc.exists():
                try:
                    out["run_outcome.json"] = json.loads(
                        oc.read_text(encoding="utf-8"))
                except Exception as exc:
                    out["run_outcome.json"] = {"error": str(exc)}
            out["latest_run"] = latest.name
            # 前端要的是"最后推进到哪一步"。此前只给了 state，没给 last_stage，
            # 于是界面一直显示"(无痕迹)"——明明有遗言却看起来没有（实测踩过）。
            if isinstance(out.get("state"), dict):
                out["last_stage"] = (out["state"].get("_persisted_stage")
                                     or "(无痕迹)")
    except Exception as exc:
        out["runs_error"] = f"{type(exc).__name__}: {exc}"
    try:
        out["process"] = {"pid": os.getpid(), "python": sys.version.split()[0]}
    except Exception as exc:
        out["process"] = {"error": str(exc)}
    return out


def api_channels() -> Dict[str, Any]:
    try:
        from hc_agent.engines import engine_channels
        health = engine_channels.probe_all()
        ch, info = engine_channels.select_channel(fallback=True)
        return {"channels": {k: v.to_dict() for k, v in health.items()},
                "selected": ch, "selection": info}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def api_runs() -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    for d in _list_run_dirs()[:40]:
        item: Dict[str, Any] = {"run_id": d.name}
        oc = d / "run_outcome.json"
        if oc.exists():
            try:
                o = json.loads(oc.read_text(encoding="utf-8"))
                item.update({
                    "exit_code": o.get("exit_code"),
                    "finished_at": o.get("finished_at"),
                    "capacity_physical_kw": o.get("capacity_physical_kw"),
                    "capacity_managed_kw": o.get("capacity_managed_kw"),
                    "level": o.get("level"),
                })
            except Exception:
                pass
        sm = d / "results" / "run_summary.json"
        if sm.exists():
            try:
                s = json.loads(sm.read_text(encoding="utf-8"))
                item.setdefault("task_id", s.get("task_id"))
                for k in ("capacity_physical_kw", "capacity_managed_kw",
                          "level", "binding_constraint"):
                    if item.get(k) is None and s.get(k) is not None:
                        item[k] = s.get(k)
            except Exception:
                pass
        st = d / "state.json"
        if st.exists():
            try:
                item["last_stage"] = json.loads(
                    st.read_text(encoding="utf-8")).get("_persisted_stage")
            except Exception:
                pass
        runs.append(item)
    return {"runs": runs}


def _resolve_run_dir(run_id: str) -> Optional[Path]:
    """把运行名解析成目录，支持两种布局且**限定在 runs/ 之内**。

    列表接口给出的是裸名（如 ``FD002``），它可能位于 ``runs/FD002`` 或
    ``runs/ACCEPT/FD002``。因此明细接口不能只在 runs/ 根下找，否则分组布局
    的运行点进去就是"不存在"（实测踩过）。
    """
    root = _runs_root().resolve()
    # 先按相对路径直接解析（同时覆盖 FD002 与 ACCEPT/FD002 两种写法）
    candidates: List[Path] = []
    try:
        direct = (root / run_id).resolve()
        direct.relative_to(root)          # 越界则抛 ValueError
        candidates.append(direct)
    except (ValueError, OSError):
        return None
    # 再按"裸名"在根下与一层分组里查找
    candidates.extend(d for d in _list_run_dirs() if d.name == run_id)
    for d in candidates:
        if d.is_dir() and _is_run_dir(d):
            return d
    return None


def api_run_detail(run_id: str) -> Dict[str, Any]:
    """单个运行的摘要（含口径声明与门禁）。"""
    d = _resolve_run_dir(run_id)
    if d is None:
        return {"error": f"运行 {run_id} 不存在或不是一次运行"}
    out: Dict[str, Any] = {"run_id": run_id}
    for name, rel in (("summary", "results/run_summary.json"),
                      ("outcome", "run_outcome.json"),
                      ("gate", "review/release_gate.json"),
                      ("publication", "review/publication.json"),
                      ("state", "state.json"),
                      ("conversion", "results/conversion.json")):
        p = d / rel
        if p.exists():
            try:
                out[name] = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                out[name] = {"error": f"{rel} 解析失败: {exc}"}
    return out


ROUTES: Dict[str, Callable[..., Dict[str, Any]]] = {
    "/api/health": api_health,
    "/api/diagnostics": api_diagnostics,
    "/api/channels": api_channels,
    "/api/runs": api_runs,
}

# 界面静态文件目录（自带设计系统，无前端构建步骤）
_STATIC = paths.resource("static")
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "hc-agent/" + __version__

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    def _send(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel: str) -> bool:
        """把 hc_agent/static/ 下的界面文件发出去。返回是否已处理。

        **路径必须做目录穿越防护**：否则 `../../` 能读到工程内任意文件。
        """
        if not _STATIC.is_dir():
            return False
        base = _STATIC.resolve()
        target = (base / rel.lstrip("/")).resolve() if rel else base / "index.html"
        try:
            target.relative_to(base)
        except ValueError:
            return False                    # 越界 → 交给上层 404
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return False
        body = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            _MIME.get(target.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            fn = ROUTES.get(path.rstrip("/") or "/")
            if fn is not None:
                return self._send(200, fn())
            if path.startswith("/api/runs/"):
                rid = path[len("/api/runs/"):]
                return self._send(200, api_run_detail(rid))
            if path.startswith("/api/"):
                return self._send(404, {"error": f"未知接口 {path}"})
            # 非 /api/ 一律当静态界面；能发出去就发，否则 404
            if self._serve_static(path if path != "/" else ""):
                return None
            return self._send(404, {"error": f"未找到 {path}"})
        except Exception as exc:
            # never-fail：路由处理出错也要返回 JSON，而不是断连
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})


def create_server(host: str = HOST, port: int = PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    # 崩溃可观测必须在任何可能崩溃的初始化**之前**安装
    from hc_agent.runtime import crashlog, watchdog
    crashlog.install()
    supervisor = bool(os.environ.get("HC_WATCHDOG_SUPERVISOR"))
    try:
        watchdog.install(require_supervisor=True,
                         supervisor_present=supervisor)
    except Exception as exc:
        crashlog.record_text(f"看门狗安装失败: {exc}", kind="manual")

    srv = create_server()
    print(f"hc-agent backend on http://{HOST}:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watchdog.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
