"""local_dispatch.py — 区域局部调度（设计文档 21.4 / 表 69 数学优化工具边界）。

逐时间片采用一种出力控制方式：Python 直接设值时，不能同时用同一 LoadShape
二次缩放。常规机组通过 Generator，光伏通过 PVSystem，储能通过 Storage 映射；
无功设定须与功率因数、逆变器及本地控制权限一致。

储能时序采用明确的外部调度模式并管理功率、状态和 kWhStored。能源状态仅由
一个模块推进：若由优化模型维护 SOC，则逐时给定一致状态并核对 OpenDSS 输出；
若由 OpenDSS 推进，则读取引擎状态反馈并避免 Python 重复扣减。

单位映射为 MW→kW 乘 1000，Mvar→kvar 乘 1000，MWh→kWh 乘 1000。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.io.schema import as_float, as_str

MW_TO_KW = 1000.0
MVAR_TO_KVAR = 1000.0
MWH_TO_KWH = 1000.0


@dataclass
class Generator:
    """常规机组（设计文档 表 67 generators.csv）。"""

    asset_id: str
    owner_region_id: str = ""
    bus_id: str = ""
    p_min_mw: float = 0.0
    p_max_mw: float = 0.0
    q_min_mvar: float = 0.0
    q_max_mvar: float = 0.0
    ramp_mw_per_h: Optional[float] = None
    cost_model_id: str = ""
    initial_state: str = "on"
    cost_a: float = 0.0
    cost_b: float = 0.0
    cost_c: float = 0.0

    def cost(self, p_mw: float) -> float:
        """二次成本 a·P² + b·P + c（单位在 cost_models.json 记录）。"""
        return self.cost_a * p_mw * p_mw + self.cost_b * p_mw + self.cost_c


@dataclass
class Storage:
    """储能（设计文档 表 67 storage.csv）。"""

    asset_id: str
    owner_region_id: str = ""
    bus_id: str = ""
    p_charge_max_mw: float = 0.0
    p_discharge_max_mw: float = 0.0
    e_min_mwh: float = 0.0
    e_max_mwh: float = 0.0
    e_initial_mwh: float = 0.0
    eta_charge: float = 0.95
    eta_discharge: float = 0.95
    terminal_policy: str = "none"
    degradation_yuan_per_mwh: float = 0.0

    def step_energy(self, e_mwh: float, p_charge_mw: float,
                    p_discharge_mw: float, dt_h: float) -> float:
        """E(t+1) = E(t) + ηc·Pc·Δt − Pd·Δt/ηd（设计文档 表 65）。"""
        return (e_mwh + self.eta_charge * p_charge_mw * dt_h
                - p_discharge_mw * dt_h / max(1e-9, self.eta_discharge))


@dataclass
class FlexLoad:
    """可调负荷（设计文档 表 67 flex_load.csv）。"""

    asset_id: str
    owner_region_id: str = ""
    bus_id: str = ""
    p_reduce_max_mw: float = 0.0
    energy_shift_max_mwh: float = 0.0
    max_duration_h: float = 0.0
    recovery_policy: str = ""
    compensation_yuan_per_mwh: float = 0.0
    permission_ref: str = ""


def load_generators(rows: Sequence[Dict[str, Any]]) -> List[Generator]:
    """读取 generators.csv；发电为正（设计文档 表 67）。"""
    out = []
    for row in rows:
        aid = as_str(row.get("asset_id"))
        if not aid:
            continue
        out.append(Generator(
            asset_id=aid,
            owner_region_id=as_str(row.get("owner_region_id")),
            bus_id=as_str(row.get("bus_id")),
            p_min_mw=as_float(row.get("p_min_mw")) or 0.0,
            p_max_mw=as_float(row.get("p_max_mw")) or 0.0,
            q_min_mvar=as_float(row.get("q_min_mvar")) or 0.0,
            q_max_mvar=as_float(row.get("q_max_mvar")) or 0.0,
            ramp_mw_per_h=as_float(row.get("ramp_mw_per_h")),
            cost_model_id=as_str(row.get("cost_model_id")),
            initial_state=as_str(row.get("initial_state")) or "on",
        ))
    return out


def load_storage(rows: Sequence[Dict[str, Any]]) -> List[Storage]:
    """读取 storage.csv；Pc、Pd 分别为非负变量，效率在 (0,1]。"""
    out = []
    for row in rows:
        aid = as_str(row.get("asset_id"))
        if not aid:
            continue
        ec = as_float(row.get("eta_charge")) or 0.95
        ed = as_float(row.get("eta_discharge")) or 0.95
        if not (0 < ec <= 1):
            raise ValueError(f"storage {aid}: eta_charge={ec} 必须在 (0,1]")
        if not (0 < ed <= 1):
            raise ValueError(f"storage {aid}: eta_discharge={ed} 必须在 (0,1]")
        out.append(Storage(
            asset_id=aid,
            owner_region_id=as_str(row.get("owner_region_id")),
            bus_id=as_str(row.get("bus_id")),
            p_charge_max_mw=as_float(row.get("p_charge_max_mw")) or 0.0,
            p_discharge_max_mw=as_float(row.get("p_discharge_max_mw")) or 0.0,
            e_min_mwh=as_float(row.get("e_min_mwh")) or 0.0,
            e_max_mwh=as_float(row.get("e_max_mwh")) or 0.0,
            e_initial_mwh=as_float(row.get("e_initial_mwh")) or 0.0,
            eta_charge=ec, eta_discharge=ed,
            terminal_policy=as_str(row.get("terminal_policy")) or "none",
        ))
    return out


def load_flex_loads(rows: Sequence[Dict[str, Any]]) -> List[FlexLoad]:
    """读取 flex_load.csv；无授权不得调度（设计文档 表 67）。"""
    out = []
    for row in rows:
        aid = as_str(row.get("asset_id"))
        if not aid:
            continue
        fl = FlexLoad(
            asset_id=aid,
            owner_region_id=as_str(row.get("owner_region_id")),
            bus_id=as_str(row.get("bus_id")),
            p_reduce_max_mw=as_float(row.get("p_reduce_max_mw")) or 0.0,
            energy_shift_max_mwh=as_float(row.get("energy_shift_max_mwh")) or 0.0,
            max_duration_h=as_float(row.get("max_duration_h")) or 0.0,
            recovery_policy=as_str(row.get("recovery_policy")),
            compensation_yuan_per_mwh=as_float(
                row.get("compensation_yuan_per_mwh")) or 0.0,
            permission_ref=as_str(row.get("permission_ref")),
        )
        out.append(fl)
    return out


@dataclass
class DispatchPlan:
    """局部调度方案（设计文档 表 70 dispatch.csv）。"""

    region_id: str
    records: List[Dict[str, Any]] = field(default_factory=list)
    cost_yuan: float = 0.0
    solver_status: str = "ok"
    residual: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def by_asset(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.records:
            out.setdefault(as_str(r.get("asset_id")), []).append(r)
        return out


def solve_local_dispatch(
    region_id: str,
    *, timestamps: Sequence[str],
    dt_h: float,
    generators: Sequence[Generator] = (),
    storage: Sequence[Storage] = (),
    flex_loads: Sequence[FlexLoad] = (),
    pv_available_mw: Optional[Dict[str, Dict[str, float]]] = None,
    load_mw: Optional[Dict[str, float]] = None,
    market: Optional[Dict[str, Dict[str, float]]] = None,
    interface_target_p_mw: float = 0.0,
    multiplier_p: float = 0.0,
    rho: float = 1.0,
    penalty_weight: float = 1.0,
) -> DispatchPlan:
    """求解区域局部调度（设计文档 20.3 局部目标 / 表 68 R04）。

    局部目标 = 本地成本 + Σ[vᵀr + ρ·||r||²/2]。

    连续局部调度原型使用 scipy.optimize.minimize(method=SLSQP)；
    仅局部候选，需记录成功状态和约束残差；不能代替混合整数全局求解。
    """
    plan = DispatchPlan(region_id=region_id)
    if not timestamps:
        plan.solver_status = "empty"
        return plan

    pv_available_mw = pv_available_mw or {}
    load_mw = load_mw or {}
    market = market or {}

    try:
        import numpy as np
        from scipy.optimize import minimize
    except ImportError:
        return _greedy_dispatch(region_id, timestamps, dt_h, generators,
                                storage, flex_loads, pv_available_mw,
                                load_mw, market, interface_target_p_mw,
                                multiplier_p, rho, plan)

    n_t = len(timestamps)
    n_g = len(generators)
    n_s = len(storage)
    # 变量：[Pg (n_t*n_g), Pc (n_t*n_s), Pd (n_t*n_s)]
    n_var = n_t * n_g + 2 * n_t * n_s

    if n_var == 0:
        return _greedy_dispatch(region_id, timestamps, dt_h, generators,
                                storage, flex_loads, pv_available_mw,
                                load_mw, market, interface_target_p_mw,
                                multiplier_p, rho, plan)

    idx_g = lambda t, i: t * n_g + i
    idx_c = lambda t, k: n_t * n_g + t * n_s + k
    idx_d = lambda t, k: n_t * n_g + n_t * n_s + t * n_s + k

    def unpack(x):
        x = np.asarray(x, dtype=float)
        pg = x[:n_t * n_g].reshape(n_t, n_g) if n_g else np.zeros((n_t, 0))
        pc = (x[n_t * n_g:n_t * n_g + n_t * n_s].reshape(n_t, n_s)
              if n_s else np.zeros((n_t, 0)))
        pd = (x[n_t * n_g + n_t * n_s:].reshape(n_t, n_s)
              if n_s else np.zeros((n_t, 0)))
        return pg, pc, pd

    def objective(x):
        pg, pc, pd = unpack(x)
        total = 0.0
        # 本地成本：机组二次成本
        for t in range(n_t):
            for i, g in enumerate(generators):
                total += g.cost(float(pg[t, i])) * dt_h
            for k, st in enumerate(storage):
                total += (pd[t, k] * dt_h * 0.0)  # 储能放电成本在退化项计
                total += (float(pc[t, k] + pd[t, k]) * dt_h
                          * st.degradation_yuan_per_mwh)
            # 外部购售电
            mk = market.get(timestamps[t], {})
            net = _net_import_mw(t, pg, pc, pd, region_id, pv_available_mw,
                                 load_mw, timestamps)
            buy = as_float(mk.get("buy_yuan_per_mwh")) or 0.0
            sell = as_float(mk.get("sell_yuan_per_mwh")) or 0.0
            total += (max(0.0, net) * buy + max(0.0, -net) * sell) * dt_h
            # 协调罚项：只用于算法收敛，不算作真实运行费用（设计文档 20.2）
            resid = net - interface_target_p_mw
            total += penalty_weight * (multiplier_p * resid
                                       + 0.5 * rho * resid * resid) * dt_h
        return total

    def energy_constraints(x):
        """储能能量守恒与上下限（设计文档 表 65）。"""
        pg, pc, pd = unpack(x)
        cons = []
        for k, st in enumerate(storage):
            e = st.e_initial_mwh
            for t in range(n_t):
                e = st.step_energy(e, float(pc[t, k]), float(pd[t, k]), dt_h)
                cons.append(st.e_max_mwh - e)
                cons.append(e - st.e_min_mwh)
        return np.array(cons) if cons else np.array([0.0])

    bounds = []
    for t in range(n_t):
        for g in generators:
            bounds.append((g.p_min_mw, g.p_max_mw))
    for t in range(n_t):
        for st in storage:
            bounds.append((0.0, st.p_charge_max_mw))
    for t in range(n_t):
        for st in storage:
            bounds.append((0.0, st.p_discharge_max_mw))

    x0 = []
    for t in range(n_t):
        for g in generators:
            x0.append(0.5 * (g.p_min_mw + g.p_max_mw))
    for _ in range(2 * n_t * n_s):
        x0.append(0.0)

    res = minimize(objective, np.array(x0), method="SLSQP",
                   bounds=bounds,
                   constraints=[{"type": "ineq", "fun": energy_constraints}]
                   if n_s else [],
                   options={"maxiter": 300, "ftol": 1e-8})

    plan.solver_status = ("ok" if res.success
                          else f"local_solver_incomplete: {res.message}")
    pg, pc, pd = unpack(res.x)

    cost = 0.0
    total_resid = 0.0
    for t in range(n_t):
        ts = timestamps[t]
        for i, g in enumerate(generators):
            p = float(pg[t, i])
            c = g.cost(p) * dt_h
            cost += c
            plan.records.append({
                "region_id": region_id, "asset_id": g.asset_id,
                "timestamp": ts, "p_mw": p, "q_mvar": 0.0,
                "e_mwh": None, "state": g.initial_state, "cost_yuan": c,
            })
        for k, st in enumerate(storage):
            net = float(pd[t, k]) - float(pc[t, k])
            plan.records.append({
                "region_id": region_id, "asset_id": st.asset_id,
                "timestamp": ts, "p_mw": net, "q_mvar": 0.0,
                "e_mwh": None, "state": ("charging" if net < 0
                                          else "discharging" if net > 0
                                          else "idling"),
                "p_charge_mw": float(pc[t, k]),
                "p_discharge_mw": float(pd[t, k]),
            })
        mk = market.get(ts, {})
        net_import = _net_import_mw(t, pg, pc, pd, region_id, pv_available_mw,
                                    load_mw, timestamps)
        buy = as_float(mk.get("buy_yuan_per_mwh")) or 0.0
        sell = as_float(mk.get("sell_yuan_per_mwh")) or 0.0
        cost += (max(0.0, net_import) * buy
                 + max(0.0, -net_import) * sell) * dt_h
        total_resid = max(total_resid,
                          abs(net_import - interface_target_p_mw))

    # 推进 SOC 并写回（能源状态仅由一个模块推进）
    for k, st in enumerate(storage):
        e = st.e_initial_mwh
        for t in range(n_t):
            e = st.step_energy(e, float(pc[t, k]), float(pd[t, k]), dt_h)
            for rec in plan.records:
                if (rec["asset_id"] == st.asset_id
                        and rec["timestamp"] == timestamps[t]):
                    rec["e_mwh"] = e
                    break

    plan.cost_yuan = cost
    plan.residual = {"max_abs_p_mw": total_resid}
    plan.notes.append(
        f"SLSQP 局部候选（success={res.success}）；已记录约束残差，"
        f"不能代替混合整数全局求解")
    return plan


def _net_import_mw(t: int, pg, pc, pd, region_id: str,
                   pv_available_mw: Dict[str, Dict[str, float]],
                   load_mw: Dict[str, float],
                   timestamps: Sequence[str]) -> float:
    """区域净受入（正为受入）。"""
    ts = timestamps[t]
    gen = float(sum(pg[t, :])) if pg.shape[1] else 0.0
    stor = float(sum(pd[t, :] - pc[t, :])) if pd.shape[1] else 0.0
    pv = sum(v.get(ts, 0.0) for v in pv_available_mw.values())
    load = load_mw.get(ts, 0.0)
    return load - gen - stor - pv


def _greedy_dispatch(region_id, timestamps, dt_h, generators, storage,
                     flex_loads, pv_available_mw, load_mw, market,
                     interface_target_p_mw, multiplier_p, rho,
                     plan: DispatchPlan) -> DispatchPlan:
    """无 scipy 时的确定性备用调度（按成本序启机，储能削峰）。"""
    plan.solver_status = "fallback_greedy"
    plan.notes.append("scipy/ numpy 不可用或无可优化变量，使用确定性备用调度")
    cost = 0.0
    for ts in timestamps:
        pv = sum(v.get(ts, 0.0) for v in pv_available_mw.values())
        load = load_mw.get(ts, 0.0)
        need = max(0.0, load - pv)
        for g in generators:
            p = min(g.p_max_mw, max(g.p_min_mw, need))
            need -= p
            c = g.cost(p) * dt_h
            cost += c
            plan.records.append({
                "region_id": region_id, "asset_id": g.asset_id,
                "timestamp": ts, "p_mw": p, "q_mvar": 0.0,
                "e_mwh": None, "state": g.initial_state, "cost_yuan": c})
        for st in storage:
            net = 0.0
            if need > 0:
                net = min(st.p_discharge_max_mw, need)
                need -= net
            plan.records.append({
                "region_id": region_id, "asset_id": st.asset_id,
                "timestamp": ts, "p_mw": net, "q_mvar": 0.0,
                "e_mwh": None,
                "state": "discharging" if net > 0 else "idling"})
    plan.cost_yuan = cost
    plan.residual = {"max_abs_p_mw": abs(need) if timestamps else 0.0}
    return plan


def to_opendss_units(value: float, kind: str) -> float:
    """单位映射（设计文档 21.4）：MW→kW、Mvar→kvar、MWh→kWh 均乘 1000。"""
    k = as_str(kind).lower()
    if k in ("mw", "p_mw"):
        return value * MW_TO_KW
    if k in ("mvar", "q_mvar"):
        return value * MVAR_TO_KVAR
    if k in ("mwh", "e_mwh"):
        return value * MWH_TO_KWH
    return value
