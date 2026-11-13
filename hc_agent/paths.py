"""paths.py — 资源定位的唯一入口（源码运行与 PyInstaller 打包运行都成立）。

**为什么必须集中处理**：打包后 `__file__` 指向解压出来的临时目录或
`_internal` 子目录，所有用 `Path(__file__).parents[N]` 推算工程根的代码都会
指错地方 —— 表现为"打包后找不到 skill_registry.json / rules.yaml / static"，
而且**源码运行完全正常**，只有拿到别人机器上才炸。

两种形态：
  * 源码：   project/  = 仓库根目录（本文件的 parents[2]）
  * 打包：   project/  = sys._MEIPASS（onedir 下即 exe 旁的 _internal）

数据分两类，**必须分开**：
  * **只读资源**（随包分发）：static / skills / rules 模板 → 在 bundle 里；
  * **可写目录**（运行产物）：runs / 日志 → 用户目录，绝不能写进安装路径
    （Program Files 通常不可写，且升级会覆盖）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

APP_NAME = "HC-Agent"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """程序安装目录（用户视角的“程序目录”）。

    打包后是 exe 所在目录（HC-Agent\），不是 _internal；
    源码模式下与 bundle_root 相同。界面显示用这个，资源查找仍用 bundle_root。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundle_root() -> Path:
    """只读资源根目录。

    * 打包（onedir）：`sys._MEIPASS` → exe 旁的 `_internal`
    * 源码：         本文件的上两级（仓库根）
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # 极少数情况下没有 _MEIPASS：退回 exe 所在目录
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource(*parts: str) -> Path:
    """拼一个**只读**资源路径（static/skills/rules/...）。"""
    return bundle_root().joinpath(*parts)


def data_root() -> Path:
    """可写数据根目录（运行产物、日志、崩溃记录）。

    优先级：环境变量 `HC_DATA_DIR` → 打包时用户 AppData → 源码时仓库根。
    **打包后绝不写进安装目录**：那里通常不可写，且升级会覆盖用户数据。
    """
    env = os.environ.get("HC_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / ("." + APP_NAME.lower())
    return bundle_root()


def _can_create(d: Path) -> bool:
    """能否真的在该路径下建目录（不只是"父目录有写权限位"）。

    `os.access` 在受限环境里会给出乐观的答案 —— 实测遇到过：access 报 true，
    但 mkdir 抛 WinError 5。所以这里**实际建一次**再清理。
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False
    return _is_writable(d)


def data_root_safe() -> Path:
    """一定是可写的 data_root，必要时逐级降级。

    降级顺序：首选 → 用户主目录隐藏目录 → 系统临时目录。
    这样即使策略/权限拦住了 AppData，程序仍能运行并把产物写到能找到的地方，
    而不是第一次就跑不起来（拦在 LOCALAPPDATA 的受限环境里实测过）。
    """
    import tempfile
    first = data_root()
    if _can_create(first):
        return first
    for cand in (Path.home() / ("." + APP_NAME.lower()),
                 Path(tempfile.gettempdir()) / APP_NAME):
        if _can_create(cand):
            return cand
    return first          # 都不行：返回首选，让上层如实报错


def runs_root() -> Path:
    """运行产物目录。``HC_RUNS`` 覆盖，便于共享/迁移。"""
    env = os.environ.get("HC_RUNS")
    if env:
        return Path(env).expanduser().resolve()
    return data_root_safe() / "runs"


def logs_root() -> Path:
    return data_root_safe() / "logs"


def ensure_writable(*dirs: Optional[Path]) -> None:
    """确保目录存在。**绝不抛出** —— 观测/数据目录创建失败不应让任务崩掉。

    并发注意：GUI 与 engine_host 是**两个进程**，首次启动会同时尝试创建同一个
    数据目录。`mkdir(parents=True)` 在这种竞态下可能抛 PermissionError /
    FileNotFoundError（一个进程刚创建了上层目录，另一个正在创建子目录）。
    因此失败后**重试一次并复核**，真正的"末尾目录已存在"视为成功。
    """
    for d in dirs:
        if d is None:
            continue
        p = Path(d)
        try:
            p.mkdir(parents=True, exist_ok=True)
            continue
        except Exception:
            pass
        # 重试：可能只是竞态，稍等后复核
        time.sleep(0.05)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            # 仍然失败：只有在目录确实不存在时才算失败（静默）
            pass


def writable_runs_root() -> Path:
    """可写的运行目录：优先 data_root/runs，不可创建时退到临时目录。

    打包后安装路径可能不可写（Program Files），因此**不能**退回 bundle；
    退到用户临时目录，保证任务至少能跑完并留下产物。
    """
    import tempfile
    root = runs_root()
    ensure_writable(root)
    if _is_writable(root):
        return root
    fallback = Path(tempfile.gettempdir()) / APP_NAME / "runs"
    ensure_writable(fallback)
    return fallback


def _is_writable(d: Path) -> bool:
    try:
        probe = Path(d) / ".write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def describe() -> dict:
    """诊断用：当前形态与各关键路径。"""
    return {
        "frozen": is_frozen(),
        "executable": sys.executable,
        "bundle_root": str(bundle_root()),
        "app_root": str(app_root()),
        "data_root": str(data_root()),
        "runs_root": str(runs_root()),
        "logs_root": str(logs_root()),
        "skills_registry": str(resource("skills", "skill_registry.json")),
    }
