"""生成 16 节点承载力验收算例 FD016 的输入包。

用法：
    python tools/build_inputs_fd016.py <目标目录>

**输入包必须放在运行输出目录之外**：Pipeline 的 S01 会把
`out_dir/_stage` 清空后作为只读快照，把输入直接放在那里会被自己清掉
（实测踩过：输入包连同生成脚本一起被删）。

拓扑（16 节点 = 7 MV + 9 LV，3 个台区）：
  SRC ─ MV-001 ─ MV-002 ─ MV-003 ─ MV-004 ─┬─ MV-005 ─(DT-002)→ LV-002 台区2
                │                            └─ MV-006 ─ MV-007 ─(DT-003)→ LV-003 台区3
                └─(DT-001)→ LV-001 台区1

候选接入点 3 个：LV-001B(0.4) / LV-002B(0.4) / LV-003B(0.2)
"""
import csv
import json
import math
import sys
from pathlib import Path

from openpyxl import Workbook

HERE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "fd016_inputs"
HERE.mkdir(parents=True, exist_ok=True)

CFG = {
    "snapshot_id": "SNAP-016",
    "topology_version": "TOPO-016",
    "load": {"L001": 110.0, "L002": 110.0, "L003": 55.0, "L004": 260.0},
    "pv": {"PV001": 60.0, "PV002": 60.0, "PV003": 160.0},
    "lv_seg_km": 0.12,
    "mv_seg_km": 1.2,
    "upper_kw": 2000,
    "precision_kw": 10,
    "candidates": [("BUS-LV-001B", 0.4), ("BUS-LV-002B", 0.4),
                   ("BUS-LV-003B", 0.2)],
}

MV = ["BUS-MV-001", "BUS-MV-002", "BUS-MV-003", "BUS-MV-004",
      "BUS-MV-005", "BUS-MV-006", "BUS-MV-007"]
LV = ["BUS-LV-001", "BUS-LV-001A", "BUS-LV-001B",
      "BUS-LV-002", "BUS-LV-002A", "BUS-LV-002B",
      "BUS-LV-003", "BUS-LV-003A", "BUS-LV-003B"]
MV_NAMES = ["10kV母线", "1号杆", "2号杆", "3号杆", "分支A末端", "4号杆", "分支B末端"]
LV_DT = {b: ("DT-001" if b.startswith("BUS-LV-001") else
             "DT-002" if b.startswith("BUS-LV-002") else "DT-003") for b in LV}
LV_NAMES = {}
for t in ("001", "002", "003"):
    LV_NAMES[f"BUS-LV-{t}"] = f"台区{int(t)}低压母线"
    LV_NAMES[f"BUS-LV-{t}A"] = f"台区{int(t)}中段"
    LV_NAMES[f"BUS-LV-{t}B"] = f"台区{int(t)}末端"


def build_network():
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, header, rows):
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(list(r))

    sheet("meta", ["key", "value"], [
        ("schema_version", "hc-input-v1"),
        ("snapshot_id", CFG["snapshot_id"]),
        ("topology_version", CFG["topology_version"]),
        ("asset_version", "ASSET-016"),
        ("valid_from", "2026-06-01T00:00:00+08:00"),
        ("valid_to", "2026-12-31T00:00:00+08:00"),
        ("base_frequency_hz", 50),
        ("timezone", "+08:00"),
        ("source_system", "BENCH-016"),
        ("dictionary_version", "DICT-002"),
    ])

    sheet("buses",
          ["node_id", "name", "feeder_id", "dt_id", "vn_kv", "phase",
           "connection", "neutral_model"],
          [(b, MV_NAMES[i], "FD-016", None, 10, "ABC", "wye", None)
           for i, b in enumerate(MV)] +
          [(b, LV_NAMES[b], "FD-016", LV_DT[b], 0.4, "ABC", "wye", "TN-C-S")
           for b in LV])

    lines = []

    def mv(lid, a, b, km):
        lines.append((lid, a, b, "ABC", km, 0.17, 0.34, 0.51, 1.02,
                      10.0, 5.0, 400, True, None))

    L = CFG["mv_seg_km"]
    mv("LN-MV-001", "BUS-MV-001", "BUS-MV-002", L)
    mv("LN-MV-002", "BUS-MV-002", "BUS-MV-003", L)
    mv("LN-MV-003", "BUS-MV-003", "BUS-MV-004", L)
    mv("LN-MV-004", "BUS-MV-004", "BUS-MV-005", L)
    mv("LN-MV-005", "BUS-MV-004", "BUS-MV-006", L)
    mv("LN-MV-006", "BUS-MV-006", "BUS-MV-007", L)
    for lid, a, b in (("LN-LV-011", "BUS-LV-001", "BUS-LV-001A"),
                      ("LN-LV-012", "BUS-LV-001A", "BUS-LV-001B"),
                      ("LN-LV-021", "BUS-LV-002", "BUS-LV-002A"),
                      ("LN-LV-022", "BUS-LV-002A", "BUS-LV-002B"),
                      ("LN-LV-031", "BUS-LV-003", "BUS-LV-003A"),
                      ("LN-LV-032", "BUS-LV-003A", "BUS-LV-003B")):
        lines.append((lid, a, b, "ABC", CFG["lv_seg_km"], 0.32, 0.08, 0.96,
                      0.24, 0.0, 0.0, 600, True, None))
    sheet("lines",
          ["line_id", "from_node", "to_node", "phase", "length_km",
           "r1_ohm_per_km", "x1_ohm_per_km", "r0_ohm_per_km",
           "x0_ohm_per_km", "c1_nf_per_km", "c0_nf_per_km", "max_i_a",
           "in_service", "phase_matrix_ref"], lines)

    sheet("transformers",
          ["transformer_id", "hv_node", "lv_node", "sn_kva", "vn_hv_kv",
           "vn_lv_kv", "vk_percent", "vkr_percent", "pfe_kw", "i0_percent",
           "vector_group", "phase", "tap_side", "tap_pos", "tap_neutral",
           "tap_step_percent", "zero_sequence_ref", "in_service"],
          [("DT-001", "BUS-MV-003", "BUS-LV-001", 400, 10, 0.4, 4.0, 1.2,
            0.6, 0.5, "Dyn11", "ABC", "lv", 0, 0, 2.5, "ZS-DT001", True),
           ("DT-002", "BUS-MV-005", "BUS-LV-002", 400, 10, 0.4, 4.0, 1.2,
            0.6, 0.5, "Dyn11", "ABC", "lv", 0, 0, 2.5, "ZS-DT002", True),
           ("DT-003", "BUS-MV-007", "BUS-LV-003", 200, 10, 0.4, 4.0, 1.2,
            0.6, 0.5, "Dyn11", "ABC", "lv", 0, 0, 2.5, "ZS-DT003", True)])

    sheet("switches",
          ["switch_id", "element_id", "terminal", "closed", "rated_i_a",
           "valid_time", "phase"],
          [("SW-001", "LN-MV-002", 1, True, 400,
            "2026-06-01T00:00:00+08:00", "ABC")])

    sheet("loads",
          ["load_id", "node_id", "phase", "connection", "profile_id",
           "model_type", "in_service"],
          [("L001", "BUS-LV-001B", "ABC", "wye", "LP001", "constant_pq", True),
           ("L002", "BUS-LV-002B", "ABC", "wye", "LP002", "constant_pq", True),
           ("L003", "BUS-LV-003B", "ABC", "wye", "LP003", "constant_pq", True),
           ("L004", "BUS-MV-004", "ABC", "wye", "LP004", "constant_pq", True)])

    sheet("pv",
          ["pv_id", "node_id", "phase", "connection", "rated_p_kw",
           "rated_s_kva", "profile_id", "control_mode", "control_ref",
           "fault_model_ref", "in_service"],
          [("PV001", "BUS-LV-001B", "ABC", "wye", CFG["pv"]["PV001"],
            round(CFG["pv"]["PV001"] * 1.1, 2), "PP001", "fixed_pf", None,
            "FM-PV001", True),
           ("PV002", "BUS-LV-002B", "ABC", "wye", CFG["pv"]["PV002"],
            round(CFG["pv"]["PV002"] * 1.1, 2), "PP002", "fixed_pf", None,
            "FM-PV002", True),
           ("PV003", "BUS-MV-006", "ABC", "wye", CFG["pv"]["PV003"],
            round(CFG["pv"]["PV003"] * 1.1, 2), "PP003", "fixed_pf", None,
            "FM-PV003", True)])

    sheet("sources",
          ["source_id", "node_id", "vm_pu", "angle_deg", "r1_ohm", "x1_ohm",
           "r0_ohm", "x0_ohm", "source_case_id", "z2_assumption_ref"],
          [("SRC-001", "BUS-MV-001", 1.02, 0, 0.5, 2.0, 1.5, 6.0,
            "MAX-MODE", "Z2-EQ-R1")])
    wb.save(HERE / "network.xlsx")


def _bell(hour, peak, start=6, end=20, peak_hour=13):
    if hour < start or hour > end:
        return 0.0
    span = (peak_hour - start) if hour <= peak_hour else (end - peak_hour)
    frac = ((hour - start) if hour <= peak_hour else (end - hour)) / (span or 1)
    return round(peak * (math.sin(math.pi * (0.06 + 0.94 * frac) / 2.0) ** 2), 4)


def build_profiles():
    ts = ["2026-07-01T%02d:00:00+08:00" % h for h in range(24)]
    shape_lv = [0.34, 0.33, 0.32, 0.31, 0.32, 0.33, 0.37, 0.49, 0.61, 0.58,
                0.51, 0.46, 0.42, 0.40, 0.43, 0.49, 0.57, 0.72, 0.90, 1.00,
                0.93, 0.78, 0.59, 0.41]
    shape_mv = [0.34, 0.33, 0.32, 0.31, 0.32, 0.34, 0.40, 0.57, 0.77, 0.73,
                0.65, 0.58, 0.55, 0.54, 0.57, 0.62, 0.71, 0.82, 0.94, 1.00,
                0.94, 0.80, 0.61, 0.43]
    header = ["profile_id", "resource_id", "resource_type", "timestamp",
              "phase", "p_kw", "q_kvar", "available_p_pu", "v_v", "i_a",
              "quality_flag", "source_id"]
    rows = []
    for pid, lid, shape, qf, meter in (
            ("LP001", "L001", shape_lv, 0.33, "METER001"),
            ("LP002", "L002", shape_lv, 0.33, "METER002"),
            ("LP003", "L003", shape_lv, 0.33, "METER003"),
            ("LP004", "L004", shape_mv, 0.30, "METER004")):
        peak = CFG["load"][lid]
        for h in range(24):
            p = round(peak * shape[h], 4)
            rows.append([pid, lid, "load", ts[h], "ALL", p,
                         round(p * qf, 4), "", "", "", "valid", meter])
    for pid, pvid, avail, meter in (("PP001", "PV001", 0.85, "PV_METER001"),
                                    ("PP002", "PV002", 0.85, "PV_METER002"),
                                    ("PP003", "PV003", 0.80, "PV_METER003")):
        for h in range(24):
            rows.append([pid, pvid, "pv", ts[h], "ALL", "", "",
                         _bell(h, avail), "", "", "valid", meter])
    with open(HERE / "profiles.csv", "w", encoding="utf-8-sig",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


RULES = """schema_version: hc-rules-v1
rule_package: HC-RULES@1.0
status: approved
approval_ref: APPROVAL-2026-002
effective_from: 2026-06-01T00:00:00+08:00
scope: {region: REGION-001, voltage_levels_kv: [0.4, 10]}
constraints:
  - rule_id: VOLTAGE_LIMIT
    rule_class: voltage
    metric: voltage_pu
    operator: between
    min: 0.90
    max: 1.10
    unit: pu
    duration_minutes: 0
    standard_ref: GB-T-12325
    failure_action: block
  - rule_id: THERMAL_LIMIT
    rule_class: thermal
    metric: loading_pct
    operator: "<="
    max: 100
    unit: pct
    duration_minutes: 0
    standard_ref: DL-T-5222
    failure_action: block
  - rule_id: REVERSE_FLOW_LIMIT
    rule_class: reverse_flow
    metric: reverse_flow_kw
    operator: "<="
    max: 400
    unit: kW
    duration_minutes: 0
    standard_ref: Q-GDW-1484
    failure_action: block
  - rule_id: IMBALANCE_LIMIT
    rule_class: imbalance
    metric: imbalance_pct
    operator: "<="
    max: 2.0
    unit: pct
    duration_minutes: 0
    standard_ref: GB-T-15543
    failure_action: block
  - rule_id: SHORT_CIRCUIT_LIMIT
    rule_class: short_circuit
    metric: fault_current_a
    operator: "<="
    max: 20000
    unit: A
    duration_minutes: 0
    standard_ref: DL-T-5222
    failure_action: block
search:
  allocation_mode: fixed_weights
  monotonicity_required_for_bisection: true
  lower_kw: 0
  upper_kw: __UPPER__
  precision_kw: __PREC__
  max_iter: 25
management:
  formula_id: HC-MGMT-STD
  coincidence_factor: 0.9
  margin_ratio: 0.1
  reservation_policy_ref: RES-POLICY-001
  hierarchy_policy_ref: HIER-POLICY-001
grading:
  red_at_or_below_kw: 0
  yellow_margin_ratio: 0.15
verification:
  metric_tolerances_ref: TOL-001
  voltage_pu_abs: 1.0e-6
  loading_pct_abs: 1.0e-4
  capacity_kw_abs: 1.0e-3
release:
  require_verification: true
  require_professional_review: true
  require_approval: true
scenarios:
  generate_derived: true
  derived: [high_pv_low_load, low_pv_high_load]
"""


def build_rules():
    text = (RULES.replace("__UPPER__", str(CFG["upper_kw"]))
                 .replace("__PREC__", str(CFG["precision_kw"])))
    (HERE / "rules.yaml").write_text(text, encoding="utf-8")


def build_task():
    task = {
        "schema_version": "hc-input-v1",
        "task_id": "TASK-016",
        "business_scene": "capacity_publication",
        "scope": {"type": "feeder", "ids": ["FD-016"]},
        "time_window": {"start": "2026-07-01T00:00:00+08:00",
                        "end": "2026-07-02T00:00:00+08:00"},
        "time_step_minutes": 60,
        "snapshot_id": CFG["snapshot_id"],
        "rule_package": "HC-RULES@1.0",
        "engine": {"id": "opendss", "version": "LOCKED_DSS_VERSION",
                   "interface": "OpenDSSDirect.py",
                   "interface_version": "LOCKED_API_VERSION"},
        "analysis_types": ["powerflow_3ph", "imbalance", "shortcircuit"],
        "candidate_nodes": [{"node_id": n, "phase": "ABC", "weight": w}
                            for n, w in CFG["candidates"]],
        "search": {"lower_kw": 0, "upper_kw": CFG["upper_kw"],
                   "precision_kw": CFG["precision_kw"], "max_iter": 25},
        "solver": {"max_iter": 100, "tolerance": 1e-4, "timeout_s": 120},
        "verification": {"enabled": True, "metric_tolerances_ref": "TOL-001"},
        "allocation_mode": "fixed_weights",
    }
    (HERE / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(name, header, rows):
    with open(HERE / name, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_rest():
    write_csv("reservations.csv",
              ["reservation_id", "application_id", "object_id", "capacity_kw",
               "status", "valid_from", "valid_to", "allocation_ref",
               "hierarchy_path"], [])
    limits = [(lid, "continuous_current", 400) for lid in
              ("LN-MV-001", "LN-MV-002", "LN-MV-003", "LN-MV-004",
               "LN-MV-005", "LN-MV-006")]
    limits += [(lid, "continuous_current", 600) for lid in
               ("LN-LV-011", "LN-LV-012", "LN-LV-021", "LN-LV-022",
                "LN-LV-031", "LN-LV-032")]
    limits += [("DT-001", "continuous_current", 577),
               ("DT-002", "continuous_current", 577),
               ("DT-003", "continuous_current", 289)]
    write_csv("equipment_limits.csv",
              ["equipment_id", "limit_type", "value", "unit", "direction",
               "duration_minutes", "rule_ref"],
              [[a, b, c, "A", "forward", 0, "THERMAL_LIMIT"]
               for a, b, c in limits])
    write_csv("fault_models.csv",
              ["resource_id", "model_type", "parameter_ref",
               "manufacturer_ref", "valid_from", "valid_to"],
              [[p, "inverter_fault_current", f"FM-{p}", "MFG-A",
                "2026-06-01T00:00:00+08:00", "2026-12-31T00:00:00+08:00"]
               for p in ("PV001", "PV002", "PV003")])


if __name__ == "__main__":
    build_network()
    build_profiles()
    build_rules()
    build_task()
    build_rest()
    n_bus = len(MV) + len(LV)
    print(f"built OK -> {HERE}")
    print(f"  节点 {n_bus}（MV {len(MV)} + LV {len(LV)}）、"
          f"线路 12、配变 3、负荷 4、PV 3、接入点 {len(CFG['candidates'])}")
