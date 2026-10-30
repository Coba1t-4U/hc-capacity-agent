"""__main__.py — PyInstaller 入口（同时承担 GUI 与引擎宿主两种角色）。

**为什么要复用同一个 exe**：打包后 `sys.executable` 就是本程序，无法再用
`python -m hc_agent.engine_host` 拉起子进程 —— 那样只会再开一个界面。
因此入口按参数分派：

    HC-Agent.exe                → 桌面 GUI（默认）
    HC-Agent.exe --engine-host  → 引擎宿主（子进程，stdio JSON 协议）

用开关而不是两个 exe，是为了让子进程复用同一份已解压的依赖，也避免
"两个 exe 版本不一致"这种难以排查的状态。
"""

from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--engine-host" in argv:
        from hc_agent.engine_host import main as host_main
        return host_main()

    if "--version" in argv:
        from hc_agent import __version__
        print(f"HC-Agent {__version__}")
        return 0

    if "--paths" in argv:
        # 诊断：打印实际解析到的各条路径（打包后排查资源缺失的第一手信息）
        import json
        from hc_agent import paths
        print(json.dumps(paths.describe(), ensure_ascii=False, indent=2))
        return 0

    if "--llm-test" in argv:
        # 自检：确认 DeepSeek Key 是否被读到、能否真正调通。
        # 专门给打包版用 —— 打包后数据目录可能降级，只看源码版会误判"已配置"。
        import json
        try:
            from hc_agent import llm, paths
            cfg = llm.LLMConfig.from_env()
            report = {
                "data_root": str(paths.data_root()),
                "data_root_safe": str(paths.data_root_safe()),
                "candidates": [
                    {"path": str(c), "exists": c.is_file()}
                    for c in llm.config_candidates()],
                "enabled": cfg.enabled,
                "key": cfg.masked(),
                "model": cfg.model,
                "base_url": cfg.base_url,
            }
            if cfg.enabled:
                st = llm.self_test(cfg)
                report["reachable"] = bool(st.get("ok"))
                report["detail"] = st.get("reason") or "ok"
            else:
                report["reachable"] = False
                report["detail"] = "未配置 Key，将使用本地规则引擎"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("reachable") else 1
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False, indent=2))
            return 1

    if "--self-test" in argv:
        # 打包后自检：可写目录 + 引擎导入 + 一次真实求解。
        # 这是排查"能启动但算不了"的最快手段，不依赖 GUI。
        import json
        import traceback
        from pathlib import Path
        report = {"checks": {}}
        try:
            from hc_agent import paths
            report["paths"] = paths.describe()
            d = Path(paths.data_root())
            # 分层定位：os.makedirs 与 pathlib 都试，区分是"系统拒绝"还是"API 差异"
            try:
                d.mkdir(parents=True, exist_ok=True)
                report["checks"]["data_dir_created"] = d.is_dir()
            except Exception as exc:
                report["checks"]["data_dir_created"] = (
                    f"FAILED {type(exc).__name__}: {exc}")
            import os as _os
            try:
                _os.makedirs(str(d), exist_ok=True)
                report["checks"]["os_makedirs"] = "OK"
            except Exception as exc:
                report["checks"]["os_makedirs"] = (
                    f"FAILED {type(exc).__name__}: {exc}")
            report["checks"]["appdata_writable"] = _os.access(
                str(d.parent), _os.W_OK)
            w = paths.writable_runs_root()
            report["checks"]["writable_runs_root"] = str(w)
        except Exception:
            report["paths_error"] = traceback.format_exc()[-800:]
        try:
            from hc_agent import runtime_env
            lib = runtime_env.ensure_engine_on_path()
            report["checks"]["engine_on_path"] = str(lib)
            import opendssdirect as _dss
            report["checks"]["engine_version"] = str(_dss.Basic.Version())[:80]
        except Exception:
            report["checks"]["engine_error"] = traceback.format_exc()[-800:]
        try:
            import numpy as _np
            report["checks"]["numpy"] = _np.__version__
        except Exception:
            report["checks"]["numpy_error"] = traceback.format_exc()[-500:]
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    if "--spawn-test" in argv:
        # 在**冻结的 windowed 进程内部**复刻 backend.py 的 spawn 方式，
        # 验证子进程能否正常应答（windowed 模式下标准流可能为 None）。
        import json
        import subprocess
        import sys as _sys
        from hc_agent.desktop.backend import Backend
        out = {"frozen": True, "argv": Backend._child_argv()}
        # 关键：windowed 模式下这些可能是 None
        out["stdin_is_none"] = _sys.stdin is None
        out["stdout_is_none"] = _sys.stdout is None
        try:
            p = subprocess.Popen(
                Backend._child_argv(), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = p.stdout.readline()
            out["child_ready"] = line[:120] if line else "(EMPTY)"
            p.kill()
            out["child_stderr"] = p.stderr.read()[-400:]
        except Exception as exc:
            out["spawn_error"] = f"{type(exc).__name__}: {exc}"
        with open("spawn_test.json", "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        return 0

    from hc_agent.desktop.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
