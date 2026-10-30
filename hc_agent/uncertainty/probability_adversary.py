"""probability_adversary.py — 分布鲁棒概率子问题（设计文档 20.4）。

保留联合场景 ω 与经验概率 p⁰，定义概率集合
  P = {p ≥ 0，Σp = 1，||p − p⁰||₁ ≤ θ₁，||p − p⁰||∞ ≤ θ∞}。
概率子问题在固定候选策略下最大化 Σ p_ω C_ω；C 必须是同一经济口径的逐场景
成本向量。L1 范数通过辅助变量线性化，可调用 scipy.optimize.linprog。

概率半径需由样本与置信方法确定，不能未经验证沿用参考代码的 N=100、α=0.99。
最坏期望成本不等价于概率安全保证。明确哪些约束在所有训练场景硬满足，
哪些采用经过批准的机会约束；另用独立样本评估越限。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.io.schema import as_float, as_str


@dataclass
class ProbabilitySet:
    """概率集合参数（设计文档 20.4）。"""

    p0: List[float]
    radius_l1: float = 0.0
    radius_linf: float = 0.0
    scenario_ids: List[str] = field(default_factory=list)

    def validate(self, *, tol: float = 1e-9) -> List[str]:
        issues: List[str] = []
        if any(p < -tol for p in self.p0):
            issues.append("经验概率存在负值（必须 p ≥ 0）")
        s = sum(self.p0)
        if abs(s - 1.0) > 1e-6:
            issues.append(f"经验概率之和 {s:.10f} ≠ 1")
        if self.radius_l1 < 0 or self.radius_linf < 0:
            issues.append("概率半径必须非负")
        if self.radius_linf > self.radius_l1 + tol:
            issues.append(f"L∞ 半径 {self.radius_linf} 不应大于 L1 半径 "
                          f"{self.radius_l1}")
        if self.scenario_ids and len(self.scenario_ids) != len(self.p0):
            issues.append("scenario_ids 数量与概率数量不一致")
        return issues


@dataclass
class WorstCaseResult:
    """最坏概率结论（设计文档 表 70 probability_trace.csv）。"""

    p_worst: List[float]
    worst_expected_cost: float
    nominal_expected_cost: float
    solver_status: str
    method: str
    radius_l1: float
    radius_linf: float
    consistency: Dict[str, Any] = field(default_factory=dict)

    def to_row(self, prob_iteration: int,
               scenario_ids: Sequence[str]) -> List[Dict[str, Any]]:
        rows = []
        for i, sid in enumerate(scenario_ids):
            rows.append({
                "prob_iteration": prob_iteration,
                "scenario_id": sid,
                "p0": self.consistency.get("p0", [None] * len(scenario_ids))[i]
                if i < len(self.consistency.get("p0", [])) else None,
                "p_worst": self.p_worst[i] if i < len(self.p_worst) else None,
                "radius_l1": self.radius_l1,
                "radius_linf": self.radius_linf,
                "solver_status": self.solver_status,
            })
        return rows


def solve_worst_probability(costs: Sequence[float],
                            prob_set: ProbabilitySet,
                            *, method: str = "auto") -> WorstCaseResult:
    """求解最坏期望成本概率（设计文档 20.4 / 表 69）。

    max_p Σ p_ω C_ω
    s.t. Σ p = 1, p ≥ 0, ||p − p⁰||₁ ≤ θ₁, ||p − p⁰||∞ ≤ θ∞

    L1 范数通过辅助变量线性化：
      |p_i - p0_i| ≤ t_i,  Σ t_i ≤ θ₁
    即 p_i - p0_i ≤ t_i 且 p0_i - p_i ≤ t_i，目标 max Σ p_i C_i。
    """
    n = len(costs)
    issues = prob_set.validate()
    if issues:
        return WorstCaseResult(
            p_worst=list(prob_set.p0), worst_expected_cost=float("nan"),
            nominal_expected_cost=_expected(prob_set.p0, costs),
            solver_status="invalid_input", method=method,
            radius_l1=prob_set.radius_l1, radius_linf=prob_set.radius_linf,
            consistency={"issues": issues, "p0": list(prob_set.p0)})

    if n == 0:
        return WorstCaseResult([], 0.0, 0.0, "empty", method,
                               prob_set.radius_l1, prob_set.radius_linf,
                               {"p0": []})

    p0 = list(prob_set.p0)
    nominal = _expected(p0, costs)

    # 变量顺序：[p_0..p_{n-1}, t_0..t_{n-1}]
    c = [-float(ci) for ci in costs] + [0.0] * n      # linprog 求最小
    a_eq = [[1.0] * n + [0.0] * n]
    b_eq = [1.0]
    a_ub: List[List[float]] = []
    b_ub: List[float] = []
    # Σ t_i ≤ θ₁
    a_ub.append([0.0] * n + [1.0] * n)
    b_ub.append(float(prob_set.radius_l1))
    # p_i − t_i ≤ p0_i  ；  −p_i − t_i ≤ −p0_i + θ∞
    for i in range(n):
        row = [0.0] * (2 * n)
        row[i] = 1.0
        row[n + i] = -1.0
        a_ub.append(row)
        b_ub.append(p0[i])
        row2 = [0.0] * (2 * n)
        row2[i] = -1.0
        row2[n + i] = -1.0
        a_ub.append(row2)
        b_ub.append(-p0[i])
    # L∞ 半径：显式约束 |p_i − p0_i| ≤ θ∞
    if prob_set.radius_linf > 0:
        for i in range(n):
            row = [0.0] * (2 * n)
            row[i] = 1.0
            a_ub.append(row)
            b_ub.append(p0[i] + prob_set.radius_linf)
            row2 = [0.0] * (2 * n)
            row2[i] = -1.0
            a_ub.append(row2)
            b_ub.append(-p0[i] + prob_set.radius_linf)

    bounds = [(0.0, None)] * n + [(0.0, None)] * n
    status = "ok"
    p_worst: List[float]
    used = "linprog"

    try:
        from scipy.optimize import linprog
        res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                      bounds=bounds, method="highs")
        if not res.success or res.x is None:
            status = f"solver_failed: {res.message}"
            p_worst = _analytic_worst(p0, costs, prob_set)
            used = "analytic_fallback"
        else:
            p_worst = [max(0.0, float(x)) for x in res.x[:n]]
            s = sum(p_worst)
            if s > 0:
                p_worst = [x / s for x in p_worst]
    except ImportError:
        p_worst = _analytic_worst(p0, costs, prob_set)
        used = "analytic_fallback"
        status = "scipy_unavailable"

    # 复算校验（表 69：检查 success、非负、概率和、集合半径及目标复算）
    chk = _verify_solution(p_worst, p0, costs, prob_set)
    if not chk["valid"] and status == "ok":
        status = "verification_failed"
        chk["fallback_used"] = True

    return WorstCaseResult(
        p_worst=p_worst,
        worst_expected_cost=_expected(p_worst, costs),
        nominal_expected_cost=nominal,
        solver_status=status, method=used,
        radius_l1=prob_set.radius_l1, radius_linf=prob_set.radius_linf,
        consistency={**chk, "p0": p0})


def _analytic_worst(p0: Sequence[float], costs: Sequence[float],
                    prob_set: ProbabilitySet) -> List[float]:
    """闭式近似最坏概率（无 LP 求解器时的降级路径）。

    最坏分布会把概率质量向高成本场景集中，受 L1 与 L∞ 半径限制。
    构造方式：按成本降序将 θ₁ 的质量从低成本场景按 L∞ 上限搬到高成本场景。
    """
    n = len(costs)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: costs[i], reverse=True)
    p = list(p0)
    remaining = float(prob_set.radius_l1)
    linf = prob_set.radius_linf if prob_set.radius_linf > 0 else \
        (prob_set.radius_l1 / max(1, n) if prob_set.radius_l1 else 0.0)

    donors = [i for i in order[::-1]]
    receivers = list(order)
    di = ri = 0
    guard = 0
    while remaining > 1e-12 and guard < 10 * n:
        guard += 1
        while di < len(donors) and p[donors[di]] <= 1e-12:
            di += 1
        while ri < len(receivers) and (p[receivers[ri]] - p0[receivers[ri]]
                                       >= linf - 1e-12):
            ri += 1
        if di >= len(donors) or ri >= len(receivers):
            break
        d, r = donors[di], receivers[ri]
        if d == r:
            di += 1
            continue
        move = min(remaining, p[d],
                   linf - (p[r] - p0[r]) if linf > 0 else remaining)
        if move <= 1e-12:
            di += 1
            continue
        p[d] -= move
        p[r] += move
        remaining -= move
    s = sum(p)
    return [x / s for x in p] if s > 0 else list(p0)


def _expected(p: Sequence[float], costs: Sequence[float]) -> float:
    return float(sum(pi * ci for pi, ci in zip(p, costs)))


def _verify_solution(p: Sequence[float], p0: Sequence[float],
                     costs: Sequence[float],
                     prob_set: ProbabilitySet) -> Dict[str, Any]:
    """解的正确性校验（设计文档 表 69）。"""
    l1 = sum(abs(a - b) for a, b in zip(p, p0))
    linf = max((abs(a - b) for a, b in zip(p, p0)), default=0.0)
    s = sum(p)
    out = {
        "valid": True,
        "prob_sum": s,
        "radius_l1_used": l1,
        "radius_linf_used": linf,
        "nonnegative": all(x >= -1e-9 for x in p),
        "l1_within": l1 <= prob_set.radius_l1 + 1e-6,
        "linf_within": linf <= prob_set.radius_linf + 1e-6
        if prob_set.radius_linf > 0 else True,
        "expected_cost": _expected(p, costs),
    }
    out["valid"] = (bool(out["nonnegative"]) and abs(s - 1.0) < 1e-6
                    and bool(out["l1_within"]) and bool(out["linf_within"]))
    return out


def load_probability_set(rows: Sequence[Dict[str, Any]],
                         *, radius_l1: Optional[float] = None,
                         radius_linf: Optional[float] = None,
                         theta_from_samples: bool = True
                         ) -> ProbabilitySet:
    """读取 scenario_probability.csv（设计文档 表 66）。

    概率非负且和为 1；样本量有来源。
    概率半径需由样本与置信方法确定，不能未经验证沿用参考代码的
    N=100、α=0.99（设计文档 20.4）。
    """
    sids: List[str] = []
    probs: List[float] = []
    samples: List[int] = []
    for row in rows:
        sid = as_str(row.get("scenario_id"))
        p = as_float(row.get("probability"))
        if not sid or p is None:
            continue
        sids.append(sid)
        probs.append(max(0.0, p))
        samples.append(int(as_float(row.get("source_sample_count")) or 0))

    total = sum(probs)
    if total > 0 and abs(total - 1.0) > 1e-6:
        probs = [p / total for p in probs]

    n_samples = min(samples) if samples else 0
    if radius_l1 is None:
        # 由样本量给出保守半径（若样本量未知则取 0，即退化为经验分布）
        radius_l1 = (2.0 * math.sqrt(2.0 / n_samples)) if n_samples else 0.0
    if radius_linf is None:
        radius_linf = (radius_l1 / max(1, len(probs))) if probs else 0.0

    return ProbabilitySet(p0=probs, radius_l1=float(radius_l1),
                          radius_linf=float(radius_linf),
                          scenario_ids=sids)


def update_probability_case(prob_set: ProbabilitySet,
                            worst: WorstCaseResult,
                            *, damping: float = 0.0) -> ProbabilitySet:
    """概率外循环更新（设计文档 表 68 R06：update_probability_case）。

    把对手找到的最坏概率作为下一轮的场景概率（可加阻尼以稳定迭代）。
    三个循环的编号、退出条件和状态分别记录，不能混用同一个迭代计数器。
    """
    if not worst.p_worst or len(worst.p_worst) != len(prob_set.p0):
        return prob_set
    if damping > 0:
        new_p = [(1 - damping) * a + damping * b
                 for a, b in zip(prob_set.p0, worst.p_worst)]
    else:
        new_p = list(worst.p_worst)
    s = sum(new_p)
    if s > 0:
        new_p = [x / s for x in new_p]
    return ProbabilitySet(p0=new_p, radius_l1=prob_set.radius_l1,
                          radius_linf=prob_set.radius_linf,
                          scenario_ids=list(prob_set.scenario_ids))


def evaluate_holdout(p_used: Sequence[float], costs: Sequence[float],
                     violations: Sequence[bool]) -> Dict[str, Any]:
    """独立样本评估（设计文档 22.2 / 表 73）。

    样本外越限率仅说明该测试集表现，不替代一般可靠性保证。
    """
    n = len(costs)
    if n == 0:
        return {"sample_count": 0, "note": "无独立样本"}
    exp_cost = _expected(p_used, costs)
    viol_rate = (sum(1 for v in violations if v) / n) if violations else 0.0
    return {
        "sample_count": n,
        "expected_cost": exp_cost,
        "violation_count": sum(1 for v in violations if v) if violations else 0,
        "violation_rate": viol_rate,
        "note": "样本外越限率仅说明该测试集表现，不替代一般可靠性保证；"
                "最坏期望成本不等价于概率安全保证",
    }
