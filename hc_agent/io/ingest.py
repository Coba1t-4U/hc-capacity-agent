"""ingest.py — 输入文件包读取（设计文档 17.4 输入文件包与通用格式）。

建议接收 task.json、network.xlsx、profiles.csv、rules.yaml，
以及有申请时的 application.json。

工具责任（表 54）：pandas、openpyxl、pyarrow 用于读表与类型转换，
不计算潮流。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.io.schema import (
    IssueCollector,
    as_bool,
    as_float,
    as_int,
    as_str,
    read_csv,
    validate_application,
    validate_meta,
    validate_reservations,
    validate_task,
)

# network.xlsx 固定英文工作表与英文表头（17.4）
NETWORK_SHEETS = ("meta", "buses", "lines", "transformers", "switches",
                  "loads", "pv", "sources")

PROFILE_HEADERS = ("profile_id", "resource_id", "resource_type", "timestamp",
                   "phase", "p_kw", "q_kvar")

PROFILE_RESOURCE_TYPES = ("load", "pv", "measurement", "wind", "storage")


@dataclass
class InputBundle:
    """输入包（设计文档 17.4-17.9）。"""

    root: Path
    task: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, str] = field(default_factory=dict)
    buses: List[Dict[str, Any]] = field(default_factory=list)
    lines: List[Dict[str, Any]] = field(default_factory=list)
    transformers: List[Dict[str, Any]] = field(default_factory=list)
    switches: List[Dict[str, Any]] = field(default_factory=list)
    loads: List[Dict[str, Any]] = field(default_factory=list)
    pv: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    profiles: List[Dict[str, Any]] = field(default_factory=list)
    reservations: List[Dict[str, Any]] = field(default_factory=list)
    application: Optional[Dict[str, Any]] = None
    equipment_limits: List[Dict[str, Any]] = field(default_factory=list)
    fault_models: List[Dict[str, Any]] = field(default_factory=list)
    controls: Dict[str, Any] = field(default_factory=dict)
    scenario_set: Dict[str, Any] = field(default_factory=dict)
    native_dss: Optional[str] = None
    collector: Optional[IssueCollector] = None
    files: Dict[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------- 便捷视图
    def bus_by_id(self) -> Dict[str, Dict[str, Any]]:
        return {as_str(b.get("node_id")): b for b in self.buses}

    def profiles_by_resource(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for p in self.profiles:
            out.setdefault(as_str(p.get("resource_id")), []).append(p)
        return out

    def timestamps(self) -> List[str]:
        seen: List[str] = []
        for p in self.profiles:
            ts = as_str(p.get("timestamp"))
            if ts and ts not in seen:
                seen.append(ts)
        return sorted(seen)

    def active_reservations(self) -> List[Dict[str, Any]]:
        return [r for r in self.reservations if r.get("status") == "active"]

    def reservations_by_object(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for r in self.active_reservations():
            obj = as_str(r.get("object_id"))
            out[obj] = out.get(obj, 0.0) + float(r.get("capacity_kw") or 0.0)
        return out


def _read_excel_sheet(path: Path, sheet: str,
                      collector: IssueCollector) -> List[Dict[str, Any]]:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover
        collector.add(file=path.name, sheet=sheet, code="DEPENDENCY_MISSING",
                      message="openpyxl 不可用，无法读取 network.xlsx",
                      severity="error", blocking=True)
        return []
    if not path.exists():
        collector.add(file=path.name, code="FILE_MISSING",
                      message=f"network.xlsx 不存在", severity="error",
                      blocking=True)
        return []
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        collector.add(file=path.name, code="WORKBOOK_UNREADABLE",
                      message=f"无法打开工作簿: {exc}", severity="error",
                      blocking=True)
        return []
    try:
        if sheet not in wb.sheetnames:
            return []
        ws = wb[sheet]
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return []
        cols = [as_str(h) for h in header]
        out: List[Dict[str, Any]] = []
        for r in rows:
            if r is None or all(c is None for c in r):
                continue
            rec = {cols[i]: r[i] for i in range(min(len(cols), len(r)))}
            out.append(rec)
        return out
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _read_meta(path: Path, collector: IssueCollector) -> Dict[str, str]:
    rows = _read_excel_sheet(path, "meta", collector)
    meta: Dict[str, str] = {}
    for idx, row in enumerate(rows, start=2):
        key = as_str(row.get("key"))
        if not key:
            continue
        if key in meta:
            collector.add(file=path.name, sheet="meta", row=idx, field="key",
                          object_id=key, code="META_KEY_DUPLICATE",
                          message=f"meta 键 {key} 重复（每个 key 唯一）",
                          severity="error", blocking=True)
            continue
        val = row.get("value")
        meta[key] = as_str(val) if val is not None else ""
    return meta


def _normalize_profiles(rows: List[Dict[str, Any]],
                        collector: IssueCollector,
                        file: str = "profiles.csv") -> List[Dict[str, Any]]:
    """规范化时序文件（设计文档 17.8）。

    复合唯一键为 profile_id+timestamp+phase；
    同一设备不能混用 ALL 与分相值而重复累计。
    """
    out: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str, str], int] = {}
    all_and_split: Dict[Tuple[str, str], set] = {}

    for idx, row in enumerate(rows, start=2):
        pid = as_str(row.get("profile_id"))
        rid = as_str(row.get("resource_id"))
        rtype = as_str(row.get("resource_type")).lower()
        ts = as_str(row.get("timestamp"))
        phase = (as_str(row.get("phase")) or "ALL").upper()
        p_kw = as_float(row.get("p_kw"))
        q_kvar = as_float(row.get("q_kvar"))
        avail = as_float(row.get("available_p_pu"))
        qflag = as_str(row.get("quality_flag")) or "valid"
        source = as_str(row.get("source_id"))

        if not pid or not rid or not ts:
            collector.add(file=file, row=idx, field="profile_id/resource_id/timestamp",
                          code="SCHEMA_MISSING_FIELD",
                          message="profile_id、resource_id、timestamp 必填",
                          severity="error", blocking=True)
            continue
        if not ts.endswith(("Z",)) and ("+" not in ts[10:]):
            collector.add(file=file, row=idx, field="timestamp", object_id=pid,
                          code="TIMEZONE_REQUIRED",
                          message=f"时间 {ts} 必须带时区",
                          severity="error", blocking=True)
            continue
        if rtype and rtype not in PROFILE_RESOURCE_TYPES:
            collector.add(file=file, row=idx, field="resource_type",
                          object_id=rid, code="SCHEMA_TYPE_MISMATCH",
                          message=f"resource_type={rtype} 未登记",
                          severity="error", blocking=True)
            continue
        if phase not in ("A", "B", "C", "ALL"):
            collector.add(file=file, row=idx, field="phase", object_id=rid,
                          code="PHASE_CONFLICT",
                          message=f"phase={phase} 不合法（A/B/C/ALL）",
                          severity="error", blocking=True)
            continue
        if p_kw is None and avail is None and rtype != "measurement":
            collector.add(file=file, row=idx, field="p_kw/available_p_pu",
                          object_id=rid, code="SCHEMA_MISSING_FIELD",
                          message="p_kw 与 available_p_pu 二选一，且须声明输入模式",
                          severity="error", blocking=True)
            continue
        if p_kw is not None and avail is not None:
            collector.add(file=file, row=idx, field="p_kw",
                          object_id=rid, code="INPUT_MODE_CONFLICT",
                          message="p_kw 与 available_p_pu 不能同时给出，"
                                  "须声明输入模式",
                          severity="error", blocking=True)
            continue

        key = (pid, ts, phase)
        if key in seen:
            collector.add(file=file, row=idx, field="profile_id",
                          object_id=pid, code="DUPLICATE_KEY",
                          message=f"复合唯一键 profile_id+timestamp+phase 重复"
                                  f"（首次在第 {seen[key]} 行）",
                          severity="error", blocking=True)
            continue
        seen[key] = idx

        bucket = all_and_split.setdefault((pid, ts), set())
        bucket.add(phase)
        if "ALL" in bucket and len(bucket) > 1:
            collector.add(file=file, row=idx, field="phase", object_id=rid,
                          code="PHASE_MIX_CONFLICT",
                          message="同一设备同一时刻混用 ALL 与分相值，"
                                  "会重复累计",
                          severity="error", blocking=True)
            continue

        out.append({
            "profile_id": pid, "resource_id": rid, "resource_type": rtype,
            "timestamp": ts, "phase": phase, "p_kw": p_kw, "q_kvar": q_kvar,
            "available_p_pu": avail, "quality_flag": qflag, "source_id": source,
            "v_v": as_float(row.get("v_v")), "i_a": as_float(row.get("i_a")),
            "_row": idx,
        })
    return out


def _normalize_equipment_limits(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        eid = as_str(row.get("equipment_id"))
        if not eid:
            continue
        out.append({
            "equipment_id": eid,
            "limit_type": as_str(row.get("limit_type")),
            "value": as_float(row.get("value")),
            "unit": as_str(row.get("unit")),
            "direction": as_str(row.get("direction")) or "forward",
            "duration_minutes": as_int(row.get("duration_minutes")),
            "rule_ref": as_str(row.get("rule_ref")),
        })
    return out


def load_input_bundle(input_dir, collector: Optional[IssueCollector] = None,
                      *, require_profiles: bool = True) -> InputBundle:
    """读取输入文件包（设计文档 S02 / 表 47）。

    文件与工作表必需性见设计文档表 47。
    """
    root = Path(input_dir).resolve()
    col = collector or IssueCollector()
    bundle = InputBundle(root=root, collector=col)

    # ---- task.json（全部任务必需）
    task_path = root / "task.json"
    task_parsed = False
    if task_path.exists():
        try:
            bundle.task = json.loads(task_path.read_text(encoding="utf-8"))
            bundle.files["task.json"] = str(task_path)
            task_parsed = True
        except Exception as exc:
            col.add(file="task.json", code="JSON_PARSE_FAILED",
                    message=f"task.json 解析失败: {exc}", severity="error",
                    blocking=True)
            bundle.task = {}
    else:
        col.add(file="task.json", code="FILE_MISSING",
                message="task.json 不存在（全部任务必需）", severity="error",
                blocking=True)
    # 用解析成功标志而非 dict 真值来判断是否校验。
    # 早先用 `if bundle.task:` 会在 task.json 内容为 {} 时静默跳过全部字段校验
    # （空 dict 为假），使完全空的任务单被当作合法输入继续执行；同理任何
    # 解析结果为假值的载荷都会绕过校验。必须按"是否成功解析"决定。
    if task_parsed:
        if not isinstance(bundle.task, dict):
            col.add(file="task.json", code="SCHEMA_TYPE_MISMATCH",
                    message="task.json 顶层必须为对象", severity="error",
                    blocking=True)
            bundle.task = {}
        else:
            validate_task(bundle.task, col)

    # ---- network.xlsx（结构化建模必需）
    net_path = root / "network.xlsx"
    bundle.files["network.xlsx"] = str(net_path)
    bundle.meta = _read_meta(net_path, col)
    if bundle.meta:
        validate_meta(bundle.meta, col)
        from hc_agent.io.schema import check_snapshot_in_validity
        check_snapshot_in_validity(bundle.task, bundle.meta, col)

    bundle.buses = _normalize_buses(_read_excel_sheet(net_path, "buses", col), col)
    bundle.lines = _normalize_lines(_read_excel_sheet(net_path, "lines", col), col)
    bundle.transformers = _normalize_transformers(
        _read_excel_sheet(net_path, "transformers", col), col)
    bundle.switches = _normalize_switches(
        _read_excel_sheet(net_path, "switches", col), col)
    bundle.loads = _normalize_loads(_read_excel_sheet(net_path, "loads", col), col)
    bundle.pv = _normalize_pv(_read_excel_sheet(net_path, "pv", col), col)
    bundle.sources = _normalize_sources(
        _read_excel_sheet(net_path, "sources", col), col)

    # ---- profiles.csv / profiles.parquet（时序评估必需）
    prof_csv = root / "profiles.csv"
    prof_parq = root / "profiles.parquet"
    if prof_csv.exists():
        rows = read_csv(prof_csv, collector=col,
                        required_headers=PROFILE_HEADERS)
        bundle.profiles = _normalize_profiles(rows, col, "profiles.csv")
        bundle.files["profiles.csv"] = str(prof_csv)
    elif prof_parq.exists():
        bundle.profiles = _normalize_profiles(
            _read_parquet(prof_parq, col), col, "profiles.parquet")
        bundle.files["profiles.parquet"] = str(prof_parq)
    elif require_profiles:
        col.add(file="profiles.csv", code="FILE_MISSING",
                message="profiles.csv 或 profiles.parquet 不存在（时序评估必需）",
                severity="error", blocking=True)

    # ---- application.json（并网审查必需）
    app_path = root / "application.json"
    if app_path.exists():
        try:
            bundle.application = json.loads(app_path.read_text(encoding="utf-8"))
            validate_application(bundle.application, col)
            bundle.files["application.json"] = str(app_path)
        except Exception as exc:
            col.add(file="application.json", code="JSON_PARSE_FAILED",
                    message=f"application.json 解析失败: {exc}",
                    severity="error", blocking=True)
    else:
        scene = as_str(bundle.task.get("business_scene"))
        if scene in ("access_check", "remediation_review"):
            col.add(file="application.json", code="FILE_MISSING",
                    message=f"业务场景 {scene} 必需 application.json",
                    severity="error", blocking=True)

    # ---- reservations.csv（管理容量必需；无占用时提交表头空表）
    res_path = root / "reservations.csv"
    if res_path.exists():
        rows = read_csv(res_path, collector=col)
        bundle.reservations = validate_reservations(rows, col)
        bundle.files["reservations.csv"] = str(res_path)
    else:
        col.add(file="reservations.csv", code="FILE_MISSING",
                message="reservations.csv 不存在；无占用时须提交表头空表",
                severity="warning", blocking=False)

    # ---- 辅助文件（表 53）
    lim_path = root / "equipment_limits.csv"
    if lim_path.exists():
        bundle.equipment_limits = _normalize_equipment_limits(
            read_csv(lim_path, collector=col))
        bundle.files["equipment_limits.csv"] = str(lim_path)
    else:
        col.add(file="equipment_limits.csv", code="FILE_MISSING",
                message="equipment_limits.csv 不存在；设备边界校验能力受限",
                severity="warning", blocking=False)

    fm_path = root / "fault_models.csv"
    if fm_path.exists():
        bundle.fault_models = read_csv(fm_path, collector=col)
        bundle.files["fault_models.csv"] = str(fm_path)
    else:
        col.add(file="fault_models.csv", code="FILE_MISSING",
                message="fault_models.csv 不存在；短路能力标记为 unknown",
                severity="warning", blocking=False)

    ctrl_path = root / "controls.json"
    if ctrl_path.exists():
        try:
            bundle.controls = json.loads(ctrl_path.read_text(encoding="utf-8"))
            bundle.files["controls.json"] = str(ctrl_path)
        except Exception as exc:
            col.add(file="controls.json", code="JSON_PARSE_FAILED",
                    message=f"controls.json 解析失败: {exc}",
                    severity="warning", blocking=False)

    sc_path = root / "scenario_set.json"
    if sc_path.exists():
        try:
            bundle.scenario_set = json.loads(sc_path.read_text(encoding="utf-8"))
            bundle.files["scenario_set.json"] = str(sc_path)
        except Exception as exc:
            col.add(file="scenario_set.json", code="JSON_PARSE_FAILED",
                    message=f"scenario_set.json 解析失败: {exc}",
                    severity="warning", blocking=False)

    # ---- 原生 DSS 模型（采用原生 DSS 模型时必需）
    for cand in ("Master.dss", "master.dss"):
        p = root / cand
        if p.exists():
            bundle.native_dss = str(p)
            bundle.files["Master.dss"] = str(p)
            break

    return bundle


def _read_parquet(path: Path, collector: IssueCollector) -> List[Dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        collector.add(file=path.name, code="DEPENDENCY_MISSING",
                      message="pandas/pyarrow 不可用", severity="error",
                      blocking=True)
        return []
    try:
        df = pd.read_parquet(path)
        df = df.astype(object).where(df.notna(), None)
        return df.to_dict(orient="records")
    except Exception as exc:
        collector.add(file=path.name, code="PARQUET_UNREADABLE",
                      message=f"读取失败: {exc}", severity="error", blocking=True)
        return []


# ------------------------------------------------ 各工作表规范化（17.6/17.7）
def _norm_common(row: Dict[str, Any]) -> Dict[str, Any]:
    return {as_str(k): v for k, v in row.items() if as_str(k)}


def _normalize_buses(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        nid = as_str(row.get("node_id"))
        if not nid:
            continue
        if nid in seen:
            col.add(file="network.xlsx", sheet="buses", row=idx,
                    field="node_id", object_id=nid, code="DUPLICATE_ID",
                    message=f"node_id {nid} 重复（主键不得重复）",
                    severity="error", blocking=True)
            continue
        seen.add(nid)
        vn = as_float(row.get("vn_kv"))
        if vn is None or vn <= 0:
            col.add(file="network.xlsx", sheet="buses", row=idx,
                    field="vn_kv", object_id=nid, code="RATING_INVALID",
                    message="vn_kv 必须为正数值", severity="error", blocking=True)
            continue
        phase = as_str(row.get("phase")) or "ABC"
        if phase not in ("A", "B", "C", "AB", "BC", "CA", "ABC"):
            col.add(file="network.xlsx", sheet="buses", row=idx, field="phase",
                    object_id=nid, code="PHASE_CONFLICT",
                    message=f"phase={phase} 不合法", severity="error",
                    blocking=True)
            continue
        out.append({
            "node_id": nid, "name": as_str(row.get("name")),
            "feeder_id": as_str(row.get("feeder_id")),
            "dt_id": as_str(row.get("dt_id")),
            "vn_kv": vn, "phase": phase,
            "connection": as_str(row.get("connection")) or "wye",
            "neutral_model": as_str(row.get("neutral_model")),
            "_row": idx,
        })
    return out


def _normalize_lines(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        lid = as_str(row.get("line_id"))
        if not lid:
            continue
        if lid in seen:
            col.add(file="network.xlsx", sheet="lines", row=idx,
                    field="line_id", object_id=lid, code="DUPLICATE_ID",
                    message=f"line_id {lid} 重复", severity="error", blocking=True)
            continue
        seen.add(lid)
        f, t = as_str(row.get("from_node")), as_str(row.get("to_node"))
        if not f or not t:
            col.add(file="network.xlsx", sheet="lines", row=idx,
                    field="from_node/to_node", object_id=lid,
                    code="SCHEMA_MISSING_FIELD",
                    message="from_node 与 to_node 必填", severity="error",
                    blocking=True)
            continue
        if f == t:
            col.add(file="network.xlsx", sheet="lines", row=idx,
                    field="from_node", object_id=lid, code="SELF_LOOP",
                    message="起止节点不能相同", severity="error", blocking=True)
            continue
        length = as_float(row.get("length_km"))
        if length is None or length <= 0:
            col.add(file="network.xlsx", sheet="lines", row=idx,
                    field="length_km", object_id=lid, code="RATING_INVALID",
                    message="length_km 必须为正（不得把全长阻抗填作单位长度阻抗）",
                    severity="error", blocking=True)
            continue
        r1 = as_float(row.get("r1_ohm_per_km"))
        x1 = as_float(row.get("x1_ohm_per_km"))
        if r1 is None or x1 is None:
            col.add(file="network.xlsx", sheet="lines", row=idx,
                    field="r1_ohm_per_km/x1_ohm_per_km", object_id=lid,
                    code="SCHEMA_MISSING_FIELD",
                    message="正序参数必填", severity="error", blocking=True)
            continue
        out.append({
            "line_id": lid, "from_node": f, "to_node": t,
            "phase": as_str(row.get("phase")) or "ABC",
            "length_km": length,
            "r1_ohm_per_km": r1, "x1_ohm_per_km": x1,
            "r0_ohm_per_km": as_float(row.get("r0_ohm_per_km")),
            "x0_ohm_per_km": as_float(row.get("x0_ohm_per_km")),
            "c1_nf_per_km": as_float(row.get("c1_nf_per_km")),
            "c0_nf_per_km": as_float(row.get("c0_nf_per_km")),
            "max_i_a": as_float(row.get("max_i_a")),
            "in_service": as_bool(row.get("in_service"), True),
            "phase_matrix_ref": as_str(row.get("phase_matrix_ref")),
            "_row": idx,
        })
    return out


def _normalize_transformers(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        tid = as_str(row.get("transformer_id"))
        if not tid:
            continue
        if tid in seen:
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="transformer_id", object_id=tid, code="DUPLICATE_ID",
                    message=f"transformer_id {tid} 重复", severity="error",
                    blocking=True)
            continue
        seen.add(tid)
        hv, lv = as_str(row.get("hv_node")), as_str(row.get("lv_node"))
        if not hv or not lv:
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="hv_node/lv_node", object_id=tid,
                    code="SCHEMA_MISSING_FIELD",
                    message="hv_node 与 lv_node 必填", severity="error",
                    blocking=True)
            continue
        sn = as_float(row.get("sn_kva"))
        vh, vl = as_float(row.get("vn_hv_kv")), as_float(row.get("vn_lv_kv"))
        if not sn or sn <= 0 or not vh or not vl:
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="sn_kva/vn_hv_kv/vn_lv_kv", object_id=tid,
                    code="RATING_INVALID",
                    message="额定容量与高低压额定线电压必须为正",
                    severity="error", blocking=True)
            continue
        vk = as_float(row.get("vk_percent"))
        vkr = as_float(row.get("vkr_percent"))
        if vk is None or vkr is None:
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="vk_percent/vkr_percent", object_id=tid,
                    code="SCHEMA_MISSING_FIELD",
                    message="短路阻抗与电阻分量必填", severity="error",
                    blocking=True)
            continue
        if not (0 <= vkr <= vk):
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="vkr_percent", object_id=tid,
                    code="RATING_INVALID",
                    message=f"必须满足 0≤vkr≤vk（当前 vkr={vkr}, vk={vk}）",
                    severity="error", blocking=True)
            continue
        vg = as_str(row.get("vector_group"))
        if not vg:
            col.add(file="network.xlsx", sheet="transformers", row=idx,
                    field="vector_group", object_id=tid,
                    code="SCHEMA_MISSING_FIELD",
                    message="接线组别不能默认为 Dyn11，必须显式给出",
                    severity="error", blocking=True)
            continue
        out.append({
            "transformer_id": tid, "hv_node": hv, "lv_node": lv,
            "sn_kva": sn, "vn_hv_kv": vh, "vn_lv_kv": vl,
            "vk_percent": vk, "vkr_percent": vkr,
            "pfe_kw": as_float(row.get("pfe_kw")),
            "i0_percent": as_float(row.get("i0_percent")),
            "vector_group": vg,
            "phase": as_str(row.get("phase")) or "ABC",
            "tap_side": as_str(row.get("tap_side")) or "lv",
            "tap_pos": as_int(row.get("tap_pos")) or 0,
            "tap_neutral": as_int(row.get("tap_neutral")) or 0,
            "tap_step_percent": as_float(row.get("tap_step_percent")),
            "zero_sequence_ref": as_str(row.get("zero_sequence_ref")),
            "in_service": as_bool(row.get("in_service"), True),
            "_row": idx,
        })
    return out


def _normalize_switches(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out = []
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        sid = as_str(row.get("switch_id"))
        if not sid:
            continue
        out.append({
            "switch_id": sid,
            "element_id": as_str(row.get("element_id")),
            "terminal": as_int(row.get("terminal")) or 1,
            "closed": as_bool(row.get("closed"), True),
            "rated_i_a": as_float(row.get("rated_i_a")),
            "valid_time": as_str(row.get("valid_time")),
            "phase": as_str(row.get("phase")) or "ABC",
            "_row": idx,
        })
    return out


def _normalize_loads(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        lid = as_str(row.get("load_id"))
        if not lid:
            continue
        if lid in seen:
            col.add(file="network.xlsx", sheet="loads", row=idx, field="load_id",
                    object_id=lid, code="DUPLICATE_ID", message=f"{lid} 重复",
                    severity="error", blocking=True)
            continue
        seen.add(lid)
        out.append({
            "load_id": lid, "node_id": as_str(row.get("node_id")),
            "phase": as_str(row.get("phase")) or "ABC",
            "connection": as_str(row.get("connection")) or "wye",
            "profile_id": as_str(row.get("profile_id")),
            "model_type": as_str(row.get("model_type")) or "constant_pq",
            "in_service": as_bool(row.get("in_service"), True),
            "_row": idx,
        })
    return out


def _normalize_pv(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        pid = as_str(row.get("pv_id"))
        if not pid:
            continue
        if pid in seen:
            col.add(file="network.xlsx", sheet="pv", row=idx, field="pv_id",
                    object_id=pid, code="DUPLICATE_ID", message=f"{pid} 重复",
                    severity="error", blocking=True)
            continue
        seen.add(pid)
        rp = as_float(row.get("rated_p_kw"))
        rs = as_float(row.get("rated_s_kva"))
        if rp is None and rs is None:
            col.add(file="network.xlsx", sheet="pv", row=idx,
                    field="rated_p_kw/rated_s_kva", object_id=pid,
                    code="SCHEMA_MISSING_FIELD",
                    message="额定有功或视在容量至少给出一项",
                    severity="error", blocking=True)
            continue
        cm = as_str(row.get("control_mode")) or "fixed_pf"
        if cm not in ("fixed_pf", "fixed_q", "volt_var", "volt_watt", "unity_pf"):
            col.add(file="network.xlsx", sheet="pv", row=idx,
                    field="control_mode", object_id=pid,
                    code="SCHEMA_TYPE_MISMATCH",
                    message=f"control_mode={cm} 未登记", severity="error",
                    blocking=True)
            continue
        if cm in ("volt_var", "volt_watt") and not as_str(row.get("control_ref")):
            col.add(file="network.xlsx", sheet="pv", row=idx,
                    field="control_ref", object_id=pid,
                    code="SCHEMA_MISSING_FIELD",
                    message="volt_var 等控制必须提供控制曲线、死区和动作设置",
                    severity="error", blocking=True)
            continue
        out.append({
            "pv_id": pid, "node_id": as_str(row.get("node_id")),
            "phase": as_str(row.get("phase")) or "ABC",
            "connection": as_str(row.get("connection")) or "wye",
            "rated_p_kw": rp, "rated_s_kva": rs,
            "profile_id": as_str(row.get("profile_id")),
            "control_mode": cm,
            "control_ref": as_str(row.get("control_ref")),
            "fault_model_ref": as_str(row.get("fault_model_ref")),
            "in_service": as_bool(row.get("in_service"), True),
            "_row": idx,
        })
    return out


def _normalize_sources(rows, col: IssueCollector) -> List[Dict[str, Any]]:
    out = []
    for idx, raw in enumerate(rows, start=2):
        row = _norm_common(raw)
        sid = as_str(row.get("source_id"))
        if not sid:
            continue
        vm = as_float(row.get("vm_pu"))
        r1 = as_float(row.get("r1_ohm"))
        x1 = as_float(row.get("x1_ohm"))
        if not as_str(row.get("node_id")) or vm is None:
            col.add(file="network.xlsx", sheet="sources", row=idx,
                    field="node_id/vm_pu", object_id=sid,
                    code="SCHEMA_MISSING_FIELD",
                    message="上级电源 node_id 与 vm_pu 必填", severity="error",
                    blocking=True)
            continue
        if r1 is None and x1 is None:
            col.add(file="network.xlsx", sheet="sources", row=idx,
                    field="r1_ohm/x1_ohm", object_id=sid,
                    code="SCHEMA_MISSING_FIELD",
                    message="上级电源阻抗必填（按最大/最小运行方式分别提供）",
                    severity="error", blocking=True)
            continue
        out.append({
            "source_id": sid, "node_id": as_str(row.get("node_id")),
            "vm_pu": vm, "angle_deg": as_float(row.get("angle_deg")) or 0.0,
            "r1_ohm": r1, "x1_ohm": x1,
            "r0_ohm": as_float(row.get("r0_ohm")),
            "x0_ohm": as_float(row.get("x0_ohm")),
            "source_case_id": as_str(row.get("source_case_id")),
            "z2_assumption_ref": as_str(row.get("z2_assumption_ref")),
            "_row": idx,
        })
    if not out:
        col.add(file="network.xlsx", sheet="sources", code="SCHEMA_MISSING_FIELD",
                message="sources 工作表必须给出上级电源等值（电源必需）",
                severity="error", blocking=True)
    return out
