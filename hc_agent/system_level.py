"""system_level.py — 系统级承载力：时序生产模拟 + 220kV 反送校验 + 县级分解。

构造算例（无实测数据时用）：区域全年 8760 点由典型日曲线扩展，参数取行业常见水平，
在报告中必须标注“构造算例”，结论只用于方法验证。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

HOURS = 8760


@dataclass
class Series:
    load_kw: Sequence[float]          # 逐时负荷
    dg_pu: Sequence[float]            # 逐时分布式出力标幺（按装机 kW 乘）
    @property
    def n(self) -> int:
        return len(self.load_kw)


@dataclass
class Limits:
    reverse_limit_kw: float = 0.0     # 允许向 220kV 反送的上限（0 = 禁止）
    spill_cap: float = 0.05           # 允许弃电率上限
    flex_min_pu: float = 0.3          # 常规机组最小技术出力（占最大负荷）


@dataclass
class County:
    name: str
    load_share: float                 # 负荷占比
    dg_share: float                   # 分布式装机占比
    parent: str | None = None


@dataclass
class Result:
    capacity_kw: float
    reverse_max_kw: float
    reverse_hours: int
    spill_rate: float
    level: str
    counties: list = field(default_factory=list)


def _series_index(t: int) -> int:
    return t % 24


def simulate(s: Series, cap_kw: float, lim: Limits) -> tuple[float, int, float]:
    """给装机 cap 跑一年：返回(最大反送, 反送小时数, 弃电率)。"""
    rev_max = 0.0
    rev_h = 0
    spill = 0.0
    gen_sum = 0.0
    for t in range(s.n):
        out = cap_kw * s.dg_pu[_series_index(t)]
        gen_sum += out
        net = s.load_kw[t] - out                     # 负值=反送
        if net < 0:
            rev = -net
            over = max(0.0, rev - lim.reverse_limit_kw)
            used = rev - over
            spill += over
            if used > 0:
                rev_h += 1
                rev_max = max(rev_max, used)
    return rev_max, rev_h, (spill / gen_sum if gen_sum else 0.0)


def search_capacity(s: Series, lim: Limits, hi: float = 2_000_000.0,
                    tol: float = 1000.0) -> Result:
    lo = 0.0
    best = 0.0
    while hi - lo > tol:
        mid = (lo + hi) / 2
        rev, hours, spill = simulate(s, mid, lim)
        ok = hours == 0 and spill <= lim.spill_cap
        if ok:
            best = mid
            lo = mid
        else:
            hi = mid
    rev, hours, spill = simulate(s, best, lim)
    lvl = "红" if best <= 0 else ("黄" if rev > 0 else "绿")
    return Result(best, rev, hours, spill, lvl)


def disaggregate(res: Result, counties: Sequence[County]) -> list[dict]:
    """按负荷占比分解，再按装机占比校核；下级不得高于上级。"""
    out = []
    for c in counties:
        share_cap = res.capacity_kw * c.load_share
        cand = min(share_cap, res.capacity_kw * (1 - c.dg_share))
        out.append({"county": c.name, "cap_kw": round(cand, 1),
                    "parent_cap_kw": round(res.capacity_kw, 1),
                    "rule": "min(负荷占比分解, 剩余装机空间)"})
    return out


def build_demo_case() -> tuple[Series, Limits, list[County]]:
    """构造算例：负荷双峰、光伏午间尖峰、风电夜高；参数取常见水平。"""
    load, dg = [], []
    for t in range(HOURS):
        h = t % 24
        # 负荷：夜间 0.55、早峰 0.9、午间 0.8、晚峰 1.0
        base = 0.55 + 0.35 * (1 if 7 <= h <= 9 else 0) + 0.25 * (1 if 18 <= h <= 21 else 0)
        load.append(200_000 * base)                  # 区域最大负荷 200 MW
        # 分布式：光伏 10–14 时尖峰 + 少量风电
        pv = max(0.0, 1 - abs(h - 12) / 4) if 7 <= h <= 17 else 0.0
        wind = 0.35 if (h >= 22 or h <= 5) else 0.15
        dg.append(0.75 * pv + 0.25 * wind)
    counties = [County("A县", 0.35, 0.20), County("B县", 0.28, 0.35), County("C县", 0.37, 0.15)]
    return Series(load, dg), Limits(reverse_limit_kw=0.0, spill_cap=0.05), counties


def main() -> None:
    s, lim, cs = build_demo_case()
    res = search_capacity(s, lim)
    res.counties = disaggregate(res, cs)
    print(f"区域最大可接纳装机 : {res.capacity_kw/1000:.1f} MW")
    print(f"反送最大功率       : {res.reverse_max_kw/1000:.2f} MW  （反送小时 {res.reverse_hours}）")
    print(f"弃电率             : {res.spill_rate*100:.2f}%")
    print(f"分级               : {res.level}")
    for c in res.counties:
        print(f"  {c['county']}: {c['cap_kw']/1000:.1f} MW")
    out = Path(__file__).resolve().parents[1] / "docs" / "system_level_demo.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"capacity_kw": res.capacity_kw,
                               "reverse_max_kw": res.reverse_max_kw,
                               "reverse_hours": res.reverse_hours,
                               "spill_rate": res.spill_rate,
                               "level": res.level,
                               "counties": res.counties,
                               "note": "构造算例：8760 点由典型日扩展，参数取行业常见水平，仅用于方法验证"},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入", out)


if __name__ == "__main__":
    main()
