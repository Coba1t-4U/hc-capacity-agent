"""validation.py — 数据质量与拓扑校验（SK-01）。

设计文档 5.3 数据质量规则、17.6 拓扑校验、表 21。

拓扑校验依次检查：外键存在、开关状态与有效时间、设备两端连接、
从 sources 到目标节点的供电路径、孤岛和多电源边界。
networkx 可以做图校验，但不能替代电气潮流求解（17.6）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from hc_agent.errors import (
    ERR_FOREIGN_KEY_MISSING,
    ERR_TOPOLOGY_DISCONNECTED,
    ERR_TOPOLOGY_ISLAND,
    ERR_VERSION_CONFLICT,
    InputBlockedError,
)
from hc_agent.io.ingest import InputBundle
from hc_agent.io.schema import IssueCollector, as_str


# ------------------------------------------------------------------ 图校验
def _build_graph(bundle: InputBundle) -> Tuple[Any, Dict[str, List[str]]]:
    """用 networkx 建图；不可用时退化为纯 Python 邻接表。"""
    adj: Dict[str, List[str]] = {as_str(b["node_id"]): [] for b in bundle.buses}
    edges: List[Tuple[str, str]] = []

    for ln in bundle.lines:
        if ln.get("in_service") is False:
            continue
        f, t = as_str(ln["from_node"]), as_str(ln["to_node"])
        edges.append((f, t))
    for tr in bundle.transformers:
        if tr.get("in_service") is False:
            continue
        edges.append((as_str(tr["hv_node"]), as_str(tr["lv_node"])))

    # 开关：闭合视为连通，断开视为断开（17.6：开关状态与有效时间）
    open_switches: Set[str] = set()
    for sw in bundle.switches:
        if sw.get("closed") is False:
            eid = as_str(sw.get("element_id"))
            if eid:
                open_switches.add(eid)

    for f, t in edges:
        if f in open_switches or t in open_switches:
            continue
        adj.setdefault(f, []).append(t)
        adj.setdefault(t, []).append(f)

    try:
        import networkx as nx
        g = nx.Graph()
        g.add_nodes_from(adj.keys())
        for f, t in edges:
            if f in open_switches or t in open_switches:
                continue
            g.add_edge(f, t)
        return g, adj
    except ImportError:
        return None, adj


def check_topology(bundle: InputBundle, collector: IssueCollector,
                   *, target_nodes: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """拓扑校验（设计文档 17.6）。

    返回 {components, islands, unreachable, sources, multi_source_boundaries}。

    说明：target_nodes 可为节点 ID 或馈线 ID；馈线 ID 通过 buses.feeder_id
    解析为其成员节点后再做供电路径判定（设计文档 17.5 scope 引用业务对象）。
    """
    bus_ids = {as_str(b["node_id"]) for b in bundle.buses}
    bus_by_id = bundle.bus_by_id()

    # 1) 外键存在
    def _fk(node: str, where: str, row: Optional[int], field: str,
            obj: str) -> bool:
        if not node:
            return False
        if node not in bus_ids:
            collector.add(file="network.xlsx", sheet=where, row=row,
                          field=field, object_id=obj,
                          code=ERR_FOREIGN_KEY_MISSING,
                          message=f"{field}={node} 不存在于 buses 表",
                          severity="error", blocking=True)
            return False
        return True

    for ln in bundle.lines:
        _fk(as_str(ln["from_node"]), "lines", ln.get("_row"), "from_node",
            as_str(ln["line_id"]))
        _fk(as_str(ln["to_node"]), "lines", ln.get("_row"), "to_node",
            as_str(ln["line_id"]))
    for tr in bundle.transformers:
        _fk(as_str(tr["hv_node"]), "transformers", tr.get("_row"), "hv_node",
            as_str(tr["transformer_id"]))
        _fk(as_str(tr["lv_node"]), "transformers", tr.get("_row"), "lv_node",
            as_str(tr["transformer_id"]))
    for ld in bundle.loads:
        _fk(as_str(ld["node_id"]), "loads", ld.get("_row"), "node_id",
            as_str(ld["load_id"]))
    for pv in bundle.pv:
        _fk(as_str(pv["node_id"]), "pv", pv.get("_row"), "node_id",
            as_str(pv["pv_id"]))

    # 2) 相别一致性：设备相别必须与节点相别兼容
    for ln in bundle.lines:
        f = as_str(ln["from_node"])
        if f in bus_by_id:
            _phase_check(ln.get("phase", "ABC"), bus_by_id[f].get("phase", "ABC"),
                         "lines", ln.get("_row"), as_str(ln["line_id"]),
                         "phase", collector)

    g, adj = _build_graph(bundle)

    # 3) 连通分量与孤岛
    components: List[List[str]] = []
    if g is not None:
        components = [sorted(c) for c in _nx_components(g)]
    else:
        components = _py_components(adj)

    source_nodes = [as_str(s["node_id"]) for s in bundle.sources]

    islands: List[List[str]] = []
    for comp in components:
        if source_nodes and not (set(comp) & set(source_nodes)):
            islands.append(comp)
            collector.add(file="network.xlsx", sheet="buses",
                          object_id=",".join(comp[:5]),
                          code=ERR_TOPOLOGY_ISLAND,
                          message=f"存在孤岛节点（无供电路径至 sources）："
                                  f"{comp[:8]}{'...' if len(comp) > 8 else ''}",
                          severity="error", blocking=True)

    # 4) 从 sources 到目标节点的供电路径
    reachable: Set[str] = set()
    if source_nodes:
        for s in source_nodes:
            if g is not None:
                if s in g:
                    reachable |= set(_nx_reachable(g, s))
            else:
                reachable |= _py_reachable(adj, s)

    unreachable: List[str] = []
    targets = list(target_nodes or [])
    if not targets:
        scene_scope = (bundle.task.get("scope") or {})
        targets = [as_str(x) for x in (scene_scope.get("ids") or [])]
    # scope 可能引用馈线/变电站 ID：解析为成员节点（设计文档 17.5）
    resolved_targets: List[str] = []
    unresolved_refs: List[str] = []
    feeders = {as_str(b.get("feeder_id")) for b in bundle.buses
               if as_str(b.get("feeder_id"))}
    for t in targets:
        if not t:
            continue
        if t in bus_ids:
            resolved_targets.append(t)
        elif t in feeders:
            members = [as_str(b["node_id"]) for b in bundle.buses
                       if as_str(b.get("feeder_id")) == t]
            resolved_targets.extend(members)
        else:
            # 悬空引用：进入未解析清单，由调用方按业务口径决定
            unresolved_refs.append(t)

    for t in resolved_targets:
        if t not in reachable:
            unreachable.append(t)

    if unresolved_refs:
        collector.add(file="task.json", field="scope.ids",
                      object_id=",".join(unresolved_refs),
                      code="SCOPE_REF_UNRESOLVED",
                      message=f"对象范围引用无法在模型中解析为节点或馈线："
                              f"{unresolved_refs}",
                      severity="error", blocking=True)

    if unreachable and source_nodes:
        collector.add(file="network.xlsx", sheet="buses",
                      object_id=",".join(unreachable),
                      code=ERR_TOPOLOGY_DISCONNECTED,
                      message=f"目标节点无供电路径：{unreachable}",
                      severity="error", blocking=True)

    # 5) 多电源边界
    multi = []
    if len(source_nodes) > 1:
        multi = source_nodes
        collector.add(file="network.xlsx", sheet="sources",
                      object_id=",".join(source_nodes),
                      code="MULTI_SOURCE_BOUNDARY",
                      message=f"存在多电源边界（{len(source_nodes)} 个），"
                              f"须确认运行方式",
                      severity="warning", blocking=False)

    return {
        "components": components,
        "islands": islands,
        "unreachable": unreachable,
        "sources": source_nodes,
        "reachable_count": len(reachable),
        "multi_source_boundaries": multi,
        "bus_count": len(bus_ids),
        "scope_refs": targets,
        "resolved_targets": resolved_targets,
        "unresolved_refs": unresolved_refs,
        "feeders": sorted(feeders),
    }


def _phase_check(dev_phase: str, bus_phase: str, sheet: str, row: Optional[int],
                 obj: str, field: str, collector: IssueCollector) -> None:
    dev = set(as_str(dev_phase).upper().replace("ABC", "ABC") or "ABC")
    bus = set(as_str(bus_phase).upper() or "ABC")
    if dev - bus:
        collector.add(file="network.xlsx", sheet=sheet, row=row, field=field,
                      object_id=obj, code="PHASE_CONFLICT",
                      message=f"设备相别 {dev_phase} 超出节点相别 {bus_phase}",
                      severity="error", blocking=True)


def _nx_components(g) -> List[list]:
    import networkx as nx
    return [list(c) for c in nx.connected_components(g)]


def _nx_reachable(g, source: str) -> Set[str]:
    import networkx as nx
    return set(nx.node_connected_component(g, source))


def _py_components(adj: Dict[str, List[str]]) -> List[List[str]]:
    seen: Set[str] = set()
    comps: List[List[str]] = []
    for start in adj:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in adj.get(cur, []):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        comps.append(sorted(comp))
    return comps


def _py_reachable(adj: Dict[str, List[str]], source: str) -> Set[str]:
    seen: Set[str] = set()
    stack = [source]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                stack.append(nxt)
    return seen


# --------------------------------------------------------------- 版本一致性
def check_version_consistency(bundle: InputBundle,
                              collector: IssueCollector) -> Dict[str, Any]:
    """版本性校验（设计文档 表 21）。

    模型、参数、量测不属于同一有效期 → 阻断，重新选择一致快照。
    """
    meta = bundle.meta or {}
    out: Dict[str, Any] = {"snapshot_id": meta.get("snapshot_id"),
                           "topology_version": meta.get("topology_version"),
                           "asset_version": meta.get("asset_version")}

    task_snap = as_str((bundle.task or {}).get("snapshot_id"))
    meta_snap = as_str(meta.get("snapshot_id"))
    if task_snap and meta_snap and task_snap != meta_snap:
        collector.add(file="task.json", field="snapshot_id",
                      code=ERR_VERSION_CONFLICT,
                      message=f"task.snapshot_id={task_snap} 与 "
                              f"network.meta.snapshot_id={meta_snap} 不一致",
                      severity="error", blocking=True)
        out["consistent"] = False
    else:
        out["consistent"] = True
    return out


# ------------------------------------------------------------- 时序覆盖校验
def check_time_coverage(bundle: InputBundle, collector: IssueCollector,
                        *, time_step_minutes: Optional[int] = None,
                        min_coverage: float = 0.9) -> Dict[str, Any]:
    """时序性校验（设计文档 表 21 / 17.5）。

    time_window.end 大于 start 且曲线覆盖全部窗口。
    """
    from hc_agent.io.schema import parse_iso

    tw = (bundle.task or {}).get("time_window") or {}
    start, end = parse_iso(tw.get("start")), parse_iso(tw.get("end"))
    stamps = []
    for p in bundle.profiles:
        dt = parse_iso(p.get("timestamp"))
        if dt is not None:
            stamps.append(dt)

    out: Dict[str, Any] = {"window": [tw.get("start"), tw.get("end")],
                           "distinct_timestamps": len(set(stamps))}
    if not stamps:
        collector.add(file="profiles.csv", code="TIME_COVERAGE_INSUFFICIENT",
                      message="时序文件无可解析的带时区时间戳",
                      severity="error", blocking=True)
        out["coverage"] = 0.0
        return out

    tz = stamps[0].tzinfo
    if any(s.tzinfo is None for s in stamps):
        collector.add(file="profiles.csv", code="TIMEZONE_INCONSISTENT",
                      message="时序中存在无时区的时间戳", severity="error",
                      blocking=True)

    in_window = [s for s in stamps if start and end and start <= s < end]
    uniq_in = len(set(in_window))
    step = time_step_minutes or int((bundle.task or {}).get("time_step_minutes")
                                    or 60)
    expected = max(1, int((end - start).total_seconds() // 60 // step)) \
        if (start and end) else 0
    coverage = (uniq_in / expected) if expected else 0.0
    out.update({"expected_slices": expected, "observed_slices": uniq_in,
                "coverage": round(coverage, 4), "time_step_minutes": step,
                "timezone": str(tz)})

    if expected and coverage < min_coverage:
        collector.add(file="profiles.csv", code="TIME_COVERAGE_INSUFFICIENT",
                      message=f"曲线覆盖不足：窗口需 {expected} 个时间片，"
                              f"实测 {uniq_in} 个（覆盖率 {coverage:.1%}）",
                      severity="error", blocking=True)
    return out


# ------------------------------------------------------- 负荷/光伏映射校验
def check_resource_mapping(bundle: InputBundle,
                           collector: IssueCollector) -> Dict[str, Any]:
    """设备与曲线映射校验（设计文档 17.8）。"""
    prof_ids = {as_str(p["profile_id"]) for p in bundle.profiles}
    res_ids = {as_str(p["resource_id"]) for p in bundle.profiles}
    missing = []

    for ld in bundle.loads:
        pid = as_str(ld.get("profile_id"))
        if pid and pid not in prof_ids:
            missing.append(("loads", as_str(ld["load_id"]), pid))
    for pv in bundle.pv:
        pid = as_str(pv.get("profile_id"))
        if pid and pid not in prof_ids:
            missing.append(("pv", as_str(pv["pv_id"]), pid))

    for sheet, oid, pid in missing:
        collector.add(file="profiles.csv", sheet=sheet, object_id=oid,
                      field="profile_id", code="PROFILE_NOT_FOUND",
                      message=f"{oid} 引用的曲线 {pid} 在 profiles 中不存在",
                      severity="error", blocking=True)

    # 重复占用校验（17.7：已并网光伏只进入基线一次）
    pv_ids = {as_str(p["pv_id"]) for p in bundle.pv}
    dup = [r for r in bundle.active_reservations()
           if as_str(r.get("object_id")) in pv_ids]
    if dup:
        collector.add(file="reservations.csv", code="RESERVATION_DOUBLE_COUNT",
                      message=f"{len(dup)} 条占用记录的对象同时是已并网光伏设备，"
                              f"存在重复扣减风险",
                      severity="warning", blocking=False)

    return {"profile_ids": len(prof_ids), "resource_ids": len(res_ids),
            "missing_mappings": missing}


# ------------------------------------------------------------------- 汇总
def validate_bundle(bundle: InputBundle, *,
                    require_profiles: bool = True,
                    min_time_coverage: float = 0.9) -> IssueCollector:
    """执行全部质量与拓扑校验，返回问题收集器（设计文档 S02）。

    blocking_count=0 才继续（表 46）。
    """
    col = bundle.collector or IssueCollector()
    bundle.collector = col

    check_version_consistency(bundle, col)

    scoped_ids: List[str] = []
    scope = (bundle.task or {}).get("scope") or {}
    if isinstance(scope, dict):
        scoped_ids = [as_str(x) for x in (scope.get("ids") or [])]
    target_nodes = [as_str(n.get("node_id"))
                    for n in ((bundle.task or {}).get("candidate_nodes") or [])
                    if isinstance(n, dict)]

    check_topology(bundle, col, target_nodes=scoped_ids or target_nodes)
    if require_profiles and bundle.profiles:
        check_time_coverage(
            bundle, col,
            time_step_minutes=(bundle.task or {}).get("time_step_minutes"))
    check_resource_mapping(bundle, col)

    # 对象范围内设备存在性（7.1 前置判定：接入点存在性）
    bus_ids = {as_str(b["node_id"]) for b in bundle.buses}
    for nid in target_nodes:
        if nid and nid not in bus_ids:
            col.add(file="task.json", field="candidate_nodes.node_id",
                    object_id=nid, code="NODE_NOT_FOUND",
                    message=f"候选接入点 {nid} 不存在于模型中",
                    severity="error", blocking=True)

    return col


def raise_if_blocking(col: IssueCollector) -> None:
    """blocking_count>0 时阻断（设计文档 17.12 伪代码）。"""
    blocking = col.blocking
    if blocking:
        raise InputBlockedError(
            f"输入校验未通过：{len(blocking)} 项阻断缺陷",
            issues=[i.to_row(n + 1) for n, i in enumerate(blocking)],
        )
