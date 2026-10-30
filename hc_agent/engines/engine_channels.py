"""engine_channels.py — 引擎多通道与自动降级（移植自天枢一号的设计）。

背景：原实现只走一条通道（OpenDSSDirect.py 进程内调用）。实测过的后果是
**引擎一旦在进程内出问题，整个任务就失败**，没有退路：

  * 天枢一号实测：`DPF_ENGINE_BACKEND=dss-python`（进程内原生库）时，
    `POST /api/run` 会让 uvicorn 进程**无 traceback 直接消失**（原生崩溃）；
    换 CLI 通道后同一请求正常成功。
  * 本工程同理：唯一的 OpenDSSDirect 通道若因原生库状态损坏而挂死/崩溃，
    没有第二条路可走。

本模块提供三条通道，并支持按可用性自动降级：

  +----------------+--------------------------------------------------------+
  | 通道            | 说明                                                   |
  +================+========================================================+
  | ``opendssdirect`` | 进程内、速度最快、精度最高。默认首选。              |
  | ``dss-python``    | 进程内 ALT-DSS 接口，与上者引擎同源但绑定不同。     |
  | ``cli``           | 独立进程 ``OpenDSScmd.exe``，**挂死可强杀**，        |
  |                   | 代价是 CSV 导出只有 5~6 位有效数字。                |
  +----------------+--------------------------------------------------------+

**通道能力差异必须显式声明**，不能让调用方以为换了通道还得到同样的东西：
CLI 通道的有效位数更低，因此不可用于字段级复算比对（容差 1e-6 量级）。
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 通道定义
CHANNEL_OPENDSSDIRECT = "opendssdirect"
CHANNEL_DSS_PYTHON = "dss-python"
CHANNEL_CLI = "cli"

ALL_CHANNELS = (CHANNEL_OPENDSSDIRECT, CHANNEL_DSS_PYTHON, CHANNEL_CLI)

# 自动降级顺序：先精度最高、隔离性最差的，最后是不精确但隔离的。
DEFAULT_FALLBACK_ORDER = (
    CHANNEL_OPENDSSDIRECT,
    CHANNEL_DSS_PYTHON,
    CHANNEL_CLI,
)

# 环境变量（沿用天枢一号的命名习惯，便于两套代码对照）
ENV_CHANNEL = "HC_ENGINE_BACKEND"
ENV_CLI_PATH = "HC_OPENDSS_CLI"


@dataclass
class ChannelCapability:
    """通道能力声明（设计文档 8.2：适配器必须声明支持的分析类型、单位、
    模型能力、版本兼容矩阵和已知限制）。"""

    channel: str
    in_process: bool
    # 数值有效位数。进程内通道为双精度；CLI 通道经 CSV 导出，实测 5~6 位。
    significant_digits: Optional[int]
    supports_python_callbacks: bool
    killable_on_hang: bool
    # 字段级复算容差下限：低于该容差时本通道不适用于复算比对
    min_reliable_tolerance: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "in_process": self.in_process,
            "significant_digits": self.significant_digits,
            "supports_python_callbacks": self.supports_python_callbacks,
            "killable_on_hang": self.killable_on_hang,
            "min_reliable_tolerance": self.min_reliable_tolerance,
            "notes": list(self.notes),
        }


CAPABILITIES: Dict[str, ChannelCapability] = {
    CHANNEL_OPENDSSDIRECT: ChannelCapability(
        channel=CHANNEL_OPENDSSDIRECT, in_process=True, significant_digits=None,
        supports_python_callbacks=True, killable_on_hang=False,
        min_reliable_tolerance=1e-9,
        notes=["双精度；与独立复算使用同一接口包版本",
               "进程内原生库异常时本进程会一起死，需由看门狗兜底"],
    ),
    CHANNEL_DSS_PYTHON: ChannelCapability(
        channel=CHANNEL_DSS_PYTHON, in_process=True, significant_digits=None,
        supports_python_callbacks=True, killable_on_hang=False,
        min_reliable_tolerance=1e-9,
        notes=["ALT-DSS 接口（IDSS/IText），与 OpenDSSDirect 引擎同源",
               "进程内原生库异常时本进程会一起死，需由看门狗兜底"],
    ),
    CHANNEL_CLI: ChannelCapability(
        channel=CHANNEL_CLI, in_process=False, significant_digits=6,
        supports_python_callbacks=False, killable_on_hang=True,
        # CLI 经 CSV 导出，有效位约 5~6 位，故不适用于 1e-6 以下的字段级比对
        min_reliable_tolerance=1e-5,
        notes=["独立进程，挂死可强杀；适合隔离与对照验证",
               "经 CSV 导出，有效位约 5~6 位，**不可**用于字段级复算比对",
               "不支持 Python 回调，逐时间片设值需改为生成 DSS 文本"],
    ),
}


def channel_capability(channel: str) -> ChannelCapability:
    return CAPABILITIES.get(channel, CAPABILITIES[CHANNEL_OPENDSSDIRECT])


# ------------------------------------------------------------ CLI 可执行定位
_CLI_NAME = "OpenDSScmd.exe"

# 常见安装位置（按优先级）。天枢一号只用环境变量；这里额外给出实测过的本机路径，
# 并保留"找不到就报 unknown 而不是猜"的原则。
_CLI_CANDIDATES: Tuple[Path, ...] = (
    Path(r"F:\BaiduNetdiskDownload\OpenDSS\x64") / _CLI_NAME,
    Path(r"C:\Program Files\OpenDSS\x64") / _CLI_NAME,
    Path(r"C:\Program Files (x86)\OpenDSS\x64") / _CLI_NAME,
    Path(r"C:\OpenDSS\x64") / _CLI_NAME,
)


def locate_cli(explicit: Optional[str] = None) -> Optional[Path]:
    """定位 ``OpenDSScmd.exe``。

    顺序：显式参数 → 环境变量 → PATH → 已知安装位置。
    找不到返回 None，**不猜测、不伪造**（上层据此把通道标为不可用）。
    """
    for cand in (explicit, os.environ.get(ENV_CLI_PATH)):
        if cand:
            p = Path(cand)
            if p.is_file():
                return p
            if p.is_dir() and (p / _CLI_NAME).is_file():
                return p / _CLI_NAME
    which = shutil.which(_CLI_NAME)
    if which:
        return Path(which)
    for p in _CLI_CANDIDATES:
        if p.is_file():
            return p
    return None


# ------------------------------------------------------------ 通道可用性探测
@dataclass
class ChannelHealth:
    channel: str
    available: bool
    version: str = ""
    detail: str = ""
    capability: Optional[ChannelCapability] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "available": self.available,
            "version": self.version,
            "detail": self.detail,
            "capability": (self.capability.to_dict()
                           if self.capability else None),
        }


def _probe_opendssdirect() -> ChannelHealth:
    from hc_agent import runtime_env
    lib = runtime_env.ensure_engine_on_path()
    if lib is None:
        return ChannelHealth(CHANNEL_OPENDSSDIRECT, False,
                             detail="OpenDSSDirect.py 不在可用路径中",
                             capability=CAPABILITIES[CHANNEL_OPENDSSDIRECT])
    try:
        import opendssdirect as dss
        ver = str(dss.Basic.Version()).strip()
        return ChannelHealth(CHANNEL_OPENDSSDIRECT, True, version=ver,
                             detail=f"pylib={lib}",
                             capability=CAPABILITIES[CHANNEL_OPENDSSDIRECT])
    except Exception as exc:
        return ChannelHealth(CHANNEL_OPENDSSDIRECT, False,
                             detail=f"{type(exc).__name__}: {exc}",
                             capability=CAPABILITIES[CHANNEL_OPENDSSDIRECT])


def _probe_dss_python() -> ChannelHealth:
    try:
        mod = importlib.import_module("dss")
    except Exception as exc:
        return ChannelHealth(CHANNEL_DSS_PYTHON, False,
                             detail=f"import dss 失败: {type(exc).__name__}: {exc}",
                             capability=CAPABILITIES[CHANNEL_DSS_PYTHON])
    try:
        ver = str(mod.DSS.Version).strip()
        return ChannelHealth(CHANNEL_DSS_PYTHON, True, version=ver,
                             detail="ALT-DSS 接口可用",
                             capability=CAPABILITIES[CHANNEL_DSS_PYTHON])
    except Exception as exc:
        return ChannelHealth(CHANNEL_DSS_PYTHON, False,
                             detail=f"取版本失败: {type(exc).__name__}: {exc}",
                             capability=CAPABILITIES[CHANNEL_DSS_PYTHON])


def _probe_cli() -> ChannelHealth:
    exe = locate_cli()
    if exe is None:
        return ChannelHealth(CHANNEL_CLI, False,
                             detail=f"未找到 {_CLI_NAME}（可用环境变量 "
                                    f"{ENV_CLI_PATH} 指定）",
                             capability=CAPABILITIES[CHANNEL_CLI])
    # 探活：CLI 从 stdin 读命令，用一条最简命令确认可执行。
    # ⚠️ 本版 OpenDSScmd.exe **不能**把脚本当参数传，必须 stdin 重定向
    # （见 opendss_demo 的本机实测记录）。
    try:
        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "probe.dss"
            probe.write_text("Clear\n", encoding="ascii")
            p = subprocess.run([str(exe)], input="Clear\n",
                               capture_output=True, text=True, timeout=60,
                               cwd=td)
        banner = ""
        for line in (p.stdout or "").splitlines():
            if "OpenDSS" in line or "Version" in line:
                banner = line.strip()
                break
        return ChannelHealth(CHANNEL_CLI, True,
                             version=banner or "OpenDSScmd",
                             detail=f"exe={exe}",
                             capability=CAPABILITIES[CHANNEL_CLI])
    except Exception as exc:
        return ChannelHealth(CHANNEL_CLI, False,
                             detail=f"探活失败: {type(exc).__name__}: {exc}",
                             capability=CAPABILITIES[CHANNEL_CLI])


_PROBERS = {
    CHANNEL_OPENDSSDIRECT: _probe_opendssdirect,
    CHANNEL_DSS_PYTHON: _probe_dss_python,
    CHANNEL_CLI: _probe_cli,
}


def probe_all() -> Dict[str, ChannelHealth]:
    """探测全部通道的可用性（不抛异常，失败即标不可用）。"""
    out: Dict[str, ChannelHealth] = {}
    for ch, fn in _PROBERS.items():
        try:
            out[ch] = fn()
        except Exception as exc:  # pragma: no cover - 兜底
            out[ch] = ChannelHealth(ch, False,
                                    detail=f"探测异常: {type(exc).__name__}: {exc}",
                                    capability=CAPABILITIES[ch])
    return out


def select_channel(preferred: Optional[str] = None,
                   *, fallback: bool = True,
                   require_killable: bool = False,
                   max_tolerance: Optional[float] = None
                   ) -> Tuple[Optional[str], Dict[str, Any]]:
    """选择可用通道，必要时按降级顺序回退。

    参数：
      preferred       首选通道；None 时读环境变量 ``HC_ENGINE_BACKEND``
      fallback        首选不可用时是否自动降级
      require_killable 是否要求通道可强杀（长任务/不可信算例时使用）
      max_tolerance   所需字段级容差；低于通道能力时不予选用

    返回 ``(选中的通道 or None, 说明)``。选不到时返回 None 并给出原因，
    **不静默使用一个不满足要求的通道**。
    """
    health = probe_all()
    want = (preferred or os.environ.get(ENV_CHANNEL) or "").strip().lower() or None

    order: List[str] = []
    if want:
        if want not in ALL_CHANNELS:
            return None, {"reason": f"未知通道 '{want}'",
                          "valid": list(ALL_CHANNELS),
                          "health": {k: v.to_dict() for k, v in health.items()}}
        order.append(want)
    if fallback:
        order.extend(c for c in DEFAULT_FALLBACK_ORDER if c not in order)

    rejected: List[Dict[str, Any]] = []
    for ch in order:
        h = health.get(ch)
        if h is None or not h.available:
            rejected.append({"channel": ch, "why": "不可用",
                             "detail": h.detail if h else ""})
            continue
        cap = channel_capability(ch)
        if require_killable and not cap.killable_on_hang:
            rejected.append({"channel": ch, "why": "要求可强杀但该通道为进程内",
                             "detail": cap.notes[0] if cap.notes else ""})
            continue
        if max_tolerance is not None and cap.min_reliable_tolerance > max_tolerance:
            rejected.append({
                "channel": ch, "why": "容差能力不足",
                "detail": f"需要 ≤{max_tolerance:g}，该通道可靠下限为 "
                          f"{cap.min_reliable_tolerance:g}"})
            continue
        return ch, {
            "selected": ch,
            "requested": want,
            "fell_back": bool(want and ch != want),
            "version": h.version,
            "detail": h.detail,
            "capability": cap.to_dict(),
            "rejected": rejected,
            "health": {k: v.to_dict() for k, v in health.items()},
        }

    return None, {"reason": "没有任何可用通道满足要求",
                  "requested": want, "rejected": rejected,
                  "health": {k: v.to_dict() for k, v in health.items()}}


def describe_channels() -> str:
    """人类可读的通道清单（用于 CLI 与健康检查输出）。"""
    health = probe_all()
    lines = ["引擎通道状态："]
    for ch in ALL_CHANNELS:
        h = health[ch]
        mark = "可用" if h.available else "不可用"
        ver = (h.version or "").splitlines()[0][:60] if h.version else ""
        lines.append(f"  [{mark:4s}] {ch:14s} {ver}")
        if h.detail and not h.available:
            lines.append(f"         原因: {h.detail}")
        cap = channel_capability(ch)
        lines.append(f"         隔离={'独立进程' if cap.killable_on_hang else '进程内'}"
                     f"  有效位={cap.significant_digits or '双精度'}"
                     f"  复算容差下限={cap.min_reliable_tolerance:g}")
    return "\n".join(lines)
