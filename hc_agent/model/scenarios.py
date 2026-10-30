"""scenarios.py — 运行场景生成（SK-03）。

设计文档 6.1 评估对象与场景 / 17.12 场景循环与容量搜索实现逻辑。

评估以"对象 × 时间片 × 运行场景 × 约束"为基本计算单元。
场景同时覆盖高光伏低负荷、低光伏高负荷、典型运行方式及经批准的检修/转供方式。
随机或概率场景作为增强能力，不能替代基准确定性校核。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import InputBlockedError
from hc_agent.io.ingest import InputBundle
from hc_agent.io.schema import as_float, as_str, parse_iso
from hc_agent.rules import RulePackage

# 场景类别（设计文档 6.1）
SCENARIO_TYPICAL_DAY = "typical_day"
SCENARIO_HIGH_PV_LOW_LOAD = "high_pv_low_load"
SCENARIO_LOW_PV_HIGH_LOAD = "low_pv_high_load"
SCENARIO_MAINTENANCE = "maintenance_transfer"
SCENARIO_EXTREME = "extreme"


@dataclass
class Slice:
    """一个时间片（设计文档 6.1）。"""

    scenario_id: str
    timestamp: str
    load_p_kw: Dict[str, float] = field(default_factory=dict)
    load_q_kvar: Dict[str, float] = field(default_factory=dict)
    load_phase: Dict[str, str] = field(default_factory=dict)
    pv_avail_pu: Dict[str, float] = field(default_factory=dict)
    pv_p_kw: Dict[str, float] = field(default_factory=dict)
    switch_state: Dict[str, bool] = field(default_factory=dict)
    tap_state: Dict[str, float] = field(default_factory=dict)
    control_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "timestamp": self.timestamp,
            "load_p_kw": self.load_p_kw, "pv_avail_pu": self.pv_avail_pu,
            "switch_state": self.switch_state,
        }


@dataclass
class Scenario:
    """场景（设计文档 表 56 scenarios.json）。"""

    scenario_id: str
    topology_ref: str = ""
    profile_ref: str = ""
    time_window: List[str] = field(default_factory=list)
    control_ref: str = ""
    weight: float = 1.0
    scenario_class: str = SCENARIO_TYPICAL_DAY
    slices: List[Slice] = field(default_factory=list)
    analysis_types: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        ts = [s.timestamp for s in self.slices]
        return {
            "scenario_id": self.scenario_id,
            "topology_ref": self.topology_ref,
            "profile_ref": self.profile_ref,
            "time_window": self.time_window,
            # 冻结实际使用的时间片清单，供独立复算逐片复现。
            # 只给 [start,end] 窗口不足以复现：窗口裁剪会漏掉窗口外的
            # 实际时间片（设计文档 9.1-3 要求按相同场景集复算）。
            "slice_timestamps": ts,
            "slice_count": len(self.slices),
            "control_ref": self.control_ref,
            "weight": self.weight,
            "scenario_class": self.scenario_class,
            "analysis_types": self.analysis_types,
        }


def build_scenarios(bundle: InputBundle, rules: RulePackage, *,
                    max_slices: Optional[int] = None) -> List[Scenario]:
    """按批准场景集生成场景（设计文档 S03 / 17.12）。

    每个时间片先应用开关/分接状态、负荷 P/Q、已有光伏出力和控制策略，
    再增加候选接入容量。
    同时率如已体现在曲线中，不得再次相乘。
    """
    prof_by_res = bundle.profiles_by_resource()
    timestamps = bundle.timestamps()
    tw = (bundle.task or {}).get("time_window") or {}
    start = parse_iso(tw.get("start"))
    end = parse_iso(tw.get("end"))
    if start and end:
        timestamps = [t for t in timestamps
                      if (lambda dt: dt is not None and start <= dt < end)
                      (parse_iso(t))]
    if not timestamps:
        raise InputBlockedError("时间窗内无可用时间片（时序评估必需）")

    if max_slices and len(timestamps) > max_slices:
        timestamps = _downsample(timestamps, max_slices)

    # 新增容量候选（task.candidate_nodes）在 bundle.pv 中不存在，但同样需要
    # 逐时间片的可用出力系数。此前只有在 bundle.pv 中登记的机组会被填
    # pv_avail_pu，导致候选件查不到曲线而按 100% 恒定出力注入（不平physical）。
    # 这里为候选件按其接入母线上已有光伏的出力形状（若无则用标准日内钟形）
    # 预置曲线，使候选新增容量的时序特性与既有光伏一致。
    cand_shapes = _candidate_profiles(bundle, prof_by_res, timestamps)

    # 如果给出了已登记场景集，按登记的场景顺序切片
    registered = _registered_scenarios(bundle)
    scenarios: List[Scenario] = []

    if registered:
        for spec in registered:
            sid = as_str(spec.get("scenario_id"))
            s_ts = [t for t in timestamps
                    if not spec.get("timestamps") or t in set(spec["timestamps"])]
            if not s_ts:
                s_ts = timestamps
            sc = Scenario(
                scenario_id=sid,
                topology_ref=as_str(spec.get("topology_ref")),
                profile_ref=as_str(spec.get("profile_ref")),
                time_window=[s_ts[0], s_ts[-1]],
                control_ref=as_str(spec.get("control_ref")),
                weight=as_float(spec.get("weight")) or 1.0,
                scenario_class=as_str(spec.get("scenario_class"))
                or SCENARIO_TYPICAL_DAY,
                analysis_types=[as_str(a) for a in
                                (spec.get("analysis_types") or [])],
            )
            sc.slices = _build_slices(bundle, prof_by_res, s_ts, sid,
                                      spec.get("switch_state"),
                                      spec.get("tap_state"),
                                      cand_shapes=cand_shapes)
            scenarios.append(sc)
        return scenarios

    # 未登记场景集：生成基准典型日场景（真实曲线）与派生极端场景。
    base = Scenario(
        scenario_id="S_BASE",
        topology_ref=as_str((bundle.meta or {}).get("topology_version")),
        profile_ref="profiles.csv",
        time_window=[timestamps[0], timestamps[-1]],
        scenario_class=SCENARIO_TYPICAL_DAY,
        analysis_types=list((bundle.task or {}).get("analysis_types") or []),
    )
    base.slices = _build_slices(bundle, prof_by_res, timestamps,
                                base.scenario_id, cand_shapes=cand_shapes)
    scenarios.append(base)

    scenario_rules = rules.raw.get("scenarios") or {}
    if scenario_rules.get("generate_derived", True):
        derived = scenario_rules.get("derived") or [
            SCENARIO_HIGH_PV_LOW_LOAD, SCENARIO_LOW_PV_HIGH_LOAD]
        for cls in derived:
            sc = _derive_extreme(base, cls)
            if sc is not None:
                scenarios.append(sc)

    return scenarios


def _candidate_profiles(bundle: InputBundle,
                        prof_by_res: Dict[str, List[Dict[str, Any]]],
                        timestamps: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """为新增容量候选件生成逐时间片可用出力系数。

    形状来源优先级：
      1) 候选接入母线上已登记光伏的出力曲线（同母线即同资源特性）；
      2) 全部已有光伏的归一化平均形状；
      3) 标准日内钟形曲线（无任何已有光伏时）。
    """
    task = bundle.task or {}
    cand_nodes = [as_str(n.get("node_id"))
                  for n in (task.get("candidate_nodes") or [])
                  if isinstance(n, dict)]
    if not cand_nodes:
        return {}

    # 收集已有光伏的形状：{node_id: {ts: avail}}
    by_node: Dict[str, Dict[str, float]] = {}
    for pv in bundle.pv:
        pid = as_str(pv["pv_id"])
        node = as_str(pv.get("node_id"))
        rows = prof_by_res.get(pid) or prof_by_res.get(
            as_str(pv.get("profile_id"))) or []
        shape: Dict[str, float] = {}
        for r in rows:
            ts = as_str(r.get("timestamp"))
            if not ts:
                continue
            rated = as_float(pv.get("rated_p_kw")) or as_float(
                pv.get("rated_s_kva")) or 0.0
            if r.get("available_p_pu") is not None:
                shape[ts] = max(0.0, min(1.0, float(r["available_p_pu"])))
            elif r.get("p_kw") is not None and rated > 0:
                shape[ts] = max(0.0, min(1.0, float(r["p_kw"]) / rated))
        if shape:
            by_node.setdefault(node, {}).update(shape)

    # 全网平均形状
    agg: Dict[str, List[float]] = {}
    for shape in by_node.values():
        for ts, v in shape.items():
            agg.setdefault(ts, []).append(v)
    mean_shape = {ts: sum(vs) / len(vs) for ts, vs in agg.items() if vs}

    out: Dict[str, Dict[str, float]] = {}
    for node in cand_nodes:
        pid = f"PV_NEW_{node}"
        if node in by_node:
            out[pid] = dict(by_node[node])
        elif mean_shape:
            out[pid] = dict(mean_shape)
        else:
            out[pid] = {ts: _bell_pu(ts) for ts in timestamps}
        # 补齐缺失时间片，避免查表落空后按恒定 100% 注入
        for ts in timestamps:
            out[pid].setdefault(ts, mean_shape.get(ts, _bell_pu(ts)))
    return out


def _bell_pu(timestamp: str) -> float:
    """标准日内钟形出力系数（无任何实测曲线时的兜底形状）。

    峰值在 13:00，夜间为 0；仅用于缺乏实测曲线的场合，并在结果中可由
    scenario/profile 引用追溯到输入。
    """
    dt = parse_iso(timestamp)
    if dt is None:
        return 1.0
    h = dt.hour + dt.minute / 60.0
    if h < 6.0 or h > 20.0:
        return 0.0
    # 以 13:00 为中心的半正弦，6:00 与 20:00 为 0
    span = 14.0
    frac = (h - 6.0) / span
    return max(0.0, math.sin(math.pi * frac) * 0.85)


def _registered_scenarios(bundle: InputBundle) -> List[Dict[str, Any]]:
    sc = bundle.scenario_set or {}
    items = sc.get("scenarios") if isinstance(sc, dict) else None
    return list(items) if items else []


def _build_slices(bundle: InputBundle, prof_by_res: Dict[str, List[Dict[str, Any]]],
                  timestamps: Sequence[str], scenario_id: str,
                  switch_state: Optional[Dict[str, Any]] = None,
                  tap_state: Optional[Dict[str, Any]] = None,
                  cand_shapes: Optional[Dict[str, Dict[str, float]]] = None
                  ) -> List[Slice]:
    slices: List[Slice] = []
    for ts in timestamps:
        sl = Slice(scenario_id=scenario_id, timestamp=ts)
        # 负荷：按 resource_id 取该时刻的 P/Q（p_kw 消费方向为正）
        for ld in bundle.loads:
            rid = as_str(ld["load_id"])
            rows = _rows_at(prof_by_res, rid, ts)
            if not rows:
                # 也允许曲线挂在 profile_id 上
                rows = _rows_at(prof_by_res, as_str(ld.get("profile_id")), ts)
            p = sum((r.get("p_kw") or 0.0) for r in rows
                    if r.get("resource_type") in ("load", "", "measurement"))
            q = sum((r.get("q_kvar") or 0.0) for r in rows
                    if r.get("resource_type") in ("load", "", "measurement"))
            if rows:
                sl.load_p_kw[rid] = p
                sl.load_q_kvar[rid] = q
                sl.load_phase[rid] = as_str(rows[0].get("phase")) or "ALL"
        # 光伏：available_p_pu 或 p_kw（设计文档 17.8 二选一）
        for pv in bundle.pv:
            rid = as_str(pv["pv_id"])
            rows = _rows_at(prof_by_res, rid, ts)
            if not rows:
                rows = _rows_at(prof_by_res, as_str(pv.get("profile_id")), ts)
            if not rows:
                sl.pv_avail_pu[rid] = 0.0
                continue
            avail = None
            p_kw = None
            for r in rows:
                if r.get("available_p_pu") is not None:
                    avail = float(r["available_p_pu"])
                if r.get("p_kw") is not None:
                    p_kw = float(r["p_kw"])
            rated = as_float(pv.get("rated_p_kw")) or as_float(
                pv.get("rated_s_kva")) or 0.0
            if avail is not None:
                sl.pv_avail_pu[rid] = max(0.0, min(1.0, avail))
            elif p_kw is not None and rated > 0:
                sl.pv_avail_pu[rid] = max(0.0, min(1.0, p_kw / rated))
            elif p_kw is not None:
                sl.pv_avail_pu[rid] = max(0.0, min(1.0, p_kw / max(rated, 1e-9)))
            else:
                sl.pv_avail_pu[rid] = 0.0
        # 新增容量候选件：按其接入母线的出力形状写入（见 _candidate_profiles）
        if cand_shapes:
            for pid, shape in cand_shapes.items():
                v = shape.get(ts)
                sl.pv_avail_pu[pid] = 0.0 if v is None else max(0.0, min(1.0, v))
        # 开关与分接：按场景状态或输入快照默认值
        for sw in bundle.switches:
            sid = as_str(sw["switch_id"])
            if switch_state and sid in switch_state:
                sl.switch_state[sid] = bool(switch_state[sid])
            else:
                sl.switch_state[sid] = bool(sw.get("closed", True))
        for tr in bundle.transformers:
            tid = as_str(tr["transformer_id"])
            if tap_state and tid in tap_state:
                sl.tap_state[tid] = float(tap_state[tid])
            else:
                sl.tap_state[tid] = float(tr.get("tap_pos") or 0)
        slices.append(sl)
    return slices


def _rows_at(prof_by_res: Dict[str, List[Dict[str, Any]]], rid: str,
             ts: str) -> List[Dict[str, Any]]:
    if not rid:
        return []
    return [r for r in prof_by_res.get(rid, []) if as_str(r.get("timestamp")) == ts]


def _derive_extreme(base: Scenario, scenario_class: str) -> Optional[Scenario]:
    """由基准场景派生极端场景（设计文档 6.1）。

    高光伏低负荷：取光伏出力比例最高、负荷最低的时段；
    低光伏高负荷：取光伏最低、负荷最高的时段。
    保持真实曲线取值，不虚构数据。
    """
    if not base.slices:
        return None
    scored: List[Tuple[float, Slice]] = []
    for sl in base.slices:
        load_total = sum(sl.load_p_kw.values()) if sl.load_p_kw else 0.0
        pv_total = sum(sl.pv_avail_pu.values()) if sl.pv_avail_pu else 0.0
        if scenario_class == SCENARIO_HIGH_PV_LOW_LOAD:
            score = pv_total - load_total / max(1.0, _load_scale(base))
            top = True
        elif scenario_class == SCENARIO_LOW_PV_HIGH_LOAD:
            score = load_total / max(1.0, _load_scale(base)) - pv_total
            top = True
        else:
            continue
        scored.append((score, sl))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    keep = [s for _, s in scored[: max(1, len(scored) // 4)]]
    # 必须按时间排序后再取窗口。
    # keep 当前是按**评分**排序的（高光伏低负荷 / 低光伏高负荷的极端时段），
    # 其首尾并不是最早/最晚时刻。直接用 keep[0]/keep[-1] 会得到反向或错误的
    # time_window（本机实测：S_HIGH_PV_LOW_LOAD 的窗口曾为 12:00 -> 10:00，
    # 起点晚于终点）。该窗口被写入 scenarios.json 后被独立复算用于裁剪时间片，
    # 导致主计算与复算覆盖不同的时间片集合、复算出虚假差异。
    keep.sort(key=lambda s: s.timestamp)
    sc = Scenario(
        scenario_id=f"S_{scenario_class.upper()}",
        topology_ref=base.topology_ref,
        profile_ref=base.profile_ref,
        time_window=[keep[0].timestamp, keep[-1].timestamp],
        control_ref=base.control_ref,
        scenario_class=scenario_class,
        analysis_types=list(base.analysis_types),
    )
    sc.slices = [Slice(scenario_id=sc.scenario_id, timestamp=s.timestamp,
                       load_p_kw=dict(s.load_p_kw),
                       load_q_kvar=dict(s.load_q_kvar),
                       load_phase=dict(s.load_phase),
                       pv_avail_pu=dict(s.pv_avail_pu),
                       switch_state=dict(s.switch_state),
                       tap_state=dict(s.tap_state))
                 for s in keep]
    return sc


def _load_scale(base: Scenario) -> float:
    vals = [sum(s.load_p_kw.values()) for s in base.slices if s.load_p_kw]
    return max(vals) if vals else 1.0


def _downsample(timestamps: Sequence[str], n: int) -> List[str]:
    if len(timestamps) <= n:
        return list(timestamps)
    step = len(timestamps) / float(n)
    return [timestamps[min(len(timestamps) - 1, int(i * step))]
            for i in range(n)]
