"""运行环境定位：唯一电气计算引擎 OpenDSS 的 Python 调用入口。

设计文档 17.10：全系统只使用 OpenDSS 进行潮流、故障研究与设备电气量计算，
Python 统一通过 OpenDSSDirect.py 接入。该接口使用 DSS-Extensions 提供的
OpenDSS 实现，部署时同时锁定接口包、底层库和 Python 版本。

本模块是唯一允许 `import opendssdirect` 的位置之外的路径装配器；
其余模块必须经 hc_agent.engines.opendss_adapter 求解。
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from hc_agent import paths

# 本机已实测可用的社区版引擎（DSS C-API），由 install_wheels.py 解包到工作区。
# 见 opendss_demo/README.md 与 opendss_demo/compare/README.md。
#
# ⚠️ 这些候选路径**只在源码运行时有效**：打包后 `__file__` 指向 _internal，
# `parents[2]` 会指到安装目录之外。打包时引擎 DLL 直接进 bundle，不需要这条路径。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]  # ...\default-workspace
_DEFAULT_PYLIB_CANDIDATES = [
    _WORKSPACE_ROOT / "opendss_demo" / "pylibs",
    Path(__file__).resolve().parents[1] / "vendor" / "pylibs",
]

# 第二引擎 pandapower 装在**独立目录**里：它要求 pandas~=2.3，
# 而主运行时可能是 pandas 3.x。混装会把主环境的 pandas 降级，
# 所以它自带一份依赖，只在需要时插到 sys.path 最前。
_PANDAPOWER_CANDIDATES = [
    _WORKSPACE_ROOT / "pylibs_pandapower",
    Path(__file__).resolve().parents[1] / "vendor" / "pylibs_pandapower",
]


def _local_pylib_config() -> Dict[str, List[Path]]:
    """读工程根的 `local_pylibs.json`，补充引擎库目录。

    引擎库（opendss_demo/pylibs、pylibs_pandapower）体积大、且位于**工程之外**
    的工作区里。工程被复制/搬走后，靠 `parents[2]` 推出来的相对路径就失效了。
    这个文件让“库在哪”成为显式配置，而不是靠目录层级猜：

        {"opendss": ["C:/.../opendss_demo/pylibs"],
         "pandapower": ["C:/.../pylibs_pandapower"]}

    文件不存在时返回空 —— 不影响原本的相对路径探测。
    """
    out: Dict[str, List[Path]] = {"opendss": [], "pandapower": []}
    try:
        import json as _json
        f = Path(__file__).resolve().parents[1] / "local_pylibs.json"
        if not f.is_file():
            return out
        raw = _json.loads(f.read_text(encoding="utf-8"))
        for key in ("opendss", "pandapower"):
            for item in (raw.get(key) or []):
                cand = Path(str(item)).expanduser()
                if cand.exists():
                    out[key].append(cand)
    except Exception:
        pass
    return out

def discover_pylib_paths() -> List[Path]:
    """返回所有候选的 OpenDSSDirect.py 运行库目录（存在的）。"""
    seen: List[Path] = []
    env = os.environ.get("HC_OPENDSS_PYLIB")
    if env:
        seen.append(Path(env))
    # 打包后引擎随包提供，导入由 PyInstaller 的引导负责，不再依赖外部目录；
    # 只在源码运行时探测工作区候选，避免把用户的无关目录插进 sys.path。
    if not paths.is_frozen():
        for cand in _DEFAULT_PYLIB_CANDIDATES:
            if cand not in seen:
                seen.append(cand)
    for cand in _local_pylib_config()["opendss"]:
        if cand not in seen:
            seen.append(cand)
    return [p for p in seen if p.exists()]


def ensure_engine_on_path() -> Optional[Path]:
    """把可用的 OpenDSS 运行库目录插入 sys.path，返回实际使用的目录。

    返回 None 表示引擎不可用 —— 调用方必须据此阻断，不得用估算替代。
    打包运行时若引擎已在 bundle 内可直接导入，返回 bundle 根作为标记。
    """
    for lib in discover_pylib_paths():
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        try:
            import opendssdirect  # noqa: F401
            return lib
        except Exception:
            continue
    # 打包后：引擎 DLL 已随 exe 分发，直接尝试导入
    if paths.is_frozen():
        try:
            import opendssdirect  # noqa: F401
            return paths.bundle_root()
        except Exception:
            return None
    return None


def engine_fingerprint() -> Dict[str, Any]:
    """采集引擎指纹，写入运行清单（设计文档 8.1 / 表 56 manifest.json）。

    run 清单记录 engine_id=opendss、底层版本、接口包版本及模型哈希。
    """
    lib_used = ensure_engine_on_path()
    fp: Dict[str, Any] = {
        "engine_id": "opendss",
        "interface": "OpenDSSDirect.py",
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "pylib_path": str(lib_used) if lib_used else None,
    }
    if lib_used is None:
        fp.update({"available": False,
                   "error": "OpenDSSDirect.py 未找到；请设置 HC_OPENDSS_PYLIB "
                            "或安装 OpenDSSDirect.py"})
        return fp

    try:
        import opendssdirect as dss
        fp["interface_version"] = getattr(dss, "__version__", "unknown")
        fp["available"] = True
        try:
            ver = dss.Basic.Version()
            fp["engine_version"] = ver  # 底层 DSS C-API / OpenDSS 版本字符串
        except Exception as exc:  # pragma: no cover
            fp["engine_version"] = f"unavailable ({exc})"
    except Exception as exc:  # pragma: no cover
        fp.update({"available": False, "error": str(exc)})
    return fp


# 锁定版本常量：设计文档要求 LOCKED_DSS_VERSION / LOCKED_API_VERSION
# 必须替换为精确可用版本。此处以本机实测版本作为锁定基线。
LOCKED_INTERFACE = "OpenDSSDirect.py"
LOCKED_INTERFACE_VERSION = "0.9.4"
LOCKED_ENGINE_BACKEND = "DSS C-API 0.14.5 (OpenDSS SVN 3723)"


def check_locked_version(strict: bool = False) -> Dict[str, Any]:
    """核对当前环境是否与锁定版本一致（设计文档 8.2 版本兼容矩阵）。"""
    fp = engine_fingerprint()
    ok = True
    notes: List[str] = []
    if not fp.get("available"):
        ok = False
        notes.append("引擎不可用")
    else:
        if fp.get("interface_version") != LOCKED_INTERFACE_VERSION:
            msg = (f"接口包版本 {fp.get('interface_version')} != 锁定版本 "
                   f"{LOCKED_INTERFACE_VERSION}")
            notes.append(msg)
            ok = False
        engine_ver = str(fp.get("engine_version", ""))
        if "0.14.5" not in engine_ver:
            notes.append(f"底层引擎版本未含 0.14.5：{engine_ver}")
    fp["locked_ok"] = ok if strict else True
    fp["locked_expectation"] = {
        "interface": LOCKED_INTERFACE,
        "interface_version": LOCKED_INTERFACE_VERSION,
        "backend": LOCKED_ENGINE_BACKEND,
    }
    fp["lock_notes"] = notes
    if strict and not ok:
        from hc_agent.errors import ComputeFailedError, ERR_DSS_DEPENDENCY_LOAD_FAILED
        raise ComputeFailedError("; ".join(notes) or "引擎版本不匹配",
                                 code=ERR_DSS_DEPENDENCY_LOAD_FAILED)
    return fp

def discover_pandapower_paths() -> List[Path]:
    """返回 pandapower 独立库目录的候选（存在的）。"""
    seen: List[Path] = []
    env = os.environ.get("HC_PANDAPOWER_PYLIB")
    if env:
        seen.append(Path(env))
    if not paths.is_frozen():
        for cand in _PANDAPOWER_CANDIDATES:
            if cand not in seen:
                seen.append(cand)
    for cand in _local_pylib_config()["pandapower"]:
        if cand not in seen:
            seen.append(cand)
    return [p for p in seen if p.exists()]


def ensure_pandapower_on_path() -> Optional[Path]:
    """把 pandapower 独立库目录插到 sys.path **最前**，返回实际使用的目录。

    为什么插最前：该目录自带 pandas 2.3.3（pandapower 的硬约束），
    若让它排到主环境的 pandas 3.x 之后，import 到的就是 3.x，
    pandapower 很可能起不来。插最前等于"进这个目录就用这套依赖"。

    返回 None 表示不可用 —— 调用方必须据此降级，不得假装引擎存在。
    """
    for lib in discover_pandapower_paths():
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        try:
            import pandapower  # noqa: F401
            return lib
        except Exception:
            continue
    if paths.is_frozen():
        try:
            import pandapower  # noqa: F401
            return paths.bundle_root()
        except Exception:
            return None
    return None

