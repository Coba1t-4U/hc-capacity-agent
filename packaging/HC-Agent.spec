# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir）。

关键决策及理由
==============

**onedir 而非 onefile**
    引擎 DLL 约 74 MB（dss_capi.dll + numpy + openblas），onefile 每次启动都要
    把这些解压到临时目录，冷启动要多花 10~20 秒，且退出时清理失败会在
    临时目录堆积。onedir 一次解压，启动快得多。代价是一个文件夹而不是单文件。

**引擎 DLL 必须显式收集**
    OpenDSS 不装在 site-packages 里，而在工作区 `opendss_demo/pylibs`；
    PyInstaller 的依赖分析**看不到**它们。不显式收集的后果是打包成功、
    启动正常，但一跑计算就 `ImportError` / `DLL load failed` —— 这正是最
    容易漏、也最难在开发机上复现的一类问题。

**排除调试版与链接期文件**
    `dss_python_backend` 里同时有发布版（`dss_capi.dll`、`_dss_capi.pyd`）和
    调试版（`*d` 结尾，体量相当），以及 `.lib`/`.a` 链接期文件。
    实测 `import dss` 只加载 `_dss_capi.pyd` → `dss_capi.dll`，所以
    调试版与链接文件可以安全排除，省约 36 MB。

**排除用不到的重量级包**
    matplotlib / scipy / pytest / tkinter 以外的东西按需排。特别注意：
    **不要排除 tkinter**，界面就是它画的。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH 是 spec 所在目录
PYLIB = Path(os.environ.get(
    "HC_OPENDSS_PYLIB",
    ROOT.parent / "opendss_demo" / "pylibs",
))
print(f"[spec] ROOT  = {ROOT}")
print(f"[spec] PYLIB = {PYLIB} (exists={PYLIB.exists()})")

# ---------------------------------------------------------------- 只读资源
datas = [
    (str(ROOT / "hc_agent" / "static"), "hc_agent/static"),
    (str(ROOT / "skills"), "skills"),
    (str(ROOT / "rules"), "rules"),
    # 配网承载力知识库的内置文档。**必须随包**：
    # 缺了它，打包版的"知识库"会一篇都加载不到（源码模式看不见这个问题，
    # 因为那时 bundle_root 就是仓库根目录）。
    # 目标路径要和 hc_agent.knowledge.builtin_dir() 里的
    # paths.resource("hc_agent", "knowledge", "builtin") 对齐。
    (str(ROOT / "hc_agent" / "knowledge" / "builtin"),
     "hc_agent/knowledge/builtin"),
]
# 显式以「二进制」身份收录的 DLL（需要位于加载器可见目录）
binaries = []

# ---------------------------------------------------------------- 引擎二进制
# 只取发布版 + 必需运行时；显式列出而不是整目录拷贝，避免把调试版带进去。
ENGINE_FILES = [
    "dss_python_backend/__init__.py",
    "dss_python_backend/enums.py",
    "dss_python_backend/events.py",
    "dss_python_backend/_dss_capi.pyd",
    "dss_python_backend/_dss_GenUserModel.pyd",
    "dss_python_backend/dss_capi.dll",
    "dss_python_backend/libklusolvex.dll",
    "dss_python_backend/libwinpthread-1.dll",
]

# 整包收录的纯 Python 包（引擎周边）
ENGINE_PACKAGES = [
    "dss",
    "opendssdirect",
    "comtypes",
    "cffi",
    "pycparser",
]

if PYLIB.exists():
    for rel in ENGINE_FILES:
        src = PYLIB / rel
        if src.exists():
            datas.append((str(src), str(Path(rel).parent)))
        else:
            print(f"[spec] 警告：缺少引擎文件 {src}")
    for pkg in ENGINE_PACKAGES:
        src = PYLIB / pkg
        if src.exists():
            datas.append((str(src), pkg))
    # cffi 的二进制后端
    for p in PYLIB.glob("_cffi_backend*.pyd"):
        datas.append((str(p), "."))
    for p in PYLIB.glob("typing_extensions.py"):
        datas.append((str(p), "."))
    # numpy 由 PyInstaller 的 hook 处理，但 pylib 里那份是引擎自带的；
    # 若 site-packages 没有 numpy 则一并带上，避免运行期找不到。
    try:
        import numpy  # noqa: F401
    except Exception:
        src = PYLIB / "numpy"
        if src.exists():
            datas.append((str(src), "numpy"))
            libs = PYLIB / "numpy.libs"
            if libs.exists():
                datas.append((str(libs), "numpy.libs"))

    # ⚠️ numpy 的 OpenBLAS 是**延迟加载**的：`_multiarray_umath.pyd` 依赖
    # `libscipy_openblas64_*.dll`，PyInstaller 的依赖分析解析不到它，于是打出
    # "Library not found" 并漏掉该 DLL。后果是 numpy 完全无法导入 →
    # 所有引擎通道探测失败 → 静默降级到 CLI 通道，界面看起来"能用"但精度不同。
    # 这里显式把 numpy.libs 下的 DLL 放进二进制（而不是 datas），确保它们位于
    # 加载器能看到的目录里。
    numpy_libs = PYLIB / "numpy.libs"
    if numpy_libs.is_dir():
        for dll in numpy_libs.glob("*.dll"):
            binaries.append((str(dll), "."))
        print(f"[spec] numpy.libs: 收录 {len(list(numpy_libs.glob('*.dll')))} 个 DLL")
    # site-packages 里的 numpy.libs 同样处理（源码环境与 pylib 可能各有一份）
    try:
        import numpy as _np
        np_libs = Path(_np.__file__).parent.parent / "numpy.libs"
        if np_libs.is_dir():
            n = 0
            for dll in np_libs.glob("*.dll"):
                binaries.append((str(dll), "."))
                n += 1
            print(f"[spec] site numpy.libs: 收录 {n} 个 DLL")
    except Exception:
        pass
else:
    print(f"[spec] 警告：未找到 pylib 目录 {PYLIB}")

hiddenimports = [
    "hc_agent.engine_host",
    "hc_agent.desktop",
    "hc_agent.desktop.app",
    "hc_agent.desktop.backend",
    "hc_agent.desktop.pages",
    "hc_agent.desktop.runs",
    "hc_agent.desktop.theme",
    "hc_agent.desktop.widgets",
    "hc_agent.runtime.crashlog",
    "hc_agent.runtime.watchdog",
    "hc_agent.runtime.state_store",
    "hc_agent.engines.engine_channels",
    "hc_agent.renderers.caliber",
    "hc_agent.io.converters",
    "tkinter",
    "tkinter.ttk",
    "tkinter.font",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "openpyxl",
    "yaml",
]
hiddenimports += collect_submodules("opendssdirect")
hiddenimports += collect_submodules("dss")

# 明确排除：调试版引擎、链接期文件、与本工具无关的重型包
EXCLUDES = [
    "matplotlib", "scipy", "pandas.tests", "pytest", "IPython",
    "notebook", "jupyter", "sphinx", "PyQt5", "PySide2", "PySide6",
    "_dss_capid", "dss_python_backend._dss_capid",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "hc_agent" / "__main__.py")],
    pathex=[str(ROOT), str(PYLIB)] if PYLIB.exists() else [str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="HC-Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 会破坏部分 DLL 的签名/加载，引擎 DLL 尤其敏感
    console=False,      # 桌面程序：不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "hc_agent_ui" / "assets" / "hc_agent.ico")
    if (ROOT / "hc_agent_ui" / "assets" / "hc_agent.ico").exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HC-Agent",
)
