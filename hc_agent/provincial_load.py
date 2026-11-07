"""provincial_load.py — 公开省级小时负荷数据 → system_level.Series 适配器。

数据来源（论文 Figshare DOI: 10.6084/m9.figshare.29832701）：
    中国 31 个省级区域，2015–2024 小时级负荷，由气象变量**重构**（非实测计量）。
    引用时必须写“基于气象重构的省级负荷曲线”，不得写成“实测”。

设计约定：
    · 列名不固定 → 自动识别时间列、省份列、负荷列
    · 单位不猜 → 用 median 值判断 MW/kW（>10 万视为 kW），并在返回时说明
    · 省份不写死 → province 参数按名称包含匹配（"山东"、"Shandong" 均可）
    · 找不到列就抛错，不静默返回空序列
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from hc_agent.system_level import Series

TIME_HINTS = ("time", "date", "timestamp", "datetime", "hour", "时间", "日期")
PROV_HINTS = ("province", "region", "area", "省份", "地区", "省")
LOAD_HINTS = ("load", "demand", "mw", "kw", "负荷", "用电")


@dataclass
class LoadInfo:
    path: str
    province: str
    rows: int
    unit: str          # "kW" | "MW"
    median_kw: float


def _pick(cols: list[str], hints: tuple[str, ...]) -> Optional[str]:
    low = {c: c.strip().lower() for c in cols}
    for h in hints:
        for c, lc in low.items():
            if h in lc:
                return c
    return None


def read_series(path: Path, province: Optional[str] = None,
                time_col: str = "", load_col: str = "") -> tuple[Series, LoadInfo]:
    """读 csv，返回 8760 量级的负荷序列与取数说明。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到数据文件：{path}")

    with path.open(encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        cols = list(rdr.fieldnames or [])
        rows = list(rdr)
    if not rows:
        raise ValueError(f"{path.name} 没有数据行")

    c_time = time_col or _pick(cols, TIME_HINTS)
    c_load = load_col or _pick(cols, LOAD_HINTS)
    c_prov = _pick(cols, PROV_HINTS)

    if c_load is None:
        raise ValueError(f"识别不到负荷列，实际列：{cols}")
    if province and c_prov is None:
        raise ValueError(f"要求按省份筛选，但识别不到省份列，实际列：{cols}")

    picked: list[float] = []
    for r in rows:
        if province and province not in str(r.get(c_prov, "")):
            continue
        try:
            picked.append(float(str(r[c_load]).replace(",", "").strip()))
        except (TypeError, ValueError):
            continue
    if not picked:
        raise ValueError(f"按 province={province!r} 过滤后没有数据")

    med = sorted(picked)[len(picked) // 2]
    unit = "MW" if med < 100_000 else "kW"
    scale = 1000.0 if unit == "MW" else 1.0
    load_kw = [v * scale for v in picked]

    # 分布式出力：数据集不含，先给占位（全 0），由调用方叠加曲线
    dg_pu = [0.0] * len(load_kw)
    info = LoadInfo(str(path), province or "(全部)", len(load_kw), unit, med * scale)
    return Series(load_kw, dg_pu), info


def with_pv_curve(s: Series, peak_kw: float, hours=(7, 17)) -> Series:
    """给负荷序列叠加一条光伏标幺曲线（默认 7–17 时正弦峰）。"""
    import math
    lo, hi = hours
    dg = []
    for t in range(s.n):
        h = t % 24
        dg.append(math.sin(math.pi * (h - lo) / (hi - lo)) if lo <= h <= hi else 0.0)
    return Series(s.load_kw, [peak_kw * v for v in dg])
