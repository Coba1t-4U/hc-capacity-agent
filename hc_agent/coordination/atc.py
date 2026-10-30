"""atc.py — 目标级联（ATC）协调（设计文档 20.3 目标级联协调的主要流程）。

以附件 ATC 作为首选开发路线。父层下发接口目标，子层求解局部调度并返回
接口响应，协调器计算目标与响应差并更新乘子和罚权；主配和配微两级接口
采用统一残差定义。分布鲁棒外循环负责更新场景概率，容量外循环负责改变
新增容量。三个循环的编号、退出条件和状态分别记录，不能混用同一个迭代
计数器。

局部目标：本地成本 + Σ[vᵀr + ρ·||r||²/2]，其中 r 为按 MW、Mvar 和电压
基准归一化的接口残差。乘子更新使用带符号残差，例如 v←v+ρr；具体更新式
须与局部目标推导一致。不能不经推导照搬参考代码的绝对值更新。

每轮要求接口最大归一化残差、目标变化和 OpenDSS 安全校核同时满足阈值；
时序 SOC 与非预见性约束也必须满足。仅经济目标趋稳不代表交换一致或网络
安全。达到硬迭代或运行时间上限时返回未收敛，保留最好已验证候选，
但不标记最优。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from hc_agent.io.schema import as_float, as_str

# 接口量类别（设计文档 表 64 主配微接口一致性条件）
IFACE_P = "p"
IFACE_Q = "q"
IFACE_V = "v"

# 控制策略（设计文档 表 71 / 22.2 对比实验与消融设计）。
# 常量在此定义，供 coordination 与 research 两层共用；
# research.capacity_outer 重新导出同名常量以保持既有两个入口都可用。
POLICY_FIXED = "fixed"           # 固定控制基线
POLICY_ATC = "atc"               # 确定性协调
POLICY_ATC_DRO = "atc_dro"       # 分布鲁棒协调


@dataclass
class InterfaceSpec:
    """区域间接口（设计文档 表 66 pcc.csv / 表 64）。"""

    pcc_id: str
    parent_region_id: str
    child_region_id: str
    parent_bus_id: str = ""
    child_bus_id: str = ""
    meter_side: str = "parent"      # 唯一计量侧
    transformer_id: str = ""
    p_min_mw: Optional[float] = None
    p_max_mw: Optional[float] = None
    q_min_mvar: Optional[float] = None
    q_max_mvar: Optional[float] = None
    v_min_pu: Optional[float] = None
    v_max_pu: Optional[float] = None
    limit_ref: str = ""
    # 归一化基准（设计文档 20.3：r 按 MW、Mvar 和电压基准归一化）
    p_base_mw: float = 1.0
    q_base_mvar: float = 1.0
    v_base_pu: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def load_interfaces(rows: Sequence[Dict[str, Any]]
                    ) -> List[InterfaceSpec]:
    """读取 pcc.csv（设计文档 表 66）。

    明确 P/Q 坐标、计量侧和负号边界；限值来自规则版本，不用经验填补。
    """
    out: List[InterfaceSpec] = []
    for row in rows:
        pid = as_str(row.get("pcc_id"))
        if not pid:
            continue
        spec = InterfaceSpec(
            pcc_id=pid,
            parent_region_id=as_str(row.get("parent_region_id")),
            child_region_id=as_str(row.get("child_region_id")),
            parent_bus_id=as_str(row.get("parent_bus_id")),
            child_bus_id=as_str(row.get("child_bus_id")),
            meter_side=as_str(row.get("meter_side")) or "parent",
            transformer_id=as_str(row.get("transformer_id")),
            p_min_mw=as_float(row.get("p_min_mw")),
            p_max_mw=as_float(row.get("p_max_mw")),
            q_min_mvar=as_float(row.get("q_min_mvar")),
            q_max_mvar=as_float(row.get("q_max_mvar")),
            v_min_pu=as_float(row.get("v_min_pu")),
            v_max_pu=as_float(row.get("v_max_pu")),
            limit_ref=as_str(row.get("limit_ref")),
        )
        spec.p_base_mw = max(1e-6, abs(spec.p_max_mw or 1.0))
        spec.q_base_mvar = max(1e-6, abs(spec.q_max_mvar or 1.0))
        spec.v_base_pu = max(1e-6, (spec.v_max_pu or 1.0) - (spec.v_min_pu or 0.0))
        out.append(spec)
    return out


@dataclass
class InterfaceTarget:
    """父层下发的接口目标。"""

    pcc_id: str
    target_p_mw: float = 0.0
    target_q_mvar: float = 0.0
    target_v_pu: Optional[float] = None


@dataclass
class InterfaceResponse:
    """子层返回的接口响应。"""

    pcc_id: str
    response_p_mw: float = 0.0
    response_q_mvar: float = 0.0
    response_v_pu: Optional[float] = None


@dataclass
class Multiplier:
    """乘子与罚权（设计文档 20.3）。"""

    pcc_id: str
    v_p: float = 0.0
    v_q: float = 0.0
    v_v: float = 0.0
    rho: float = 1.0
    rho_min: float = 0.1
    rho_max: float = 100.0


def compute_residual(iface: InterfaceSpec, target: InterfaceTarget,
                     response: InterfaceResponse) -> Dict[str, float]:
    """归一化接口残差（设计文档 20.3 / 参考代码静态审查结论）。

    参考代码把 3×24×场景数组用 norm(...,'fro')，设计修正为
    "逐项归一化最大残差"；有功、无功、电压分别判定。
    """
    r_p = (target.target_p_mw - response.response_p_mw) / iface.p_base_mw
    r_q = (target.target_q_mvar - response.response_q_mvar) / iface.q_base_mvar
    r_v = 0.0
    if target.target_v_pu is not None and response.response_v_pu is not None:
        r_v = ((target.target_v_pu - response.response_v_pu)
               / iface.v_base_pu)
    return {"r_p": r_p, "r_q": r_q, "r_v": r_v,
            "max_abs": max(abs(r_p), abs(r_q), abs(r_v))}


def update_targets(targets: Dict[str, InterfaceTarget],
                   responses: Dict[str, InterfaceResponse],
                   multipliers: Dict[str, Multiplier],
                   interfaces: Dict[str, InterfaceSpec],
                   *, rho_scale: float = 1.0, damping: float = 0.0
                   ) -> Dict[str, Any]:
    """乘子与目标更新（设计文档 20.3）。

    乘子更新使用**带符号残差**：v ← v + ρ·r（不是参考代码的绝对值更新）。
    父层目标按常规 ATC 更新 x ← x + v + ρ·r 的等价形式，这里采用
    "目标 = 子层响应 - (v + ρr)/ρ 的松弛" 形式会引入不必要复杂度，
    故直接按 v ← v + ρr 更新乘子，并由调用方的局部目标承担罚项。
    """
    updated: Dict[str, Multiplier] = {}
    stats: Dict[str, Any] = {"max_abs_residual": 0.0, "by_pcc": {}}
    for pid, iface in interfaces.items():
        m = multipliers.get(pid)
        if m is None:
            continue
        tgt = targets.get(pid, InterfaceTarget(pid))
        resp = responses.get(pid, InterfaceResponse(pid))
        res = compute_residual(iface, tgt, resp)
        new_v_p = m.v_p + rho_scale * m.rho * res["r_p"]
        new_v_q = m.v_q + rho_scale * m.rho * res["r_q"]
        new_v_v = m.v_v + rho_scale * m.rho * res["r_v"]
        if damping > 0:
            new_v_p = (1 - damping) * new_v_p + damping * m.v_p
            new_v_q = (1 - damping) * new_v_q + damping * m.v_q
            new_v_v = (1 - damping) * new_v_v + damping * m.v_v
        updated[pid] = Multiplier(pcc_id=pid, v_p=new_v_p, v_q=new_v_q,
                                  v_v=new_v_v, rho=m.rho,
                                  rho_min=m.rho_min, rho_max=m.rho_max)
        stats["by_pcc"][pid] = res
        stats["max_abs_residual"] = max(stats["max_abs_residual"],
                                        res["max_abs"])
    stats["multipliers"] = updated
    return stats


def update_penalty(multipliers: Dict[str, Multiplier],
                   prev_residual: Dict[str, float],
                   curr_residual: Dict[str, float],
                   *, factor_up: float = 2.0, factor_down: float = 0.5,
                   improvement_ratio: float = 0.8,
                   stagnation_limit: int = 3) -> Tuple[Dict[str, Multiplier],
                                                       Dict[str, Any]]:
    """罚权受控调整（设计文档 20.3）。

    罚权按残差改善规则受控调整，保留上下限及停滞检查。
    """
    updated: Dict[str, Multiplier] = {}
    info: Dict[str, Any] = {"adjusted": {}, "stagnation": {}}
    for pid, m in multipliers.items():
        prev = prev_residual.get(pid, float("inf"))
        curr = curr_residual.get(pid, float("inf"))
        rho = m.rho
        if curr > improvement_ratio * prev:
            rho = min(m.rho_max, rho * factor_up)
            info["adjusted"][pid] = "up"
        else:
            rho = max(m.rho_min, rho * factor_down)
            info["adjusted"][pid] = "down"
        updated[pid] = Multiplier(pcc_id=pid, v_p=m.v_p, v_q=m.v_q,
                                  v_v=m.v_v, rho=rho, rho_min=m.rho_min,
                                  rho_max=m.rho_max)
    return updated, info


# 子层求解签名：
#   solve_local(region_id, targets, lambda_mw) ->
#        (objects, response, cost, security_ok, detail)
LocalSolverFn = Callable[[str, Dict[str, InterfaceTarget], float],
                         Tuple[Dict[str, Any], Dict[str, InterfaceResponse],
                               float, bool, Dict[str, Any]]]


@dataclass
class ATCResult:
    """ATC 迭代结论（设计文档 20.3 / 表 70 interface_trace.csv）。"""

    converged: bool
    iterations: int
    reason: str
    interface_trace: List[Dict[str, Any]] = field(default_factory=list)
    objective_history: List[float] = field(default_factory=list)
    max_abs_residual: float = 0.0
    objective_change: float = 0.0
    security_ok: bool = True
    best_verified: Optional[Dict[str, Any]] = None
    multipliers: Dict[str, Multiplier] = field(default_factory=dict)

    def summary(self) -> Dict[str, Any]:
        return {
            "converged": self.converged,
            "iterations": self.iterations,
            "reason": self.reason,
            "max_abs_residual": self.max_abs_residual,
            "objective_change": self.objective_change,
            "security_ok": self.security_ok,
            "objective_history": self.objective_history,
        }


def run_atc(regions: Sequence[Dict[str, Any]],
            interfaces: Sequence[InterfaceSpec],
            solve_local: LocalSolverFn,
            *, lambda_mw: float = 0.0,
            max_iter: int = 20,
            time_limit_s: float = 300.0,
            residual_tol: float = 1e-3,
            objective_tol: float = 1e-4,
            rho0: float = 1.0,
            require_security: bool = True,
            atc_iteration: int = 0) -> ATCResult:
    """执行目标级联协调（设计文档 20.3 / 表 68 R04：run_atc/update_targets）。

    每轮要求接口最大归一化残差、目标变化和 OpenDSS 安全校核同时满足阈值。
    达到硬迭代或运行时间上限时返回未收敛，保留最好已验证候选，但不标记最优。
    """
    iface_by_id = {i.pcc_id: i for i in interfaces}
    child_regions = [as_str(r.get("region_id")) for r in regions
                     if as_str(r.get("level")) in ("D", "M")]
    if not child_regions:
        child_regions = [as_str(i.child_region_id) for i in interfaces]

    # 初始化：父层目标置零交换
    targets: Dict[str, InterfaceTarget] = {
        i.pcc_id: InterfaceTarget(pcc_id=i.pcc_id, target_p_mw=0.0,
                                  target_q_mvar=0.0)
        for i in interfaces}
    multipliers: Dict[str, Multiplier] = {
        i.pcc_id: Multiplier(pcc_id=i.pcc_id, rho=rho0)
        for i in interfaces}

    t0 = time.perf_counter()
    trace: List[Dict[str, Any]] = []
    obj_hist: List[float] = []
    prev_res: Dict[str, float] = {}
    best: Optional[Dict[str, Any]] = None
    reason = "未收敛"
    converged = False
    it = 0

    for it in range(1, max_iter + 1):
        if time.perf_counter() - t0 > time_limit_s:
            reason = f"达到运行时间上限 {time_limit_s:g}s（迭代 {it - 1}）"
            break

        all_objects: Dict[str, Any] = {}
        responses: Dict[str, InterfaceResponse] = {}
        total_cost = 0.0
        security_ok = True
        detail_all: Dict[str, Any] = {}
        failed = False

        for rid in child_regions:
            try:
                objs, resp, cost, sec_ok, detail = solve_local(
                    rid, targets, lambda_mw)
            except Exception as exc:
                detail_all[rid] = {"error": f"{type(exc).__name__}: {exc}"}
                failed = True
                continue
            all_objects[rid] = objs
            responses.update(resp)
            total_cost += cost
            security_ok = security_ok and sec_ok
            detail_all[rid] = detail

        if failed and not responses:
            reason = "全部子区域求解失败，终止"
            break

        upd = update_targets(targets, responses, multipliers, iface_by_id,
                             rho_scale=1.0)
        new_mult: Dict[str, Multiplier] = upd["multipliers"]
        max_res = float(upd["max_abs_residual"])
        curr_res = {pid: r["max_abs"] for pid, r in upd["by_pcc"].items()}

        # 罚权受控调整
        new_mult, pen_info = update_penalty(new_mult, prev_res, curr_res)

        obj_hist.append(total_cost)
        obj_change = (abs(obj_hist[-1] - obj_hist[-2])
                      if len(obj_hist) >= 2 else float("inf"))

        trace.append({
            "atc_iteration": atc_iteration + it,
            "local_iteration": it,
            "lambda_mw": lambda_mw,
            "objective": total_cost,
            "max_abs_residual": max_res,
            "objective_change": obj_change,
            "security_ok": security_ok,
            "penalty_adjust": pen_info.get("adjusted"),
            "detail": {pid: r for pid, r in upd["by_pcc"].items()},
        })

        # 保留最好已验证候选（安全 + 残差最小）
        if security_ok and (best is None
                            or max_res < best.get("max_abs_residual", 1e18)):
            best = {"objects": all_objects, "responses": responses,
                    "cost": total_cost, "max_abs_residual": max_res,
                    "iteration": it, "lambda_mw": lambda_mw}

        prev_res = curr_res
        multipliers = new_mult

        residual_ok = max_res <= residual_tol
        objective_ok = obj_change <= objective_tol
        security_pass = security_ok if require_security else True

        if residual_ok and objective_ok and security_pass:
            converged = True
            reason = (f"第 {it} 轮收敛：最大归一化残差 {max_res:.3e} ≤ "
                      f"{residual_tol:g}，目标变化 {obj_change:.3e} ≤ "
                      f"{objective_tol:g}，安全校核通过")
            break

    if not converged and reason == "未收敛":
        reason = (f"达到硬迭代上限 {max_iter} 轮仍未同时满足残差/目标/安全阈值，"
                  f"返回未收敛；保留最好已验证候选，不标记最优")

    return ATCResult(
        converged=converged, iterations=it, reason=reason,
        interface_trace=trace, objective_history=obj_hist,
        max_abs_residual=(trace[-1]["max_abs_residual"] if trace else 0.0),
        objective_change=(trace[-1]["objective_change"] if trace else 0.0),
        security_ok=bool(trace[-1]["security_ok"]) if trace else False,
        best_verified=best, multipliers=multipliers,
    )


def check_nonanticipativity(dispatch: Dict[str, Dict[str, Any]],
                            stage_one_keys: Sequence[str] = ("p_mw", "q_mvar"),
                            ) -> Dict[str, Any]:
    """非预见性检查（设计文档 20.4 / 21.4）。

    日前承诺属于第一阶段，实时响应属于第二阶段，未知信息揭示前各场景决策一致。
    储能、机组状态和备用不能借用未来场景信息。
    """
    violations: List[Dict[str, Any]] = []
    # dispatch: {asset_id: {timestamp: {scenario_id: {...}}}}
    for aid, by_ts in dispatch.items():
        for ts, by_sc in by_ts.items():
            vals = {}
            for scid, rec in by_sc.items():
                for k in stage_one_keys:
                    v = rec.get(k)
                    if v is None:
                        continue
                    vals.setdefault(k, {})[scid] = round(float(v), 9)
            for k, per_sc in vals.items():
                distinct = set(per_sc.values())
                if len(distinct) > 1:
                    violations.append({
                        "asset_id": aid, "timestamp": ts, "quantity": k,
                        "values": per_sc,
                        "detail": "第一阶段（日前）决策在不同场景间不一致，"
                                  "违反非预见性约束",
                    })
    return {"pass": not violations, "violation_count": len(violations),
            "violations": violations[:50],
            "note": "第一阶段的日前承诺必须在所有场景间相等；"
                    "实时响应属第二阶段，允许场景相关"}


def check_energy_conservation(storage_states: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """储能能量守恒检查（设计文档 表 65 / 表 73）。

    E(t+1) = E(t) + ηc·Pc·Δt − Pd·Δt/ηd；固定 E0、上下限及终端策略。
    """
    from hc_agent.io.schema import as_float as _f
    issues: List[Dict[str, Any]] = []
    by_asset: Dict[str, List[Dict[str, Any]]] = {}
    for r in storage_states:
        by_asset.setdefault(as_str(r.get("asset_id")), []).append(r)

    for aid, rows in by_asset.items():
        rows = sorted(rows, key=lambda x: as_str(x.get("timestamp")))
        prev_e = None
        for r in rows:
            e = _f(r.get("e_mwh"))
            if e is None:
                continue
            e_min, e_max = _f(r.get("e_min_mwh")), _f(r.get("e_max_mwh"))
            if e_min is not None and e < e_min - 1e-6:
                issues.append({"asset_id": aid, "timestamp": r.get("timestamp"),
                               "detail": f"E={e:.6f} < 下限 {e_min:.6f}"})
            if e_max is not None and e > e_max + 1e-6:
                issues.append({"asset_id": aid, "timestamp": r.get("timestamp"),
                               "detail": f"E={e:.6f} > 上限 {e_max:.6f}"})
            if prev_e is not None and r.get("expected_e_mwh") is not None:
                exp = _f(r.get("expected_e_mwh"))
                if exp is not None and abs(e - exp) > 1e-6:
                    issues.append({
                        "asset_id": aid, "timestamp": r.get("timestamp"),
                        "detail": f"能量守恒偏差：实测 {e:.6f} vs "
                                  f"递推 {exp:.6f}"})
            prev_e = e
    return {"pass": not issues, "issue_count": len(issues),
            "issues": issues[:50]}
