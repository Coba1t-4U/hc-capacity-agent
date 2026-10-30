"""Verify the full chain that the desktop shortcut triggers, minus the GUI window.

The GUI window itself cannot open in this sandbox (proven: a 3-line Electron app
dies with -2147483645 before any JS runs). But everything the shortcut is
responsible for CAN be verified:

  shortcut -> start-desktop.bat -> clears env vars, sets supervisor flag
           -> electron --disable-gpu <appdir>
           -> main.js spawns `python -m hc_agent.web` with the right env
           -> backend serves /api/health

This script exercises that chain by launching the backend exactly the way
main.js does, then checking every guarantee the shortcut is supposed to provide.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "hc_agent_ui"
BAT = UI / "start-desktop.bat"
MAIN_JS = UI / "main.js"

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 74)
print("桌面快捷方式链路验证（除 GUI 窗口外的全部环节）")
print("=" * 74)

# ---------------------------------------------------------------- 1. 快捷方式目标
print("\n1) 快捷方式与启动器")
check("启动器存在", BAT.exists(), str(BAT))
raw = BAT.read_bytes()
non_ascii = len([b for b in raw if b > 127])
check("启动器为纯 ASCII（cmd.exe 按 ANSI 解析，中文会被当命令执行）",
      non_ascii == 0, f"{non_ascii} 个非 ASCII 字节")
text = BAT.read_text(encoding="ascii", errors="replace")
check("清除 ELECTRON_RUN_AS_NODE", "ELECTRON_RUN_AS_NODE=" in text)
check("清除 NODE_OPTIONS", "NODE_OPTIONS=" in text)
check("声明有外层重启者", "HC_WATCHDOG_SUPERVISOR=1" in text)
check("把应用目录作为参数传给 Electron",
      '"%APP_DIR%"' in text and "%ELECTRON%" in text)
check("失败时有明确提示而非静默退出", "Exit code" in text and "pause" in text)

# ---------------------------------------------------------------- 2. main.js 约定
print("\n2) main.js 与后端的约定")
js = MAIN_JS.read_text(encoding="utf-8")
check("监听看门狗退出码 70", "WATCHDOG_EXIT_CODE = 70" in js)
check("以 `python -m hc_agent.web` 启动后端",
      "'-m', 'hc_agent.web'" in js or '"-m", "hc_agent.web"' in js)
check("注入 HC_WATCHDOG_SUPERVISOR", "HC_WATCHDOG_SUPERVISOR" in js)
check("重启前等端口释放", "RESTART_DELAY_MS" in js)
check("重启风暴熔断", "RESTART_MAX" in js and "RESTART_WINDOW_MS" in js)

# ---------------------------------------------------------------- 3. 后端可启动
print("\n3) 后端按 main.js 的方式启动")
env = dict(os.environ)
env.update({
    "HC_WATCHDOG_SUPERVISOR": "1",
    "HC_WEB_HOST": "127.0.0.1",
    "HC_WEB_PORT": "8792",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
})
py = None
for cand in (Path(os.environ.get("USERPROFILE", "")) / ".dsh" / "dsh-runtimes"
             / "dsh-primary-runtime" / "dependencies" / "python" / "python.exe",
             ROOT / ".venv" / "Scripts" / "python.exe"):
    if cand.exists():
        py = cand
        break
if py is None:
    import shutil
    py = shutil.which("python")
check("找到 Python", py is not None, str(py))

proc = None
if py:
    log = ROOT / "runs" / "_logs" / "shortcut_backend.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen([str(py), "-m", "hc_agent.web"], cwd=str(ROOT),
                            env=env, stdout=fh, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:8792"
    up = False
    for _ in range(40):
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:
            time.sleep(0.5)
    check("后端在快捷方式设定的端口上就绪", up)

    if up:
        def get(p):
            with urllib.request.urlopen(base + p, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))

        h = get("/api/health")
        check("health 报告 supervisor 已就位", h.get("supervisor") is True, str(h))
        d = get("/api/diagnostics")
        check("诊断接口可用", "error" not in d)
        wd = d.get("watchdog") or {}
        check("看门狗已启动（因有重启者）", wd.get("started") is True, str(wd))
        check("看门狗退出码为 70",
              (wd.get("params") or {}).get("exit_code") == 70)
        cl = d.get("crashlog") or {}
        check("崩溃可观测已安装", cl.get("installed") is True)
        check("崩溃日志声明了原生崩溃的局限",
              "段错误" in str(cl.get("limitation", "")))
        ch = get("/api/channels")
        check("引擎通道接口可用", "channels" in ch, str(ch)[:120])
        check("选出可用通道", ch.get("selected") in
              ("opendssdirect", "dss-python", "cli"), str(ch.get("selected")))
        runs = get("/api/runs")
        check("运行列表接口可用", "runs" in runs)

    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        fh.close()
        check("后端可被干净终止", proc.poll() is not None)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
