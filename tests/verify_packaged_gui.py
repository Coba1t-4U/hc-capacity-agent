"""Verify the packaged GUI actually opens a window, without pipe deadlocks.

Launching a GUI child from a captured shell is awkward: the child inherits the
stdout pipe, so tools that wait on "output closed" hang even after the window is
up. This script uses DETACHED_PROCESS so the child shares no handles with us,
then polls for the window by title.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "HC-Agent" / "HC-Agent.exe"
TITLE = "配网承载力智能体Agent"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def visible_windows():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if not user32.IsWindowVisible(h):
            return True
        b = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, b, 512)
        if b.value.strip():
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            out.append((b.value.strip(), pid.value, h))
        return True

    user32.EnumWindows(cb, 0)
    return out


print("=" * 74)
print("打包 GUI 验证：窗口能否真正打开")
print("=" * 74)

check("exe 存在", EXE.is_file(), str(EXE))
if not EXE.is_file():
    sys.exit(1)

env = dict(os.environ)
env["HC_DATA_DIR"] = str((ROOT / "runs" / "_guidata").resolve())
# 打开后端追踪：既能让测试等到"真的收到应答"，也是排查打包问题的第一手材料
env["HC_TRACE"] = "1"
env.pop("ELECTRON_RUN_AS_NODE", None)
env.pop("NODE_OPTIONS", None)

print("\n启动（DETACHED_PROCESS，与当前 shell 无共享句柄）…")
proc = subprocess.Popen(
    [str(EXE)],
    cwd=str(EXE.parent),
    env=env,
    close_fds=True,
    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

found = None
deadline = time.time() + 90
while time.time() < deadline:
    time.sleep(1.5)
    for title, pid, hwnd in visible_windows():
        if title == TITLE:
            found = (title, pid, hwnd)
            break
    if found:
        break
    if proc.poll() is not None:
        print(f"  (进程已退出，退出码 {proc.returncode})")
        break

check("GUI 窗口出现（标题精确匹配）", found is not None,
      "90 秒内未出现" if found is None else "")

# 窗口出现 ≠ 数据到位。打包版冷启动要解压 _internal、加载 OpenDSS，
# 首个数据往返可能要 10~20 秒。必须**等到后端真的有应答**再断言，
# 否则截到的是"未获取"的中间态，会被误判成故障（实测踩过）。
if found:
    print(f"     标题: {found[0]}")
    print(f"     pid : {found[1]}     hwnd: {found[2]}")
    trace = Path(env["HC_DATA_DIR"]) / "logs" / "backend-trace.log"
    waited = 0.0
    while waited < 70:
        time.sleep(2)
        waited += 2
        try:
            txt = trace.read_text(encoding="utf-8") if trace.exists() else ""
        except Exception:
            txt = ""
        if '"kind": "ready"' in txt and '"runs"' in txt:
            print(f"     后端已应答（等待 {waited:.0f}s）")
            break
    time.sleep(3)          # 留一点时间让界面刷新

# GUI 会拉起引擎子进程，因此应有 ≥2 个 HC-Agent 进程
def count_procs(image="HC-Agent.exe"):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                       capture_output=True, text=True)
    return r.stdout.lower().count(image.lower())


n = count_procs()
print(f"\n  HC-Agent 进程数: {n}  (GUI + 引擎宿主)")
check("存在 GUI 与引擎两个进程", n >= 2, f"实际 {n}")

# 截一张图作为交付证据
try:
    from PIL import Image
    if found:
        hwnd = found[2]
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        hdc = user32.GetWindowDC(hwnd)
        mdc = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
        bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
        ctypes.windll.gdi32.SelectObject(mdc, bmp)
        user32.PrintWindow(hwnd, mdc, 2)

        class BIH(ctypes.Structure):
            _fields_ = [("s", wintypes.DWORD), ("w", wintypes.LONG),
                        ("h", wintypes.LONG), ("p", wintypes.WORD),
                        ("b", wintypes.WORD), ("c", wintypes.DWORD),
                        ("si", wintypes.DWORD), ("x", wintypes.LONG),
                        ("y", wintypes.LONG), ("cu", wintypes.DWORD),
                        ("ci", wintypes.DWORD)]

        bi = BIH()
        bi.s = ctypes.sizeof(BIH)
        bi.w, bi.h, bi.p, bi.b = w, -h, 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        ctypes.windll.gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        out = ROOT / "tests" / "_desktop_shots" / "9_packaged.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out)
        content = img.convert("L").getextrema()
        check("截图非空白（内容区已渲染）",
              content[1] - content[0] > 40, f"灰度范围 {content}")
        print(f"     截图: {out}")
        ctypes.windll.gdi32.DeleteObject(bmp)
        ctypes.windll.gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
except Exception as exc:
    print(f"  (截图跳过: {exc})")

# 收尾：按进程名结束，避免遗留
subprocess.run(["taskkill", "/F", "/IM", "HC-Agent.exe"],
               capture_output=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
