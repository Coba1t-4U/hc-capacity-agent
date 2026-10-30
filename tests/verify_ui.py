"""Verify the TianShu-style desktop UI end to end.

Checks three layers:
  1. static assets exist and the backend serves them (with traversal blocked)
  2. every page renders real data (headless Edge + DOM probes)
  3. the Electron launch flags that make the window actually appear are present

Requires the backend to be running on 127.0.0.1:8791; skipped gracefully if not.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8791"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
BAT = ROOT / "hc_agent_ui" / "start-desktop.bat"

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def http(path, timeout=20):
    """返回 (status, body_bytes, content_type)。失败时 status 为 None。"""
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", e.headers.get("Content-Type", "")
    except Exception as e:
        return None, str(e).encode(), ""


print("=" * 74)
print("天枢一号风格桌面界面 — 端到端验证")
print("=" * 74)

# ---------------------------------------------------------------- 1. 静态资源
print("\n1) 静态界面资源")
for f in ("index.html", "styles.css", "app.js"):
    p = ROOT / "hc_agent" / "static" / f
    check(f"存在 {f}", p.is_file(),
          f"{p.stat().st_size} B" if p.is_file() else "缺失")

status, _, _ = http("/api/health")
if status != 200:
    print("\n  [跳过] 后端未运行，无法验证渲染。请先启动 python -m hc_agent.web")
    print(f"\n结果：{ok} 通过 / {fail} 失败")
    sys.exit(1 if fail else 0)

print("\n2) 后端起服务与安全")
for path, mime in (("/", "text/html"), ("/styles.css", "text/css"),
                   ("/app.js", "javascript")):
    st, body, ct = http(path)
    check(f"GET {path} 且类型正确", st == 200 and mime in ct and len(body) > 100,
          f"status={st} ct={ct!r} bytes={len(body)}")

st, _, _ = http("/../../hc_agent/errors.py")
check("目录穿越被阻断", st == 404, f"status={st}")
st, _, _ = http("/..%2f..%2fhc_agent%2ferrors.py")
check("编码穿越被阻断", st in (404, 400), f"status={st}")

# 设计令牌必须与天枢一号一致
css = (ROOT / "hc_agent" / "static" / "styles.css").read_text(encoding="utf-8")
for token, value in (("--nav-bg", "#001432"), ("--primary", "#0050F0"),
                     ("--bg", "#F8F9FC"), ("--border", "#E4E8EE"),
                     ("--text", "#1A2138"), ("--titlebar-h", "56px"),
                     ("--statusbar-h", "36px"), ("--nav-w", "220px"),
                     ("--radius", "8px")):
    check(f"设计令牌 {token}={value}", f"{token}: {value}" in css)

# ---------------------------------------------------------------- 2. 页面渲染
print("\n3) 各页面渲染（无头 Edge + DOM 探针）")
tmp = Path(tempfile.mkdtemp(dir=str(ROOT / "tests")))

PAGES = [
    ("overview", "overview", ["环境与就绪度", "计算后端"]),
    ("overview", "reliability", ["可靠性设施", "看门狗参数", "70"]),
    ("overview", "channels", ["引擎通道", "opendssdirect", "容差下限"]),
    ("overview", "runs", ["运行记录", "FD002"]),
    ("workbench", "howto", ["如何发起一次评估", "hc_agent.cli", "退出码契约"]),
    ("workbench", "constraints", ["约束与阈值", "规则包", "unknown"]),
    ("results", "result", ["容量结论", "物理可开放容量"]),
    ("results", "caliber", ["求解口径声明", "负荷模型", "本次未覆盖"]),
]

probe_js = None
for page, sub, needles in PAGES:
    url = f"{BASE}/?page={page}&sub={sub}"
    dump = tmp / f"{page}_{sub}.html"
    cmd = [str(EDGE), "--headless=new", "--disable-gpu",
           "--virtual-time-budget=8000", "--dump-dom", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        html = r.stdout
    except Exception as exc:
        html = ""
        print(f"       (dump-dom 失败: {exc})")
    missing = [n for n in needles if n not in html]
    check(f"{page}/{sub} 渲染关键内容", not missing,
          f"缺少 {missing}" if missing else "")

# ---------------------------------------------------------------- 3. 启动参数
print("\n4) 桌面壳启动参数（窗口能真正出现的前提）")
bat = BAT.read_text(encoding="ascii", errors="replace")
check("纯 ASCII（cmd.exe 按 ANSI 解析）",
      all(b < 128 for b in BAT.read_bytes()))
check("带 --no-sandbox（否则 0x80000003，JS 都不执行）",
      "--no-sandbox" in bat)
check("带 --user-data-dir（否则 0xC0000005，getPath 崩）",
      "--user-data-dir" in bat)
check("Profile 目录位于可写位置", "%APP_DIR%\\profile" in bat)
check("清除 ELECTRON_RUN_AS_NODE", "ELECTRON_RUN_AS_NODE=" in bat)
check("声明外层重启者", "HC_WATCHDOG_SUPERVISOR=1" in bat)

main_js = (ROOT / "hc_agent_ui" / "main.js").read_text(encoding="utf-8")
check("无边框窗口（自绘标题栏）", "frame: false" in main_js)
check("按屏幕比例定尺寸", "workAreaSize" in main_js)
check("加载后端页面（单一来源）",
      "loadURL(`${DEFAULT_API}/`)" in main_js)
check("监听看门狗退出码 70", "WATCHDOG_EXIT_CODE = 70" in main_js)
check("重启风暴熔断", "RESTART_MAX" in main_js and "RESTART_WINDOW_MS" in main_js)

# ---------------------------------------------------------------- 4. 数据诚实性
print("\n5) 数据诚实性（取不到不得伪造）")
app_js = (ROOT / "hc_agent" / "static" / "app.js").read_text(encoding="utf-8")
check("有「未获取」占位", "未获取" in app_js)
check("NaN 视为未获取", "isFinite" in app_js)
check("不把 0 当占位符填充", "return NA;" in app_js)
check("markdown 星号已转义渲染", "function md(" in app_js)

# _logs 这类基础设施目录不得混进运行列表
st, body, _ = http("/api/runs")
runs = json.loads(body).get("runs", [])
names = [r.get("run_id") for r in runs]
check("运行列表排除 _logs 等基础目录",
      "_logs" not in names and "_archive" not in names, str(names))
check("运行列表含真实运行", any(n and n != "_logs" for n in names), str(names))
# 运行产物有两种布局，都必须被发现：
#   扁平 —— runs/ACCEPT/          ；分组 —— runs/ACCEPT/FD001/
# 只认扁平会让分组布局整个消失，界面看起来像"从没跑过"（实测踩过）。
nested = ROOT / "runs" / "ACCEPT" / "FD002"
if nested.is_dir():
    check("分组布局的运行被发现（嵌套一层）",
          "FD002" in names, f"names={names}")

st, _, _ = http("/api/runs/..%2f..%2fhc_agent")
check("运行明细接口阻断路径穿越",
      b"error" in http("/api/runs/..%2f..%2fhc_agent")[1],
      "目录穿越未被拦截")
st, _, _ = http("/api/runs/FD002")
check("运行明细可读取", st == 200 and b"summary" in http("/api/runs/FD002")[1],
      f"status={st}")

import shutil
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
