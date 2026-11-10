"""installer_app.py — 配网承载力智能体安装程序（单文件 setup.exe 的载荷逻辑）。

打包方式（PyInstaller --onefile，把 release zip 作为数据嵌入）：
    pyinstaller --onefile --windowed --name HC-Agent-1.1.0-Setup \
        --add-data "release\\HC-Agent-1.1.0-win64-20261110.zip;." tools\\installer_app.py

运行行为：
    · 无参数        → 图形安装界面（选目录、安装、建快捷方式）
    · --silent DIR  → 静默安装到 DIR（供批处理/自动化测试）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APP_NAME = "配网承载力智能体"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "配网承载力智能体"


def payload() -> Path:
    """取出内嵌的 release zip（onefile 模式下在 _MEIPASS）。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    cands = sorted(base.glob("HC-Agent-*.zip"))
    if not cands:
        raise SystemExit("安装包损坏：找不到内嵌的发行包")
    return cands[0]


def install(target: Path, progress=lambda s: None) -> Path:
    """解压到 target，返回 exe 路径。"""
    zp = payload()
    target.mkdir(parents=True, exist_ok=True)
    progress("正在解压程序文件…")
    with zipfile.ZipFile(zp) as z:
        z.extractall(target)
    exe = target / "HC-Agent" / "HC-Agent.exe"
    if not exe.is_file():
        # 兼容 zip 顶层就是文件的情况
        hits = list(target.rglob("HC-Agent.exe"))
        if not hits:
            raise RuntimeError("解压后找不到 HC-Agent.exe")
        exe = hits[0]
    progress("正在创建桌面快捷方式…")
    _shortcut(exe)
    progress("安装完成")
    return exe


def _shortcut(exe: Path) -> None:
    """用 PowerShell 建桌面快捷方式（避免引入第三方 COM 依赖）。"""
    lnk = Path.home() / "Desktop" / f"{APP_NAME}.lnk"
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
        f"'{lnk}');$s.TargetPath='{exe}';"
        f"$s.WorkingDirectory='{exe.parent}';"
        f"$s.Description='{APP_NAME}';$s.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title(f"{APP_NAME} 安装程序")
    root.geometry("560x300")
    root.resizable(False, False)

    tk.Label(root, text=f"{APP_NAME}", font=("微软雅黑", 16, "bold"),
             fg="#014C83").pack(pady=(18, 2))
    tk.Label(root, text="版本 1.1.0　·　支持 Windows 10/11 x64",
             font=("微软雅黑", 9), fg="#5A626A").pack()

    box = tk.Frame(root)
    box.pack(fill="x", padx=24, pady=18)
    tk.Label(box, text="安装目录：", font=("微软雅黑", 10)).pack(side="left")
    var = tk.StringVar(value=str(DEFAULT_DIR))
    tk.Entry(box, textvariable=var, font=("Consolas", 9), width=44).pack(
        side="left", padx=6)

    def browse() -> None:
        d = filedialog.askdirectory(initialdir=str(Path.home()))
        if d:
            var.set(d)

    tk.Button(box, text="浏览…", command=browse).pack(side="left")

    hint = tk.Label(root, text="安装后将创建桌面快捷方式；授权文件可稍后放入程序目录。",
                    font=("微软雅黑", 9), fg="#5A626A")
    hint.pack()

    def do_install() -> None:
        target = Path(var.get().strip())
        if not target:
            messagebox.showwarning("提示", "请先选择安装目录")
            return
        try:
            hint.config(text="正在安装，请稍候…", fg="#014C83")
            root.update_idletasks()
            exe = install(target, lambda s: (hint.config(text=s), root.update_idletasks()))
        except Exception as exc:
            messagebox.showerror("安装失败", f"{type(exc).__name__}: {exc}")
            hint.config(text="安装失败", fg="#864B48")
            return
        if messagebox.askyesno("安装完成", f"已安装到：\n{target}\n\n现在启动吗？"):
            try:
                subprocess.Popen([str(exe)], cwd=str(exe.parent))
            except Exception:
                pass
        root.destroy()

    tk.Button(root, text="开始安装", command=do_install, bg="#014C83", fg="white",
              font=("微软雅黑", 11), width=16, height=1,
              relief="flat").pack(pady=14)
    root.mainloop()


if __name__ == "__main__":
    def _log(msg: str, line: str) -> None:
        """windowed 模式没有 stdout，日志写文件。"""
        try:
            with open(line, "a", encoding="utf-8") as fh:
                fh.write(msg + chr(10))
        except Exception:
            pass

    if len(sys.argv) >= 3 and sys.argv[1] == "--silent":
        tgt = Path(sys.argv[2])
        logf = str(tgt.parent / (tgt.name + "_install.log"))
        try:
            e = install(tgt, lambda s: _log(s, logf))
            _log("OK " + str(e), logf)
        except Exception as exc:
            _log(f"FAIL {type(exc).__name__}: {exc}", logf)
    else:
        gui()
