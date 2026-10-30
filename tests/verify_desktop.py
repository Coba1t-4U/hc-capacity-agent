"""Verify the NATIVE desktop client.

The point of this suite is to prove the desktop app is genuinely native:
no HTML, no HTTP server, no port, no browser engine -- and that every page
actually renders widgets (a blank content area is the failure mode that a
screenshot of the nav bar alone would hide).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "hc_agent" / "desktop"
BAT = ROOT / "hc_agent_ui" / "start-native.bat"

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
print("原生桌面端验证（Tk，非网页）")
print("=" * 74)

# ---------------------------------------------------------------- 1. 原生性
print("\n1) 原生性：不得有 HTML / HTTP / 浏览器")
srcs = {p.name: p.read_text(encoding="utf-8")
        for p in DESKTOP.glob("*.py")}
host = (ROOT / "hc_agent" / "engine_host.py").read_text(encoding="utf-8")
allsrc = "\n".join(srcs.values()) + host

for needle, why in (("<!DOCTYPE", "HTML 文档"), ("<html", "HTML 标签"),
                    ("WebView", "内嵌浏览器控件"),
                    ("loadURL", "浏览器加载"),
                    ("electron", "Electron")):
    check(f"桌面端不含 {why}", needle not in allsrc)

# 只允许"为了对比/说明"提到 http，不允许真的起服务
imports_http = re.findall(r"^\s*(?:from|import)\s+(http\S*|urllib\S*|socket)\b",
                          allsrc, re.M)
check("桌面端不导入 http/urllib/socket", not imports_http,
      f"发现 {imports_http}")
check("桌面端不含 HTTP 服务端口常量",
      not re.search(r"\b(?:PORT|listen|serve_forever)\b", allsrc))

# 界面由 Tk 绘制
check("使用 tkinter 绘制界面", "import tkinter" in srcs.get("app.py", "")
      or "import tkinter" in srcs.get("widgets.py", ""))
check("窗口标题与主进程一致",
      "配网承载力智能体Agent" in srcs.get("app.py", ""))

# ---------------------------------------------------------------- 2. 设计令牌
print("\n2) 设计令牌与网页版一致（同一套视觉语言）")
css = (ROOT / "hc_agent" / "static" / "styles.css").read_text(encoding="utf-8")
theme = srcs.get("theme.py", "")
import ast
mod = ast.parse(theme)
tokens = {}
for node in mod.body:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        t = node.targets[0]
        if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant):
            tokens[t.id] = node.value.value
for name, css_var in (("NAV_BG", "--nav-bg"), ("PRIMARY", "--primary"),
                      ("BG", "--bg"), ("CARD", "--card"),
                      ("BORDER", "--border"), ("TEXT", "--text"),
                      ("MUTED", "--text-muted"), ("SUCCESS", "--success"),
                      ("DANGER", "--danger"), ("WARNING", "--warning")):
    val = tokens.get(name)
    check(f"{name} = {val} 与 CSS {css_var} 一致",
          val is not None and f"{css_var}: {val}" in css,
          f"theme={val}")

# ---------------------------------------------------------------- 3. 协议
print("\n3) 后端协议（管道 JSON，非 HTTP）")
hostr = subprocess.run(
    [sys.executable, "-c", "import hc_agent.engine_host as m; print('ok')"],
    cwd=str(ROOT), capture_output=True, text=True, timeout=120)
check("engine_host 可导入", "ok" in hostr.stdout,
      hostr.stderr[-200:] if hostr.returncode else "")


def protocol_session():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["HC_WATCHDOG_SUPERVISOR"] = "1"
    p = subprocess.Popen(
        [sys.executable, "-u", "-m", "hc_agent.engine_host"],
        cwd=str(ROOT), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        encoding="utf-8", errors="replace")
    return p


proc = protocol_session()
ready = json.loads(proc.stdout.readline())
check("子进程上报 ready", ready.get("kind") == "ready", str(ready)[:120])
check("ready 带引擎通道", "channel" in ready)


def rpc(cmd, **args):
    proc.stdin.write(json.dumps({"cmd": cmd, "args": args, "tag": cmd}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


r = rpc("ping")
check("ping 应答正确", r.get("ok") and r.get("pong") is True)
check("子进程 pid 与父进程不同", r.get("pid") not in (None, os.getpid()),
      f"child={r.get('pid')} parent={os.getpid()}")
r = rpc("runs")
check("runs 通过管道返回", r.get("ok") is True)
seen_runs = r.get("runs") or []
check("能读到运行目录", isinstance(seen_runs, list))
check("运行列表排除 _logs",
      all(x.get("run_id") != "_logs" for x in seen_runs),
      str([x.get("run_id") for x in seen_runs]))
r = rpc("diagnostics")
diag = r.get("diagnostics") or {}
check("diagnostics 可读", r.get("ok") is True)
check("选定的是有结论的运行",
      bool(diag.get("run_outcome")) or not seen_runs,
      f"latest={diag.get('latest_run')} outcome={bool(diag.get('run_outcome'))}")
r = rpc("shutdown")
proc.wait(timeout=15)
check("shutdown 干净退出", proc.returncode == 0, f"rc={proc.returncode}")

# ---------------------------------------------------------------- 4. 并发
print("\n4) 并发调用（曾因共享队列而静默超时）")
code = r"""
import sys, time, threading
sys.path.insert(0, r'%s')
from hc_agent.desktop.backend import Backend
b = Backend()
b.start()
for _ in range(80):
    if b.ready: break
    time.sleep(0.2)
got = {}
def drain():
    t0 = time.time()
    while len(got) < 3 and time.time() - t0 < 40:
        b.drain(); time.sleep(0.05)
threading.Thread(target=drain, daemon=True).start()
for n in ('diagnostics', 'channels', 'runs'):
    def cb(msg, k=n):
        got[k] = bool(msg.get('ok'))
    b.call_async(n, on_done=cb)
t0 = time.time()
while len(got) < 3 and time.time() - t0 < 40:
    time.sleep(0.2)
print('RESULT', sum(1 for v in got.values() if v), len(got))
b.stop()
""" % str(ROOT)
r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                   capture_output=True, text=True, timeout=200)
line = next((l for l in r.stdout.splitlines() if l.startswith("RESULT")), "")
check("三个并发请求全部成功", line.strip() == "RESULT 3 3", line or r.stderr[-200:])

# ---------------------------------------------------------------- 5. 渲染
print("\n5) 每个页面都能渲染出控件（空白内容区是真实故障）")
render_code = r"""
import sys, os
sys.path.insert(0, r'%s')
os.environ.setdefault('HC_WATCHDOG_SUPERVISOR', '1')
from hc_agent.desktop.app import App
app = App()
app.withdraw()
report = {}
for page, subs in (('overview', ['overview','reliability','channels','runs']),
                   ('workbench', ['howto','scenario','constraints']),
                   ('results', ['result','caliber','evidence','verify'])):
    for sub in subs:
        try:
            app.show(page, sub)
            app.update_idletasks()
            n = len(app.pagebody.winfo_children())
            deep = 0
            stack = list(app.pagebody.winfo_children())
            while stack:
                w = stack.pop(); deep += 1
                stack.extend(w.winfo_children())
            report[page + '/' + sub] = deep
        except Exception as exc:
            report[page + '/' + sub] = 'ERR:' + type(exc).__name__ + ':' + str(exc)[:60]
app.destroy()
import json
print('RENDER ' + json.dumps(report, ensure_ascii=False))
""" % str(ROOT)
r = subprocess.run([sys.executable, "-c", render_code], cwd=str(ROOT),
                   capture_output=True, text=True, timeout=300)
line = next((l for l in r.stdout.splitlines() if l.startswith("RENDER")), "")
if not line:
    check("页面渲染", False, (r.stderr or r.stdout)[-400:])
else:
    rep = json.loads(line[len("RENDER "):])
    for key, val in rep.items():
        check(f"{key} 渲染出控件", isinstance(val, int) and val > 3, str(val))

# ---------------------------------------------------------------- 6. 启动器
print("\n6) 桌面启动器")
raw = BAT.read_bytes()
check("启动器为纯 ASCII", all(b < 128 for b in raw),
      f"{len([b for b in raw if b > 127])} 个非 ASCII 字节")
bat = raw.decode("ascii")
check("使用 pythonw.exe（不弹控制台）", "pythonw.exe" in bat)
check("以 -m hc_agent.desktop 启动", "-m hc_agent.desktop" in bat)
check("声明外层重启者", "HC_WATCHDOG_SUPERVISOR=1" in bat)
check("清除 ELECTRON_RUN_AS_NODE", "ELECTRON_RUN_AS_NODE=" in bat)

main_py = srcs.get("app.py", "")
check("自绘标题栏（无系统边框）", "overrideredirect" in main_py
      or "_build_titlebar" in main_py)
check("启动时必定显示一页（防空白窗口）",
      # 只校验意图：没有页面时必定 show 一页。不绑定默认页名字面量 ——
      # 默认落地页会随产品调整（现在默认进问答），测试不该把它钉死。
      bool(re.search(r"if\s+self\.page\s+is\s+None\s*:\s*\n\s*self\.show\(",
                     main_py))
      and "self._deep_page" in main_py,
      "未找到 page is None 守卫")
check("主线程轮询后端结果（Tk 非线程安全）", "_drain_loop" in main_py)
check("支持 F5 刷新", '"<F5>"' in main_py)

# ---------------------------------------------------------------- 7. 数据诚实
print("\n7) 数据诚实性（取不到不得伪造）")
pages = srcs.get("pages.py", "")
check("有「未获取」占位", "NA = \"未获取\"" in pages)
check("浮点 NaN/Inf 视为未获取", "float(\"inf\")" in pages)
check("带单位时缺失不拼单位", "def fmt_unit" in pages)
check("markdown 星号不原样显示", '.replace("**", "")' in pages)

# ---------------------------------------------------------------- 有状态页面
print("\n9) 有状态页面不被整页重建（防聊天记录被清空）")
check("_maybe_rerender 尊重 STATEFUL", "STATEFUL" in main_py)
check("Page 基类有 STATEFUL 默认值", "STATEFUL = False" in srcs.get("pages.py", ""))
for cls in ("ChatPage", "DssCasePage", "KbPage"):
    src = srcs.get("pages.py", "")
    i = src.find(f"class {cls}(Page):")
    seg = src[i:src.find(chr(10) + "class ", i + 1)] if i >= 0 else ""
    check(f"{cls} 标记为有状态", "STATEFUL = True" in seg, cls)

probe2 = """
import sys, os, time
sys.path.insert(0, r'%s')
os.environ.setdefault('HC_WATCHDOG_SUPERVISOR', '1')
from hc_agent.desktop.app import App
app = App()
app.update(); time.sleep(0.8); app.update()
app.show('chat'); app.update(); time.sleep(0.3); app.update()
v = app.chat_view
v.add_system('测试消息一'); v.add_system('测试消息二')
app.update()
n0 = len(v._bubbles)
# 模拟后端数据到达（诊断/运行列表回调）
app.state['diagnostics'] = {'ok': True}
for _ in range(3):
    app._maybe_rerender()
app.update()
same = (app.chat_view is v)
n1 = len(v._bubbles)
print('STATEFUL_KEEP', same, n0, n1)
app.destroy()
""" % str(ROOT)
r2 = subprocess.run([sys.executable, "-c", probe2], cwd=str(ROOT),
                    capture_output=True, text=True, timeout=180)
line = next((l for l in r2.stdout.splitlines()
             if l.startswith('STATEFUL_KEEP')), '')
if not line:
    check("聊天视图不被重建", False, (r2.stderr or r2.stdout)[-300:])
else:
    parts = line.split()
    same, n0, n1 = parts[1], int(parts[2]), int(parts[3])
    check("后端回调后仍是同一个 chat_view", same == 'True', line)
    check("后端回调后消息数不变", n0 == n1, f"{n0} -> {n1}")

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
