"""capacity_outer.py — 容量外层搜索（设计文档 20.2 / 21.5 / 表 68 R03）。

容量研究优先采用外层最大容量、内层最小成本的分层目标（20.2）。

容量向量 k 表示新增设备额定容量；实际注入 r = Φ(k,u,ξ,t) 由控制 u、
不确定性 ξ 和时间 t 决定。沿固定接入方向 d 研究 k = k₀ + λd，最大化 λ；
d 非零、非负，注明分配权重和容量单位。

新增容量目标与运行经济性目标分开：外层最大化 λ，内层在指定 λ 下寻求满足
安全约束的调度并优化成本。单纯最小化购电成本而没有容量变量和容量搜索，
只得到调度方案，不构成承载力计算。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hc_agent.io.schema import as_float, as_str

# 容量边界状态（设计文档 表 70 capacity_frontier.csv）
FEASIBLE = "feasible"
INFEASIBLE = "infeasible"
UNKNOWN = "unknown"

# 控制策略（设计文档 表 71 / 22.2 对比实验与消融设计）。
# 权威定义在 coordination.atc，此处重新导出，使两个入口的引用都成立。
from hc_agent.coordination.atc import (  # noqa: E402  (re-export)
    POLICY_ATC,
    POLICY_ATC_DRO,
    POLICY_FIXED,
)


@dataclass
class CapacityPoint:
    """一个容量试探点（设计文档 表 70 capacity_frontier.csv）。"""

    direction_id: str
    control_policy_id: str = POLICY_FIXED
    lambda_mw: float = 0.0
    status: str = UNKNOWN
    limiting_constraint: str = ""
    cost_yuan: Optional[float] = None
    unknown_reason: str = ""
    region_mw: Dict[str, float] = field(default_factory=dict)
    iterations: Dict[str, int] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "direction_id": self.direction_id,
            "control_policy_id": self.control_policy_id,
            "lambda_mw": self.lambda_mw,
            "status": self.status,
            "limiting_constraint": self.limiting_constraint,
            "cost_yuan": self.cost_yuan,
            "unknown_reason": self.unknown_reason,
            "region_mw": self.region_mw,
        }


@dataclass
class CapacityDirection:
    """接入方向（设计文档 表 66 capacity_direction.csv）。

    权重非负且非零；注明归一化口径，λ 单位 MW。
    """

    direction_id: str
    assets: List[Dict[str, Any]] = field(default_factory=list)
    control_policy_id: str = POLICY_FIXED

    def normalized(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        total = sum(float(a.get("direction_weight") or 0.0)
                    for a in self.assets)
        if total <= 0:
            return {}
        for a in self.assets:
            aid = as_str(a.get("asset_id"))
            w = float(a.get("direction_weight") or 0.0)
            out[aid] = w / total
        return out

    def baseline(self) -> Dict[str, float]:
        return {as_str(a.get("asset_id")):
                float(a.get("baseline_capacity_mw") or 0.0)
                for a in self.assets}


def load_capacity_direction(rows: Sequence[Dict[str, Any]],
                            *, direction_id: str = "D1",
                            control_policy_id: str = POLICY_FIXED
                            ) -> CapacityDirection:
    """读取接入方向；校验权重非负且非零（设计文档 表 66）。"""
    assets = []
    for row in rows:
        aid = as_str(row.get("asset_id"))
        if not aid:
            continue
        w = as_float(row.get("direction_weight"))
        if w is None or w < 0:
            raise ValueError(f"capacity_direction: asset {aid} 的 direction_weight "
                             f"必须非负且非零")
        assets.append({
            "asset_id": aid,
            "baseline_capacity_mw": as_float(row.get("baseline_capacity_mw")) or 0.0,
            "direction_weight": w,
            "control_policy_id": as_str(row.get("control_policy_id"))
            or control_policy_id,
        })
    if not assets or sum(a["direction_weight"] for a in assets) <= 0:
        raise ValueError("capacity_direction: 方向和必须非零")
    return CapacityDirection(direction_id=direction_id, assets=assets,
                             control_policy_id=control_policy_id)


# 试探函数签名：trial(lambda_mw) -> CapacityPoint
OuterTrialFn = Callable[[float], CapacityPoint]


def propose_capacity(direction: CapacityDirection, lambda_mw: float
                     ) -> Dict[str, float]:
    """由 λ 与方向权重给出各资源新增容量（设计文档 20.2）。

    k = k₀ + λ·d，d 为归一化权重。
    """
    weights = direction.normalized()
    base = direction.baseline()
    return {aid: base.get(aid, 0.0) + lambda_mw * w
            for aid, w in weights.items()}


def search_capacity_outer(trial: OuterTrialFn,
                          direction: CapacityDirection,
                          *, lambda_max: float = 50.0,
                          precision_mw: float = 0.5,
                          max_iter: int = 25,
                          outer_iterations: int = 0) -> Dict[str, Any]:
    """容量外层搜索（设计文档 表 68 R03：propose_capacity / update_search_bracket）。

    沿用 17.12 的定界规则：
      - 上界仍可行 -> 只报"至少为上界"，不宣称最大值；
      - 上界不可行 -> 形成可行/不可行括区间并二分；
      - unknown 不得当作不可行上界。
    """
    trace: List[CapacityPoint] = []
    it = 0

    # 下界
    it += 1
    p0 = trial(0.0)
    p0.iterations = {"outer": it, "outer_base": outer_iterations}
    trace.append(p0)
    if p0.status == UNKNOWN:
        return {"status": UNKNOWN, "lambda_mw": None, "trace": trace,
                "reason": "λ=0 试探为 unknown（内层求解失败或 OpenDSS 不收敛），"
                          "不据此证明容量不可行"}
    if p0.status != FEASIBLE:
        return {"status": FEASIBLE, "lambda_mw": 0.0, "trace": trace,
                "limiting_constraint": p0.limiting_constraint,
                "reason": "λ=0 即不可行：当前运行点已违反安全约束"}

    # 上界
    it += 1
    p_hi = trial(lambda_max)
    p_hi.iterations = {"outer": it, "outer_base": outer_iterations}
    trace.append(p_hi)
    if p_hi.status == UNKNOWN:
        return {"status": UNKNOWN, "lambda_mw": None, "trace": trace,
                "reason": f"λ={lambda_max:g} MW 试探为 unknown，不能形成括区间"}
    if p_hi.status == FEASIBLE:
        return {"status": "at_least_upper_bound", "lambda_mw": lambda_max,
                "trace": trace,
                "reason": f"上界 λ={lambda_max:g} MW 仍可行：结论为"
                          f"至少不低于该值，需扩展搜索上界，不得宣称已找到最大值"}

    lo, hi = 0.0, lambda_max
    limiting = p_hi.limiting_constraint
    unknown_points: List[Dict[str, Any]] = []
    while (hi - lo) > precision_mw and it < max_iter:
        mid = 0.5 * (lo + hi)
        it += 1
        pm = trial(mid)
        pm.iterations = {"outer": it, "outer_base": outer_iterations}
        trace.append(pm)
        if pm.status == UNKNOWN:
            unknown_points.append({"lambda_mw": mid,
                                   "reason": pm.unknown_reason
                                   or "内层求解失败"})
            break
        if pm.status == FEASIBLE:
            lo = mid
        else:
            hi = mid
            limiting = pm.limiting_constraint

    return {
        "status": UNKNOWN if unknown_points else FEASIBLE,
        "lambda_mw": lo,
        "lambda_lower_mw": lo,
        "lambda_upper_mw": hi,
        "bracket_gap_mw": hi - lo,
        "limiting_constraint": limiting,
        "unknowns": unknown_points,
        "iterations": it,
        "trace": trace,
        "reason": (f"已验证可行 λ={lo:g} MW、不可行上界 {hi:g} MW，"
                   f"间隔 {hi - lo:g} MW ≤ 精度 {precision_mw:g} MW"
                   if not unknown_points else
                   f"二分点为 unknown，终止并保留已验证区间 [{lo:g}, {hi:g}] MW"),
    }
