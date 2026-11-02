# -*- coding: utf-8 -*-
"""用本机应用自己截取各功能页，供 PPT 的「产品界面」页使用。

为什么要自动化点击：应用支持 ``HC_DESKTOP_PAGE`` 指定起始页，但实测
配置页加载后右侧内容区是空白的（页面未渲染），只能先启动到问答页，
再按坐标点击左侧导航切换，逐页截图。

坐标用**窗口相对比例**，不写死像素：不同分辨率下导航项位置会变。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

PY = (r"C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime"
      r"\dependencies\python\python.exe")
PROJ = Path.home() / "Desktop" / "配网承载力" / "配网承载力智能体Agent"
SHOTS = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace"
             r"\hc_agent_project\tests\_desktop_shots")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetProcessDPIAware()

# 左侧导航项的相对位置（占窗口高度的比例），取自 1456x939 的界面截图
NAV = {
    "chat": 0.159, "overview": 0.210, "workbench": 0.261,
    "process": 0.312, "dss": 0.363, "results": 0.414,
    "kb": 0.900, "config": 0.948,
}
NAV_X = 0.062          # 导航条中心 x 比例


def find_window(title_part: str = "承载力") -> int:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_part in buf.value:
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else 0


def rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def click(hwnd, fx: float, fy: float) -> None:
    left, top, w, h = rect(hwnd)
    x, y = left + int(w * fx), top + int(h * fy)
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)      # 左键按下
    user32.mouse_event(0x0004, 0, 0, 0, 0)      # 左键抬起


def grab(hwnd, out: Path) -> None:
    """用 GDI 抓窗口客户区，避免多显示器/遮挡导致黑图。"""
    from PIL import ImageGrab

    left, top, w, h = rect(hwnd)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    img = ImageGrab.grab(bbox=(left, top, left + w, top + h))
    img.save(out)


def main() -> int:
    if not (PROJ / "hc_agent").is_dir():
        print(f"工程不存在：{PROJ}")
        return 2
    SHOTS.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen([PY, "-m", "hc_agent.desktop.app"], cwd=str(PROJ))
    hwnd = 0
    for _ in range(40):
        time.sleep(0.5)
        hwnd = find_window()
        if hwnd:
            break
    if not hwnd:
        print("没找到应用窗口")
        proc.terminate()
        return 3
    print(f"窗口 hwnd={hwnd} 尺寸={rect(hwnd)}")
    time.sleep(3)

    targets = [("config", "60_config"), ("kb", "61_kb"),
               ("results", "62_results"), ("process", "63_process"),
               ("overview", "64_overview")]
    for key, name in targets:
        click(hwnd, NAV_X, NAV[key])
        time.sleep(1.6)
        out = SHOTS / f"{name}.png"
        grab(hwnd, out)
        size = out.stat().st_size if out.is_file() else 0
        print(f"  {key:<10} -> {out.name}  {size // 1024} KB")

    proc.terminate()
    try:
        proc.wait(timeout=8)
    except Exception:
        proc.kill()
    print("已关闭应用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
