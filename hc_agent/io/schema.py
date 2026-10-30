"""schema.py — 字段契约与校验（设计文档 17.4 / 17.5 / 表 48-53）。

统一约定（17.4）：
  - CSV 为 UTF-8 编码、逗号分隔、首行为表头；XLSX 第 1 行为表头，
    第 2 行起为记录，不合并单元格，不含宏。
  - ID 全部按字符串读取并保留前导零。
  - 数值单元格不得写单位或千位分隔符；单位由字段后缀明确。
  - 缺失值用空单元格或 JSON null，不用 0、横线或"暂无"代替。
  - 布尔值统一为 true/false。
  - 时间使用 ISO 8601 且带时区；窗口采用 [start,end) 半开区间。
  - 功率契约：负荷 P/Q 为消费方向正值，光伏 P 为发出方向正值、
    Q 为向网注入正值；适配器负责转换为引擎符号，禁止混用。
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hc_agent.errors import ERR_SCHEMA_MISSING_FIELD, ERR_SCHEMA_TYPE_MISMATCH

ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)
NULLISH = {"", "-", "--", "null", "none", "nan", "暂无", "n/a", "na", "\\n"}

# ------------------------------------------------- task.json 契约（表 48）
TASK_REQUIRED = ("schema_version", "task_id", "business_scene", "scope",
                 "time_window", "snapshot_id", "rule_package", "engine",
                 "analysis_types")

BUSINESS_SCENES = ("capacity_publication", "access_check",
                   "capacity_assessment", "remediation_review",
                   "research_coordination")

SCOPE_TYPES = ("dt", "feeder", "substation", "region", "batch")

ANALYSIS_TYPES = ("powerflow_3ph", "powerflow_1ph", "shortcircuit",
                  "timeseries", "imbalance", "thermal")

PHASE_ENUM = ("A", "B", "C", "AB", "BC", "CA", "ABC", "ALL")

SCHEMA_VERSION_INPUT = "hc-input-v1"
SCHEMA_VERSION_RULES = "hc-rules-v1"
SCHEMA_VERSION_RESEARCH = "hc-research-v1"


class Issue:
    """一条质量问题（设计文档 表 56 quality_issues.csv）。"""

    __slots__ = ("issue_id", "file", "sheet", "row", "field", "object_id",
                 "severity", "code", "message", "blocking")

    def __init__(self, *, file: str, code: str, message: str,
                 severity: str = "warning", blocking: bool = False,
                 sheet: str = "", row: Optional[int] = None, field: str = "",
                 object_id: str = "") -> None:
        self.file = file
        self.sheet = sheet
        self.row = row
        self.field = field
        self.object_id = object_id
        self.severity = severity
        self.code = code
        self.message = message
        self.blocking = blocking
        self.issue_id = ""

    def to_row(self, idx: int) -> Dict[str, Any]:
        self.issue_id = f"QI-{idx:05d}"
        return {
            "issue_id": self.issue_id, "file": self.file, "sheet": self.sheet,
            "row": self.row if self.row is not None else "",
            "field": self.field, "object_id": self.object_id,
            "severity": self.severity, "code": self.code,
            "message": self.message, "blocking": str(self.blocking).lower(),
        }


class IssueCollector:
    def __init__(self) -> None:
        self.issues: List[Issue] = []

    def add(self, **kw) -> Issue:
        iss = Issue(**kw)
        self.issues.append(iss)
        return iss

    @property
    def blocking(self) -> List[Issue]:
        return [i for i in self.issues if i.blocking]

    def write(self, path) -> str:
        rows = [i.to_row(n + 1) for n, i in enumerate(self.issues)]
        cols = ["issue_id", "file", "sheet", "row", "field", "object_id",
                "severity", "code", "message", "blocking"]
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        return str(path)


# --------------------------------------------------------- 基础类型工具
def is_nullish(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and value != value:
        return True
    return str(value).strip().lower() in NULLISH


def as_str(value: Any, default: str = "") -> str:
    """ID 全部按字符串读取并保留前导零（17.4）。"""
    if is_nullish(value):
        return default
    if isinstance(value, float) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def as_float(value: Any) -> Optional[float]:
    """数值读取：空值返回 None，不返回 0（17.13：空值与零不同）。"""
    if is_nullish(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def as_int(value: Any) -> Optional[int]:
    f = as_float(value)
    return None if f is None else int(round(f))


def as_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    """布尔值统一为 true/false（17.4）。"""
    if is_nullish(value):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "t", "closed", "on", "是"):
        return True
    if text in ("false", "0", "no", "n", "f", "open", "off", "否"):
        return False
    return default


def parse_iso(value: Any) -> Optional[datetime]:
    """ISO 8601 且带时区（17.4）。"""
    if is_nullish(value):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def has_timezone(value: Any) -> bool:
    dt = parse_iso(value)
    return bool(dt is not None and dt.tzinfo is not None)


def validate_schema_version(doc: Dict[str, Any], expected: str, *,
                            file: str, collector: IssueCollector,
                            field: str = "schema_version") -> bool:
    got = as_str(doc.get(field))
    if not got:
        collector.add(file=file, field=field, code=ERR_SCHEMA_MISSING_FIELD,
                      message=f"缺少 {field}", severity="error", blocking=True)
        return False
    if got != expected:
        collector.add(file=file, field=field, code="SCHEMA_VERSION_UNEXPECTED",
                      message=f"{field}={got} 与期望 {expected} 不一致",
                      severity="warning", blocking=False)
        return False
    return True


def check_required(doc: Dict[str, Any], required: Sequence[str], *,
                   file: str, collector: IssueCollector,
                   prefix: str = "") -> List[str]:
    missing = []
    for key in required:
        if key not in doc or is_nullish(doc.get(key)):
            missing.append(key)
            collector.add(file=file, field=f"{prefix}{key}",
                          code=ERR_SCHEMA_MISSING_FIELD,
                          message=f"必需字段 {prefix}{key} 缺失",
                          severity="error", blocking=True)
    return missing


def check_enum(doc: Dict[str, Any], field: str, allowed: Sequence[str], *,
               file: str, collector: IssueCollector, blocking: bool = True,
               object_id: str = "") -> bool:
    val = as_str(doc.get(field))
    if not val:
        return False
    if val not in allowed:
        collector.add(file=file, field=field, object_id=object_id,
                      code=ERR_SCHEMA_TYPE_MISMATCH,
                      message=f"{field}={val} 不在允许集合 {list(allowed)} 内",
                      severity="error" if blocking else "warning",
                      blocking=blocking)
        return False
    return True


def validate_task(task: Dict[str, Any], collector: IssueCollector,
                  file: str = "task.json") -> Dict[str, Any]:
    """校验 task.json（设计文档 表 48）。"""
    check_required(task, TASK_REQUIRED, file=file, collector=collector)
    validate_schema_version(task, SCHEMA_VERSION_INPUT, file=file,
                            collector=collector)
    check_enum(task, "business_scene", BUSINESS_SCENES, file=file,
               collector=collector)

    scope = task.get("scope") or {}
    if isinstance(scope, dict):
        check_enum(scope, "type", SCOPE_TYPES, file=file, collector=collector)
        ids = scope.get("ids")
        if not ids or not isinstance(ids, (list, tuple)) or len(ids) == 0:
            collector.add(file=file, field="scope.ids",
                          code=ERR_SCHEMA_MISSING_FIELD,
                          message="scope.ids 必须为非空数组（必须在模型中存在）",
                          severity="error", blocking=True)
    else:
        collector.add(file=file, field="scope", code=ERR_SCHEMA_TYPE_MISMATCH,
                      message="scope 必须为对象", severity="error",
                      blocking=True)

    tw = task.get("time_window") or {}
    if isinstance(tw, dict):
        start = parse_iso(tw.get("start"))
        end = parse_iso(tw.get("end"))
        if start is None or end is None:
            collector.add(file=file, field="time_window",
                          code=ERR_SCHEMA_MISSING_FIELD,
                          message="time_window.start/end 必须为带时区 ISO 8601",
                          severity="error", blocking=True)
        else:
            if not has_timezone(tw.get("start")) or not has_timezone(tw.get("end")):
                collector.add(file=file, field="time_window",
                              code="TIMEZONE_REQUIRED",
                              message="时间必须带时区，例如 2026-07-01T00:00:00+08:00",
                              severity="error", blocking=True)
            if end <= start:
                collector.add(file=file, field="time_window",
                              code=ERR_SCHEMA_TYPE_MISMATCH,
                              message="time_window.end 必须大于 start（[start,end) 半开区间）",
                              severity="error", blocking=True)
    else:
        collector.add(file=file, field="time_window",
                      code=ERR_SCHEMA_MISSING_FIELD,
                      message="time_window 必须为对象", severity="error",
                      blocking=True)

    atypes = task.get("analysis_types") or []
    if isinstance(atypes, (list, tuple)):
        for a in atypes:
            if as_str(a) not in ANALYSIS_TYPES:
                collector.add(file=file, field="analysis_types",
                              code=ERR_SCHEMA_TYPE_MISMATCH,
                              message=f"未支持的 analysis_type={a}；"
                                      f"允许 {list(ANALYSIS_TYPES)}",
                              severity="error", blocking=True)
    else:
        collector.add(file=file, field="analysis_types",
                      code=ERR_SCHEMA_TYPE_MISMATCH,
                      message="analysis_types 必须为数组", severity="error",
                      blocking=True)

    engine = task.get("engine") or {}
    if isinstance(engine, dict):
        eid = as_str(engine.get("id"))
        if eid and eid.lower() != "opendss":
            collector.add(file=file, field="engine.id",
                          code="ENGINE_UNSUPPORTED",
                          message=f"本系统唯一电气计算引擎为 OpenDSS，收到 {eid}",
                          severity="error", blocking=True)

    rule_pkg = as_str(task.get("rule_package"))
    if rule_pkg and "approved" in rule_pkg and "@" in rule_pkg:
        collector.add(file=file, field="rule_package",
                      code="RULE_FLOATING_ALIAS",
                      message="执行时不得保留 approved 浮动别名，"
                              "须固定为具体版本号（表 48）",
                      severity="error", blocking=True)

    nodes = task.get("candidate_nodes") or []
    if isinstance(nodes, (list, tuple)) and nodes:
        total_w = 0.0
        for n in nodes:
            if not isinstance(n, dict):
                continue
            total_w += as_float(n.get("weight")) or 0.0
            ph = as_str(n.get("phase"))
            if ph and ph not in PHASE_ENUM:
                collector.add(file=file, field="candidate_nodes.phase",
                              object_id=as_str(n.get("node_id")),
                              code=ERR_SCHEMA_TYPE_MISMATCH,
                              message=f"相别 {ph} 不在 {list(PHASE_ENUM)}",
                              severity="error", blocking=True)
        if nodes and abs(total_w - 1.0) > 1e-6:
            collector.add(file=file, field="candidate_nodes.weight",
                          code="WEIGHT_SUM_INVALID",
                          message=f"weight 之和必须为 1（当前 {total_w:.6f}）",
                          severity="error", blocking=True)

    search = task.get("search") or {}
    if isinstance(search, dict) and search:
        lo = as_float(search.get("lower_kw"))
        hi = as_float(search.get("upper_kw"))
        pr = as_float(search.get("precision_kw"))
        if lo is not None and hi is not None and hi <= lo:
            collector.add(file=file, field="search",
                          code=ERR_SCHEMA_TYPE_MISMATCH,
                          message="search.upper_kw 必须大于 lower_kw",
                          severity="error", blocking=True)
        if pr is not None and pr <= 0:
            collector.add(file=file, field="search.precision_kw",
                          code=ERR_SCHEMA_TYPE_MISMATCH,
                          message="precision_kw 必须为正",
                          severity="error", blocking=True)
    return task


def validate_meta(meta: Dict[str, str], collector: IssueCollector,
                  file: str = "network.xlsx:meta") -> Dict[str, str]:
    """network.xlsx 的 meta 工作表（设计文档 17.5）。

    必需键：schema_version、snapshot_id、topology_version、asset_version、
    valid_from、valid_to、base_frequency_hz、timezone、source_system。
    """
    required = ("schema_version", "snapshot_id", "topology_version",
                "asset_version", "valid_from", "valid_to", "base_frequency_hz",
                "timezone", "source_system")
    for key in required:
        if key not in meta or is_nullish(meta.get(key)):
            collector.add(file=file, field=key, code=ERR_SCHEMA_MISSING_FIELD,
                          message=f"meta 必需键 {key} 缺失", severity="error",
                          blocking=True)
    vf, vt = parse_iso(meta.get("valid_from")), parse_iso(meta.get("valid_to"))
    if vf and vt and vt <= vf:
        collector.add(file=file, field="valid_to", code=ERR_SCHEMA_TYPE_MISMATCH,
                      message="meta.valid_to 必须晚于 valid_from",
                      severity="error", blocking=True)
    return meta


def check_snapshot_in_validity(task: Dict[str, Any], meta: Dict[str, str],
                               collector: IssueCollector,
                               file: str = "network.xlsx:meta") -> None:
    """运行快照必须落在模型有效区间内（设计文档 17.5）。"""
    tw = task.get("time_window") or {}
    start = parse_iso(tw.get("start"))
    vf, vt = parse_iso(meta.get("valid_from")), parse_iso(meta.get("valid_to"))
    if start is None or vf is None or vt is None:
        return
    if not (vf <= start < vt):
        collector.add(file=file, field="valid_from/valid_to",
                      code=ERR_SCHEMA_TYPE_MISMATCH,
                      message=f"运行快照 {start.isoformat()} 不在模型有效区间 "
                              f"[{vf.isoformat()}, {vt.isoformat()}) 内",
                      severity="error", blocking=True)


def validate_reservations(rows: List[Dict[str, Any]],
                          collector: IssueCollector,
                          file: str = "reservations.csv") -> List[Dict[str, Any]]:
    """校验占用台账（设计文档 表 52 / 17.7）。

    已并网光伏只进入基线一次，已受理未并网容量进入占用台账，
    不得同时作为已并网源和待占用源重复扣减。
    跨层级同笔占用不得重复叠加。
    """
    out: List[Dict[str, Any]] = []
    seen: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for idx, row in enumerate(rows, start=2):
        rid = as_str(row.get("reservation_id"))
        app = as_str(row.get("application_id"))
        obj = as_str(row.get("object_id"))
        cap = as_float(row.get("capacity_kw"))
        status = as_str(row.get("status")).lower() or "active"
        if not rid or not obj:
            collector.add(file=file, row=idx, field="reservation_id/object_id",
                          code=ERR_SCHEMA_MISSING_FIELD,
                          message="reservation_id 与 object_id 必填",
                          severity="error", blocking=True)
            continue
        if cap is None or cap < 0:
            collector.add(file=file, row=idx, field="capacity_kw",
                          object_id=rid, code=ERR_SCHEMA_TYPE_MISMATCH,
                          message="capacity_kw 必须为非负数值",
                          severity="error", blocking=True)
            continue
        key = (rid, obj)
        if key in seen:
            collector.add(file=file, row=idx, field="reservation_id",
                          object_id=rid, code="RESERVATION_DUPLICATE",
                          message=f"同一 reservation_id={rid} 在 object_id={obj} "
                                  f"重复登记，跨层级同笔占用不得重复叠加",
                          severity="error", blocking=True)
            continue
        seen[key] = row
        if status not in ("active", "released", "connected"):
            collector.add(file=file, row=idx, field="status", object_id=rid,
                          code=ERR_SCHEMA_TYPE_MISMATCH,
                          message=f"status={status} 不合法（active/released/connected）",
                          severity="error", blocking=True)
            continue
        if status != "active":
            continue
        if app:
            dup = [r for r in out if as_str(r.get("application_id")) == app
                   and as_str(r.get("object_id")) == obj]
            if dup:
                collector.add(file=file, row=idx, field="application_id",
                              object_id=app, code="RESERVATION_DUPLICATE",
                              message=f"申请 {app} 在同一对象 {obj} 重复占用",
                              severity="error", blocking=True)
                continue
        out.append({
            "reservation_id": rid, "application_id": app, "object_id": obj,
            "capacity_kw": cap, "status": status,
            "valid_from": as_str(row.get("valid_from")),
            "valid_to": as_str(row.get("valid_to")),
            "allocation_ref": as_str(row.get("allocation_ref")),
            "hierarchy_path": as_str(row.get("hierarchy_path")),
        })
    return out


def validate_application(app: Dict[str, Any], collector: IssueCollector,
                         file: str = "application.json") -> Dict[str, Any]:
    """校验 application.json（设计文档 表 52）。"""
    required = ("application_id", "version", "generation_type",
                "requested_capacity_kw", "connection_voltage_kv",
                "candidate_node_ids")
    check_required(app, required, file=file, collector=collector)
    cap = as_float(app.get("requested_capacity_kw"))
    if cap is not None and cap <= 0:
        collector.add(file=file, field="requested_capacity_kw",
                      code=ERR_SCHEMA_TYPE_MISMATCH,
                      message="申请容量必须为正", severity="error", blocking=True)
    nodes = app.get("candidate_node_ids")
    if nodes is not None and (not isinstance(nodes, (list, tuple) )
                              or len(nodes) == 0):
        collector.add(file=file, field="candidate_node_ids",
                      code=ERR_SCHEMA_MISSING_FIELD,
                      message="candidate_node_ids 必须为非空数组",
                      severity="error", blocking=True)
    cm = as_str(app.get("control_mode"))
    if cm and cm not in ("fixed_pf", "fixed_q", "volt_var", "volt_watt",
                         "unity_pf"):
        collector.add(file=file, field="control_mode",
                      code=ERR_SCHEMA_TYPE_MISMATCH,
                      message=f"control_mode={cm} 未登记",
                      severity="error", blocking=True)
    return app


def read_csv(path, *, collector: Optional[IssueCollector] = None,
             required_headers: Sequence[str] = ()) -> List[Dict[str, str]]:
    """读取 CSV（UTF-8、逗号分隔、首行为表头）。"""
    p = Path(path)
    if not p.exists():
        if collector is not None:
            collector.add(file=p.name, code="FILE_MISSING",
                          message=f"必需文件 {p.name} 不存在",
                          severity="error", blocking=True)
        return []
    with open(p, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if collector is not None:
            for h in required_headers:
                if h not in headers:
                    collector.add(file=p.name, field=h, code="HEADER_MISSING",
                                  message=f"必需表头 {h} 缺失",
                                  severity="error", blocking=True)
        return [dict(r) for r in reader]


def write_csv(path, rows: Iterable[Dict[str, Any]],
              columns: Optional[Sequence[str]] = None) -> str:
    rows = list(rows)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        cols: List[str] = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        columns = cols
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _csv_val(r.get(k)) for k in columns})
    return str(p)


def _csv_val(v: Any) -> Any:
    if v is None:
        return ""  # 缺失值用空单元格，不用 0 或横线（17.4）
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if v != v:
            return ""
        return f"{v:.10g}"
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v
