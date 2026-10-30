"""Verify the desktop launcher end to end, WITHOUT opening a console pipe.

`start ""` gives the GUI its own process but the batch file itself may hold the
inherited stdout handle, so capturing the .bat's output hangs. Instead: launch
it detached (as Explorer does), then poll for the window by title.
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAT = ROOT / "hc_agent_ui" / "start-native.bat"
TITLE = "配网承载力智能体Agent"

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def windows():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if not user32.IsWindowVisible(h):
            return True
        b = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, b, 512)
        if b.value.strip():
            out.append(b.value.strip())
        return True

    user32.EnumWindows(cb, 0)
    return out


def count(name):
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                       capture_output=True, text=True)
    return r.stdout.lower().count(name.lower())


print("=" * 70)
print("桌面启动器端到端验证")
print("=" * 70)

print(f"\n启动前: pythonw={count('pythonw.exe')}")
# 以 Explorer 的方式启动（完全脱离当前 shell 的管道）
subprocess.Popen(["explorer.exe", str(BAT)], close_fds=True)
print(f"已通过 {BAT.name} 启动（模拟双击）")

found = None
for i in range(40):
    time.sleep(1.5)
    ws = windows()
    if TITLE in ws:
        found = i * 1.5
        break

pw = count("pythonw.exe")
print(f"\n等待后: pythonw={pw}  耗时={found}")
print(f"窗口标题命中: {'是' if found is not None else '否'}")

ok = True
if found is None:
    print("  FAIL 未在 60 秒内检测到窗口")
    print("  当前可见窗口:")
    for w in windows()[:15]:
        print("    -", w[:70])
    ok = False
else:
    print(f"  PASS 桌面快捷方式能拉起原生窗口（{found:.1f}s）")

# GUI 进程 + 引擎子进程 = 2 个 pythonw
if pw >= 2:
    print(f"  PASS 存在 GUI 与引擎两个 pythonw 进程（{pw}）")
else:
    print(f"  FAIL 期望 ≥2 个 pythonw 进程，实际 {pw}")
    ok = False

sys.exit(0 if ok else 1)
