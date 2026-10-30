"""capacity_search.py — 开放容量搜索（SK-10）。

设计文档 6.3 可开放容量计算 / 17.12 场景循环与容量搜索实现逻辑。

可开放容量定义为：在指定对象、时间窗、场景、接入分配和控制权限下，
使全部强制约束仍满足的最大新增容量。
概念表达：C_open = max{ΔP ≥ 0 | g_j(x, ΔP, s) ≤ 0, ∀j, s}。

关键规则：
  1. 二分搜索仅适用于已验证可行性随增量容量单调的区间及固定接入分配。
  2. 基线通过后应先试探上界：上界仍可行只能输出"至少为上界"或按规则扩展
     搜索，不能宣称找到最大值；上界不可行才形成可行/不可行括区间。
  3. 终止时保留可行下界、不可行上界及间隔。
  4. 电压/无功控制、调压分接动作、储能策略可能破坏单调性，此时使用经批准
     步长的离散全域扫描。
  5. 超时、不收敛或短路模型缺失是 unknown，不可等同于电气不可行，
     更不可被二分程序当作不可行上界。
  6. 各点独立最大值不能直接相加。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import STATUS_FAIL, STATUS_PASS, STATUS_UNKNOWN
from hc_agent.io.schema import as_bool, as_float, as_int

# 搜索状态（设计文档 表 56 search_status / capacity_frontier.csv）
SEARCH_OK = "ok"
SEARCH_AT_LEAST_UPPER = "at_least_upper_bound"
SEARCH_UNKNOWN = "unknown"
SEARCH_BLOCKED = "blocked"


@dataclass
class TrialResult:
    """一次试探的结果（设计文档 表 56 search_trace.csv）。"""

    iteration: int
    delta_kw: float
    status: str            # feasible / infeasible / unknown
    binding_constraint: str = ""
    scenario_id: str = ""
    timestamp: str = ""
    constraint_status: Dict[str, int] = field(default_factory=dict)
    result_hash: str = ""
    engine_run_ref: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration, "delta_kw": self.delta_kw,
            "status": self.status, "binding_constraint": self.binding_constraint,
            "scenario_id": self.scenario_id, "timestamp": self.timestamp,
            "pass_count": self.constraint_status.get(STATUS_PASS, 0),
            "fail_count": self.constraint_status.get(STATUS_FAIL, 0),
            "unknown_count": self.constraint_status.get(STATUS_UNKNOWN, 0),
            "engine_run_ref": self.engine_run_ref,
            "result_hash": self.result_hash,
        }


@dataclass
class SearchResult:
    """搜索结论（设计文档 6.3-4 / 17.12）。"""

    capacity_physical_kw: Optional[float]
    search_status: str
    feasible_lower_kw: Optional[float] = None
    infeasible_upper_kw: Optional[float] = None
    bracket_gap_kw: Optional[float] = None
    binding_constraint: str = ""
    binding_detail: Dict[str, Any] = field(default_factory=dict)
    method: str = ""
    iterations: int = 0
    trace: List[TrialResult] = field(default_factory=list)
    unknowns: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        return {
            "capacity_physical_kw": self.capacity_physical_kw,
            "search_status": self.search_status,
            "feasible_lower_kw": self.feasible_lower_kw,
            "infeasible_upper_kw": self.infeasible_upper_kw,
            "bracket_gap_kw": self.bracket_gap_kw,
            "binding_constraint": self.binding_constraint,
            "method": self.method,
            "iterations": self.iterations,
        }


# 试探函数签名：feasible(delta_kw) -> TrialResult
FeasibilityFn = Callable[[float, int], TrialResult]


def search_capacity(feasible: FeasibilityFn, search_cfg: Dict[str, Any],
                    *, monotonicity_validated: Optional[bool] = None,
                    allocation_mode: str = "fixed_weights",
                    binding_extractor: Optional[
                        Callable[[TrialResult], str]] = None) -> SearchResult:
    """执行容量搜索（设计文档 17.12 search_bracket_and_bisect）。

    仅当可行性随增量容量单调已验证且采用固定接入分配时执行二分；
    否则使用离散网格扫描。
    """
    lower = as_float(search_cfg.get("lower_kw")) or 0.0
    upper = as_float(search_cfg.get("upper_kw"))
    precision = as_float(search_cfg.get("precision_kw")) or 1.0
    max_iter = as_int(search_cfg.get("max_iter")) or 20
    step = as_float(search_cfg.get("step_kw")) or precision

    if upper is None or upper <= lower:
        return SearchResult(
            capacity_physical_kw=None, search_status=SEARCH_BLOCKED,
            notes=["search.upper_kw 缺失或不大于 lower_kw，无法定界"],
        )

    use_bisect = (monotonicity_validated is True
                  and allocation_mode == "fixed_weights")
    if use_bisect:
        return _bisection_search(feasible, lower, upper, precision, max_iter,
                                 binding_extractor)
    return _discrete_scan(feasible, lower, upper, step, max_iter,
                          binding_extractor)


def _bisection_search(feasible: FeasibilityFn, lower: float, upper: float,
                      precision: float, max_iter: int,
                      binding_extractor) -> SearchResult:
    """括区间 + 二分（设计文档 17.12 伪代码）。"""
    res = SearchResult(capacity_physical_kw=None, search_status=SEARCH_UNKNOWN,
                       method="bracket_and_bisect")
    it = 0

    # 步骤 1：试探下界（应可行）
    it += 1
    lo_trial = feasible(lower, it)
    res.trace.append(lo_trial)
    if lo_trial.status == "unknown":
        res.unknowns.append({"delta_kw": lower, "reason": "起点试探为 unknown"})
        res.notes.append("下界试探为 unknown，无法定界（不收敛/超时不得视为不可行）")
        res.iterations = it
        return res
    if lo_trial.status != "feasible":
        res.capacity_physical_kw = 0.0
        res.search_status = SEARCH_OK
        res.feasible_lower_kw = 0.0
        res.binding_constraint = _bind(lo_trial, binding_extractor)
        res.binding_detail = lo_trial.detail
        res.notes.append(
            f"下界 {lower:g} kW 已不可行，物理可开放容量为 0（存量问题优先）")
        res.iterations = it
        return res

    # 步骤 2：试探上界（设计文档 17.12）
    it += 1
    hi_trial = feasible(upper, it)
    res.trace.append(hi_trial)
    if hi_trial.status == "unknown":
        res.unknowns.append({"delta_kw": upper, "reason": "上界试探为 unknown"})
        res.notes.append("上界试探为 unknown，不能形成括区间")
        res.feasible_lower_kw = lower
        res.iterations = it
        return res
    if hi_trial.status == "feasible":
        # 上界仍可行只能输出"至少为上界"，不能宣称找到最大值
        res.capacity_physical_kw = upper
        res.search_status = SEARCH_AT_LEAST_UPPER
        res.feasible_lower_kw = upper
        res.method = "bracket_and_bisect"
        res.notes.append(
            f"上界 {upper:g} kW 仍可行：结论为至少不低于 {upper:g} kW；"
            f"按设计 17.12 不得宣称已找到最大值，需按规则扩展搜索上界")
        res.iterations = it
        return res

    res.binding_constraint = _bind(hi_trial, binding_extractor)
    res.binding_detail = hi_trial.detail
    lo_val, hi_val = lower, upper

    # 步骤 3：二分
    while (hi_val - lo_val) > precision and it < max_iter:
        mid = 0.5 * (lo_val + hi_val)
        it += 1
        t = feasible(mid, it)
        res.trace.append(t)
        if t.status == "unknown":
            # unknown 不可被当作不可行上界（17.12）
            res.unknowns.append({"delta_kw": mid,
                                 "reason": "二分点为 unknown，终止并保留区间"})
            res.notes.append(
                f"二分点 {mid:g} kW 为 unknown；按设计 17.12 不得当作不可行上界，"
                f"搜索终止并保留已验证区间")
            break
        if t.status == "feasible":
            lo_val = mid
        else:
            hi_val = mid
            res.binding_constraint = _bind(t, binding_extractor)
            res.binding_detail = t.detail

    res.feasible_lower_kw = lo_val
    res.infeasible_upper_kw = hi_val
    res.bracket_gap_kw = hi_val - lo_val
    res.capacity_physical_kw = lo_val  # 保守：取已验证可行的下界
    res.search_status = SEARCH_UNKNOWN if res.unknowns else SEARCH_OK
    if not res.unknowns:
        res.notes.append(
            f"已验证可行下界 {lo_val:g} kW，不可行上界 {hi_val:g} kW，"
            f"间隔 {res.bracket_gap_kw:g} kW ≤ 精度 {precision:g} kW")
    res.iterations = it
    return res


def _discrete_scan(feasible: FeasibilityFn, lower: float, upper: float,
                   step: float, max_iter: int,
                   binding_extractor) -> SearchResult:
    """离散网格扫描（设计文档 17.12：非单调时使用经批准步长的离散扫描）。

    报告仅保证扫描分辨率内的边界，不宣称连续全局最优。
    """
    res = SearchResult(capacity_physical_kw=None, search_status=SEARCH_UNKNOWN,
                       method="discrete_scan")
    if step <= 0:
        step = max(1.0, (upper - lower) / 20.0)

    n_points = int(math.floor((upper - lower) / step)) + 1
    n_points = max(2, min(n_points, max_iter))

    last_feasible: Optional[float] = None
    first_fail: Optional[float] = None
    unknown_points: List[float] = []
    it = 0

    for k in range(n_points):
        delta = lower + k * (upper - lower) / max(1, n_points - 1)
        it += 1
        t = feasible(delta, it)
        res.trace.append(t)
        if t.status == "feasible":
            last_feasible = delta
        elif t.status == "fail" or t.status == "infeasible":
            if first_fail is None:
                first_fail = delta
                res.binding_constraint = _bind(t, binding_extractor)
                res.binding_detail = t.detail
            break
        else:  # unknown
            unknown_points.append(delta)

    res.iterations = it
    res.feasible_lower_kw = last_feasible
    res.infeasible_upper_kw = first_fail
    if first_fail is not None and last_feasible is not None:
        res.bracket_gap_kw = first_fail - last_feasible
    if unknown_points:
        res.unknowns.extend({"delta_kw": x, "reason": "扫描点为 unknown"}
                            for x in unknown_points)
    res.capacity_physical_kw = last_feasible
    if last_feasible is None:
        if unknown_points and first_fail is None:
            res.search_status = SEARCH_UNKNOWN
            res.notes.append("扫描点全部为 unknown，无法给出容量结论")
        else:
            res.search_status = SEARCH_OK
            res.capacity_physical_kw = lower
            res.notes.append("扫描范围内首个点即不可行，物理容量取搜索下界")
    elif first_fail is None:
        res.search_status = SEARCH_AT_LEAST_UPPER
        res.notes.append(
            f"扫描至上界 {upper:g} kW 仍全部可行；结论为至少不低于 "
            f"{last_feasible:g} kW，仅保证扫描分辨率 {step:g} kW 内的边界")
    else:
        res.search_status = SEARCH_UNKNOWN if unknown_points else SEARCH_OK
        res.notes.append(
            f"离散扫描（步长 {step:g} kW）：可行 {last_feasible:g} kW，"
            f"不可行 {first_fail:g} kW；仅保证扫描分辨率内的边界，"
            f"不宣称连续全局最优")
    return res


def _bind(trial: TrialResult, extractor) -> str:
    if extractor is not None:
        try:
            return extractor(trial)
        except Exception:
            pass
    return trial.binding_constraint


def check_monotonicity(feasible: FeasibilityFn, search_cfg: Dict[str, Any],
                       *, samples: int = 6) -> Dict[str, Any]:
    """单调性验证（设计文档 17.12 monotonicity_is_validated）。

    二分搜索仅适用于已验证可行性随增量容量单调的区间及固定接入分配。
    电压/无功控制、调压分接动作、储能策略可能破坏单调性。
    """
    lower = as_float(search_cfg.get("lower_kw")) or 0.0
    upper = as_float(search_cfg.get("upper_kw")) or 0.0
    if upper <= lower:
        return {"validated": False, "reason": "搜索区间无效"}

    points = [lower + (upper - lower) * i / max(1, samples - 1)
              for i in range(samples)]
    pattern: List[str] = []
    for i, d in enumerate(points):
        t = feasible(d, -(i + 1))  # 负迭代号标记为验证性试探
        pattern.append(t.status)

    # 单调：once infeasible, never feasible again
    seen_fail = False
    monotone = True
    for st in pattern:
        if st in ("fail", "infeasible"):
            seen_fail = True
        elif st == "feasible" and seen_fail:
            monotone = False
            break
    return {
        "validated": monotone and "unknown" not in pattern,
        "pattern": pattern,
        "points_kw": points,
        "reason": ("可行性随增量容量单调" if monotone
                   else "检测到非单调响应，须改用离散扫描"),
    }
