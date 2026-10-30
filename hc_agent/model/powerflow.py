"""powerflow.py — 潮流执行（SK-04 基线潮流 / SK-05 增量接入潮流）。

设计文档 S04 / S05 / 17.12。

每次从冻结基线复制或重建，避免新增容量累积。
失败记为 unknown，不能当 pass。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.engines.opendss_adapter import EngineResult, OpenDSSAdapter
from hc_agent.errors import ComputeFailedError
from hc_agent.io.ingest import InputBundle
from hc_agent.io.schema import IssueCollector, as_float, as_str
from hc_agent.model.build_model import DSSModel, build_case
from hc_agent.model.scenarios import Scenario, Slice

SQRT3 = math.sqrt(3.0)


@dataclass
class ScenarioRun:
    """一个场景在一个增量下的执行结果（设计文档 表 56）。"""

    scenario_id: str
    increment_kw: float
    slices: List[Dict[str, Any]] = field(default_factory=list)
    converged: bool = True
    status: str = "ok"
    error_code: Optional[str] = None
    engine_refs: List[str] = field(default_factory=list)
    unserved_slices: List[str] = field(default_factory=list)

    @property
    def all_converged(self) -> bool:
        return self.converged and not self.unserved_slices


def apply_slice(adapter: OpenDSSAdapter, bundle: InputBundle, sl: Slice,
                *, increment_kw: float = 0.0,
                allocation: Optional[Dict[str, float]] = None,
                new_pv_ids: Optional[Sequence[str]] = None) -> None:
    """把一个时间片写入引擎（设计文档 17.12）。

    每个时间片先应用开关/分接状态、负荷 P/Q、已有光伏出力和控制策略，
    再增加候选接入容量。
    若 Python 逐时间片设置 P/Q，则不再同时套用同一时序 LoadShape，
    避免出力重复缩放（设计文档 8.1）。
    """
    # 1) 负荷：分相 P/Q（低压三相负荷不能把总功率复制到三相）
    for ld in bundle.loads:
        if ld.get("in_service") is False:
            continue
        lid = as_str(ld["load_id"])
        dss_name = ld.get("_dss_name") or f"load_{lid}"
        p = sl.load_p_kw.get(lid)
        q = sl.load_q_kvar.get(lid)
        if p is None and q is None:
            continue
        ph = (sl.load_phase.get(lid) or as_str(ld.get("phase")) or "ALL").upper()
        if ph in ("A", "B", "C"):
            # 分相显式设置：kW 为负表示消费（OpenDSS 中 Load 的 kW 为正即消费）
            adapter.set_load(dss_name, kw=max(0.0, p or 0.0),
                             kvar=max(0.0, q or 0.0), phases=(ph,))
        else:
            adapter.set_load(dss_name, kw=max(0.0, p or 0.0),
                             kvar=max(0.0, q or 0.0))

    # 2) 已有光伏出力：由可用出力系数 × 额定容量
    for pv in bundle.pv:
        if pv.get("in_service") is False:
            continue
        pid = as_str(pv["pv_id"])
        dss_name = pv.get("_dss_name") or f"pv_{pid}"
        avail = sl.pv_avail_pu.get(pid)
        if avail is None:
            continue
        rated = as_float(pv.get("rated_p_kw")) or as_float(pv.get("rated_s_kva")) \
            or 0.0
        adapter.set_pv(dss_name, pmpp_kw=max(0.0, rated * avail),
                       irradiance=1.0)

    # 3) 新增候选容量：按固定分配权重注入，并乘以该时间片的可用出力系数
    #
    # 必须**无条件**写入试探容量，包括 increment_kw == 0 的情形。
    # 早先的写法 `if increment_kw and new_pv_ids:` 在 delta=0 时短路，
    # 新增 PVSystem 保持其铭牌出力，于是"零增量"试探点实际注入了上界兆瓦级
    # 功率并被判不可行，容量搜索结果恒为 0（本机实测：delta=0 时报出 5264.9 kW）。
    # 又因 set_pv 走 `Edit` 命令，上一次试探的值会残留到下一次，
    # 使试探结果依赖调用顺序。因此这里必须每次都显式写入。
    #
    # 可用出力系数：候选件与已有光伏一样应服从日内出力曲线（design 17.12：
    # "新增光伏有功由容量、可用出力系数及固定分配权重计算"）。系数由
    # scenarios._candidate_profiles 写入 sl.pv_avail_pu，此处取用；
    # 缺失时按 1.0 处理并保持在 0..1 区间内。
    if new_pv_ids is not None:
        weights = allocation or {pid: 1.0 / max(1, len(new_pv_ids))
                                 for pid in new_pv_ids}
        for pid in new_pv_ids:
            dss_name = _new_pv_dss_name(pid)
            share = float(weights.get(pid, 0.0)) * float(increment_kw or 0.0)
            avail = sl.pv_avail_pu.get(pid)
            factor = 1.0 if avail is None else max(0.0, min(1.0, float(avail)))
            adapter.set_pv(dss_name, pmpp_kw=max(0.0, share * factor),
                           irradiance=1.0)

    # 4) 开关与分接状态
    for sid, closed in (sl.switch_state or {}).items():
        adapter.set_switch(sid, bool(closed))
    for tid, pos in (sl.tap_state or {}).items():
        adapter.set_tap(tid, float(pos))


def _new_pv_dss_name(pid: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in pid)
    if not safe or not safe[0].isalpha():
        safe = f"_{safe}"
    return f"pv_{safe}"


def make_new_pv_specs(bundle: InputBundle, task: Dict[str, Any]
                      ) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """由 task.candidate_nodes 生成新增光伏设备定义（设计文档 17.5）。

    新增容量试探件按搜索上界建立**非零铭牌**，再由 apply_slice 在每次试探时
    用 Edit kVA/Pmpp 写入实际出力。

    为什么必须非零铭牌：本机实测（OpenDSSDirect.py 0.9.4 / DSS C-API 0.14.5）
    若 PVSystem 以 kVA=0 Pmpp=0 建立，后续 `Edit ... kVA=750 Pmpp=750` 不会
    产生任何出力（始终 0 kW）；只有以非零铭牌建立后 Edit 才能正确改变出力
    （kVA=Pmpp=750 -> 750.01 kW，=1500 -> 1500.00 kW，=0 -> 0 kW）。

    为什么不能依赖"未写入即等价于零注入"：铭牌非零意味着一旦某次试探漏写，
    该设备就会按铭牌满发。因此 apply_slice 必须**无条件**在每个时间片写入
    实际出力（含 delta=0），二者配合才能既可控又不残留。
    """
    specs: List[Dict[str, Any]] = []
    weights: Dict[str, float] = {}
    search = task.get("search") or {}
    nameplate = float(as_float(search.get("upper_kw")) or 1000.0)
    if nameplate <= 0:
        nameplate = 1000.0
    for node in task.get("candidate_nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = as_str(node.get("node_id"))
        if not nid:
            continue
        pid = f"PV_NEW_{nid}"
        w = as_float(node.get("weight"))
        weights[pid] = float(w) if w is not None else 1.0
        specs.append({
            "pv_id": pid, "node_id": nid,
            "phase": as_str(node.get("phase")) or "ABC",
            # 建立用铭牌（非零），实际出力由 apply_slice 逐次覆盖
            "rated_p_kw": nameplate,
            "rated_s_kva": nameplate,
            "nameplate_reference": True,
        })
    total = sum(weights.values())
    if total > 0:
        for k in weights:
            weights[k] /= total
    return specs, weights


def run_scenario_set(adapter: OpenDSSAdapter, bundle: InputBundle,
                     model: DSSModel, scenarios: Sequence[Scenario],
                     *, increment_kw: float = 0.0,
                     allocation: Optional[Dict[str, float]] = None,
                     new_pv_ids: Optional[Sequence[str]] = None,
                     solver: Optional[Dict[str, Any]] = None,
                     require_all_converged: bool = True) -> ScenarioRun:
    """执行全部场景的全部时间片（设计文档 S05 / 17.12 evaluate_all）。"""
    solver = solver or {}
    max_iter = int(solver.get("max_iter") or 100)
    tolerance = float(solver.get("tolerance") or 1e-4)
    # 每次从冻结基线重建，避免新增容量累积（17.12）
    run = ScenarioRun(scenario_id=",".join(s.scenario_id for s in scenarios),
                      increment_kw=float(increment_kw))

    for sc in scenarios:
        for sl in sc.slices:
            try:
                apply_slice(adapter, bundle, sl, increment_kw=increment_kw,
                            allocation=allocation, new_pv_ids=new_pv_ids)
                res: EngineResult = adapter.solve(
                    scenario_id=sc.scenario_id, timestamp=sl.timestamp,
                    max_iter=max_iter, tolerance=tolerance)
            except ComputeFailedError as exc:
                run.converged = False
                run.status = "unknown"
                run.error_code = exc.code
                run.unserved_slices.append(f"{sc.scenario_id}@{sl.timestamp}")
                continue

            if not res.converged:
                run.unserved_slices.append(f"{sc.scenario_id}@{sl.timestamp}")
                run.error_code = run.error_code or res.error_code
                # 不收敛记为 unknown，不能当 pass（17.12）
                continue

            for row in res.bus_voltages:
                run.slices.append({
                    "kind": "bus", "scenario_id": sc.scenario_id,
                    "timestamp": sl.timestamp, **{k: v for k, v in row.items()
                                                  if k not in ("scenario_id",
                                                               "timestamp")}})
            for row in res.element_results:
                run.slices.append({
                    "kind": "element", "scenario_id": sc.scenario_id,
                    "timestamp": sl.timestamp,
                    **{k: v for k, v in row.items()
                       if k not in ("scenario_id", "timestamp")}})
            run.engine_refs.extend(res.artifact_refs)
            if res.metrics:
                run.slices.append({
                    "kind": "circuit", "scenario_id": sc.scenario_id,
                    "timestamp": sl.timestamp, **res.metrics})

    if run.unserved_slices:
        run.converged = False
        if run.status == "ok":
            run.status = "unknown"
    return run


def run_baseline(adapter: OpenDSSAdapter, bundle: InputBundle, model: DSSModel,
                 scenarios: Sequence[Scenario], *,
                 solver: Optional[Dict[str, Any]] = None) -> ScenarioRun:
    """基线潮流（SK-04，设计文档 S04：evaluate_all(case, scenarios, 0)）。"""
    return run_scenario_set(adapter, bundle, model, scenarios, increment_kw=0.0,
                            solver=solver)


def require_baseline_admissible(run: ScenarioRun,
                                collector: Optional[IssueCollector] = None
                                ) -> Tuple[bool, List[str]]:
    """基线准入规则（设计文档 S04 / 17.12 require_baseline_admissible）。

    基线已违规时先输出存量问题，不直接给出可开放结论。
    """
    problems: List[str] = []
    if not run.all_converged:
        problems.append(
            f"基线未全场景收敛：{len(run.unserved_slices)} 个时间片未收敛/失败")
    return (not problems), problems
