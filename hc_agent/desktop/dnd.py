"""dnd.py — Windows 原生拖放（WM_DROPFILES）。

## 为什么不用第三方库

`tkinterdnd2` 要额外装包并把 tkdnd 动态库打进 PyInstaller 包，
体积与打包复杂度都上升。这里直接用 Win32 的两件事：

  1. `DragAcceptFiles(hwnd, True)` —— 告诉系统"这个窗口收文件投放"
  2. `SetWindowLongPtrW(GWLP_WNDPROC, new_proc)` —— 接管窗口过程，
     在 `WM_DROPFILES` 里取出路径

## 安全性（这类代码最容易把进程搞崩）

**必须给每个 Win32 调用声明 argtypes/restype**。
第一版没声明就直接 `CallWindowProcW(old, ...)`，`old` 被当成 32 位 int 传入，
64 位下立刻访问违例、整个进程消失（实测踩过：界面挂上拖放后几秒内闪退）。
`SetWindowLongPtrW` 的返回值也要用 `c_void_p` 接住，不能当普通整数。

其余要点：

* 全程 try/except；**任何环节失败都只是"拖放不可用"**，界面照常可用，
  另有「选择文件…」按钮作为后备。
* 必须**长期持有**新窗口过程的引用（`self._proc`）。被 GC 掉会在下一次
  消息到达时执行野指针 → 崩溃。
* 除 `WM_DROPFILES` 外的消息一律 `CallWindowProcW(old, ...)` 转回去，
  不改变 Tk 原有行为。
* 路径通过队列交给主线程，不在窗口过程里做耗时工作。
"""

from __future__ import annotations

import queue
from typing import Callable, List, Optional

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


class DropTarget:
    """给一个 Tk 顶层窗口挂上"接收文件投放"的能力。"""

    def __init__(self, widget, on_paths: Callable[[List[str]], None]):
        self.widget = widget
        self._on_paths = on_paths
        self._paths: "queue.Queue[List[str]]" = queue.Queue()
        self.enabled = False
        self.error = ""
        self._proc = None          # 必须持有引用，否则 GC 后崩溃
        self._old = None           # c_void_p，原窗口过程
        self._hwnd: Optional[int] = None
        self._user32 = None
        self._shell32 = None

    # ------------------------------------------------------------ 挂载
    def attach(self) -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            self._user32 = ctypes.windll.user32
            self._shell32 = ctypes.windll.shell32
            top = self.widget.winfo_toplevel()
            self._hwnd = int(top.winfo_id())

            shell = self._shell32
            shell.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            shell.DragAcceptFiles.restype = None
            shell.DragQueryFileW.argtypes = [
                wintypes.HANDLE, wintypes.UINT,
                ctypes.c_wchar_p, wintypes.UINT]
            shell.DragQueryFileW.restype = wintypes.UINT
            shell.DragFinish.argtypes = [wintypes.HANDLE]
            shell.DragFinish.restype = None
            shell.DragAcceptFiles(wintypes.HWND(self._hwnd), True)

            user32 = self._user32
            # 关键：先声明类型，再调用。缺了这一步 64 位下必然崩溃。
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t]
            user32.CallWindowProcW.restype = ctypes.c_ssize_t

            proto = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                ctypes.c_size_t, ctypes.c_ssize_t)

            def _wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    try:
                        self._read_drop(wparam)
                    except Exception:
                        pass
                    return 0
                try:
                    return user32.CallWindowProcW(
                        self._old, hwnd, msg, wparam, lparam)
                except Exception:
                    return 0

            self._proc = proto(_wndproc)

            setter = getattr(user32, "SetWindowLongPtrW", None)
            if setter is None:                       # 32 位系统
                setter = getattr(user32, "SetWindowLongW")
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            setter.restype = ctypes.c_void_p
            old = setter(wintypes.HWND(self._hwnd), GWLP_WNDPROC,
                         ctypes.cast(self._proc, ctypes.c_void_p))
            if not old:
                raise OSError("SetWindowLongPtrW 返回空（无法接管窗口过程）")
            self._old = ctypes.c_void_p(old)
            self.enabled = True
            return True
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.enabled = False
            return False

    def _read_drop(self, hdrop) -> None:
        """从 HDROP 里取出所有路径（可能多文件）。"""
        import ctypes
        from ctypes import wintypes

        shell = self._shell32
        count = shell.DragQueryFileW(wintypes.HANDLE(hdrop),
                                     0xFFFFFFFF, None, 0)
        paths: List[str] = []
        for i in range(int(count or 0)):
            need = shell.DragQueryFileW(wintypes.HANDLE(hdrop), i, None, 0)
            buf = ctypes.create_unicode_buffer(int(need) + 2)
            shell.DragQueryFileW(wintypes.HANDLE(hdrop), i, buf, len(buf))
            if buf.value:
                paths.append(buf.value)
        try:
            shell.DragFinish(wintypes.HANDLE(hdrop))
        except Exception:
            pass
        if paths:
            self._paths.put(paths)

    # ------------------------------------------------------------ 取路径
    def poll(self) -> List[List[str]]:
        """主线程调用：取走所有已投放的路径组（非阻塞）。"""
        out: List[List[str]] = []
        while True:
            try:
                out.append(self._paths.get_nowait())
            except queue.Empty:
                break
        return out

    def detach(self) -> None:
        """恢复原窗口过程（关窗时调用）。"""
        try:
            from ctypes import wintypes
            if self._hwnd and self._old and self._user32 is not None:
                setter = getattr(self._user32, "SetWindowLongPtrW", None)
                if setter is None:
                    setter = getattr(self._user32, "SetWindowLongW")
                setter(wintypes.HWND(self._hwnd), GWLP_WNDPROC, self._old)
        except Exception:
            pass
        self.enabled = False
