# -*- coding: utf-8 -*-
"""Build the FD-001 golden-test input package."""
import csv
import json
import os

from openpyxl import Workbook

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- network.xlsx
def build_network():
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, header, rows):
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(list(r))
        return ws

    meta = [
        ("schema_version", "hc-input-v1"),
        ("snapshot_id", "SNAP-001"),
        ("topology_version", "TOPO-001"),
        ("asset_version", "ASSET-001"),
        ("valid_from", "2026-06-01T00:00:00+08:00"),
        ("valid_to", "2026-12-31T00:00:00+08:00"),
        ("base_frequency_hz", 50),
        ("timezone", "+08:00"),
        ("source_system", "GOLDEN-TEST"),
        ("dictionary_version", "DICT-001"),
    ]
    sheet("meta", ["key", "value"], meta)

    sheet(
        "buses",
        ["node_id", "name", "feeder_id", "dt_id", "vn_kv", "phase", "connection", "neutral_model"],
        [
            ("BUS-MV-001", "10kV母线", "FD-001", None, 10, "ABC", "wye", None),
            ("BUS-MV-002", "1号杆", "FD-001", None, 10, "ABC", "wye", None),
            ("BUS-MV-003", "2号杆", "FD-001", None, 10, "ABC", "wye", None),
            ("BUS-LV-001", "台区低压母线", "FD-001", "DT-001", 0.4, "ABC", "wye", "TN-C-S"),
            ("BUS-LV-002", "低压末端", "FD-001", "DT-001", 0.4, "ABC", "wye", "TN-C-S"),
        ],
    )

    sheet(
        "lines",
        ["line_id", "from_node", "to_node", "phase", "length_km",
         "r1_ohm_per_km", "x1_ohm_per_km", "r0_ohm_per_km", "x0_ohm_per_km",
         "c1_nf_per_km", "c0_nf_per_km", "max_i_a", "in_service", "phase_matrix_ref"],
        [
            ("LN-MV-001", "BUS-MV-001", "BUS-MV-002", "ABC", 2.0, 0.17, 0.34, 0.51, 1.02,
             10.0, 5.0, 400, True, None),
            ("LN-MV-002", "BUS-MV-002", "BUS-MV-003", "ABC", 3.0, 0.17, 0.34, 0.51, 1.02,
             10.0, 5.0, 400, True, None),
            ("LN-LV-001", "BUS-LV-001", "BUS-LV-002", "ABC", 0.15, 0.32, 0.08, 0.96, 0.24,
             0.0, 0.0, 250, True, None),
        ],
    )

    sheet(
        "transformers",
        ["transformer_id", "hv_node", "lv_node", "sn_kva", "vn_hv_kv", "vn_lv_kv",
         "vk_percent", "vkr_percent", "pfe_kw", "i0_percent", "vector_group", "phase",
         "tap_side", "tap_pos", "tap_neutral", "tap_step_percent", "zero_sequence_ref",
         "in_service"],
        [
            ("DT-001", "BUS-MV-003", "BUS-LV-001", 400, 10, 0.4, 4.0, 1.2, 0.6, 0.5,
             "Dyn11", "ABC", "lv", 0, 0, 2.5, "ZS-DT001", True),
        ],
    )

    sheet(
        "switches",
        ["switch_id", "element_id", "terminal", "closed", "rated_i_a", "valid_time", "phase"],
        [("SW-001", "LN-MV-002", 1, True, 400, "2026-06-01T00:00:00+08:00", "ABC")],
    )

    sheet(
        "loads",
        ["load_id", "node_id", "phase", "connection", "profile_id", "model_type", "in_service"],
        [
            ("L001", "BUS-LV-002", "ABC", "wye", "LP001", "constant_pq", True),
            ("L002", "BUS-MV-003", "ABC", "wye", "LP002", "constant_pq", True),
        ],
    )

    sheet(
        "pv",
        ["pv_id", "node_id", "phase", "connection", "rated_p_kw", "rated_s_kva",
         "profile_id", "control_mode", "control_ref", "fault_model_ref", "in_service"],
        [
            ("PV001", "BUS-LV-002", "ABC", "wye", 200, 220, "PP001", "fixed_pf",
             None, "FM-PV001", True),
            ("PV002", "BUS-MV-003", "ABC", "wye", 500, 550, "PP002", "fixed_pf",
             None, "FM-PV002", True),
        ],
    )

    sheet(
        "sources",
        ["source_id", "node_id", "vm_pu", "angle_deg", "r1_ohm", "x1_ohm",
         "r0_ohm", "x0_ohm", "source_case_id", "z2_assumption_ref"],
        [("SRC-001", "BUS-MV-001", 1.02, 0, 0.5, 2.0, 1.5, 6.0, "MAX-MODE", "Z2-EQ-R1")],
    )

    wb.save(os.path.join(HERE, "network.xlsx"))


# ---------------------------------------------------------------- profiles.csv
def _bell(hour, peak, start=6, end=20, peak_hour=13):
    """Realistic PV availability bell curve.

    0.0 strictly outside [start, end]; non-zero from hour `start` itself
    (ramping up) and through hour `end`, peaking at `peak_hour`.
    A raised-cosine is used with the sine phase offset by half a step so the
    window endpoints are small but strictly positive.
    """
    import math
    if hour < start or hour > end:
        return 0.0
    if hour <= peak_hour:
        span = peak_hour - start
        frac = (hour - start) / span if span else 1.0
    else:
        span = end - peak_hour
        frac = (end - hour) / span if span else 1.0
    # sin^2 with an offset floor keeps frac=0 (window edge) above zero
    val = peak * (math.sin(math.pi * (0.06 + 0.94 * frac) / 2.0) ** 2)
    return round(val, 4)


def build_profiles():
    ts = ["2026-07-01T%02d:00:00+08:00" % h for h in range(24)]

    # LP001: LV load, night ~80, morning peak ~190 (h8), midday dip ~120 (h13),
    # evening peak ~240 (h19)
    lp001 = [80, 76, 74, 72, 73, 78, 95, 140, 190, 175, 150, 132,
             125, 120, 128, 145, 170, 200, 230, 240, 225, 190, 140, 100]
    # LP002: MV load, ~1.5x shape: night ~300, midday ~650, evening peak ~900
    lp002 = [300, 288, 280, 272, 276, 295, 360, 520, 700, 660, 580, 520,
             500, 650, 520, 560, 640, 740, 860, 900, 840, 720, 540, 380]

    header = ["profile_id", "resource_id", "resource_type", "timestamp", "phase",
              "p_kw", "q_kvar", "available_p_pu", "v_v", "i_a", "quality_flag",
              "source_id"]
    rows = []

    for h in range(24):
        p = float(lp001[h])
        rows.append(["LP001", "L001", "load", ts[h], "ALL",
                     p, round(p * 0.33, 4), "", "", "", "valid", "METER001"])
    for h in range(24):
        p = float(lp002[h])
        rows.append(["LP002", "L002", "load", ts[h], "ALL",
                     p, round(p * 0.30, 4), "", "", "", "valid", "METER002"])
    for h in range(24):
        rows.append(["PP001", "PV001", "pv", ts[h], "ALL",
                     "", "", _bell(h, 0.85), "", "", "valid", "PV_METER001"])
    for h in range(24):
        rows.append(["PP002", "PV002", "pv", ts[h], "ALL",
                     "", "", _bell(h, 0.80), "", "", "valid", "PV_METER002"])

    path = os.path.join(HERE, "profiles.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ---------------------------------------------------------------- rules.yaml
RULES_YAML = """schema_version: hc-rules-v1
rule_package: HC-RULES@1.0
status: approved
approval_ref: APPROVAL-2026-001
effective_from: 2026-06-01T00:00:00+08:00
scope: {region: REGION-001, voltage_levels_kv: [0.4, 10]}
constraints:
  - rule_id: VOLTAGE_LIMIT
    rule_class: voltage
    metric: voltage_pu
    operator: between
    min: 0.93
    max: 1.07
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
  upper_kw: 2000
  precision_kw: 10
  max_iter: 20
management:
  formula_id: HC-MGMT-STD
  coincidence_factor: 0.9
  margin_ratio: 0.1
  reservation_policy_ref: RES-POLICY-001
  hierarchy_policy_ref: HIER-POLICY-001
grading:
  red_at_or_below_kw: 0
  yellow_margin_ratio: 0.1
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
    with open(os.path.join(HERE, "rules.yaml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(RULES_YAML)


# ---------------------------------------------------------------- task.json
TASK = {
    "schema_version": "hc-input-v1",
    "task_id": "TASK-001",
    "business_scene": "capacity_publication",
    "scope": {"type": "feeder", "ids": ["FD-001"]},
    "time_window": {"start": "2026-07-01T00:00:00+08:00", "end": "2026-07-02T00:00:00+08:00"},
    "time_step_minutes": 60,
    "snapshot_id": "SNAP-001",
    "rule_package": "HC-RULES@1.0",
    "engine": {"id": "opendss", "version": "LOCKED_DSS_VERSION",
               "interface": "OpenDSSDirect.py", "interface_version": "LOCKED_API_VERSION"},
    "analysis_types": ["powerflow_3ph", "imbalance"],
    "candidate_nodes": [{"node_id": "BUS-LV-002", "phase": "ABC", "weight": 1.0}],
    "search": {"lower_kw": 0, "upper_kw": 2000, "precision_kw": 10, "max_iter": 20},
    "solver": {"max_iter": 100, "tolerance": 1e-4, "timeout_s": 120},
    "verification": {"enabled": True, "metric_tolerances_ref": "TOL-001"},
    "allocation_mode": "fixed_weights",
}


def build_task():
    with open(os.path.join(HERE, "task.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(TASK, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ---------------------------------------------------------------- simple CSVs
def write_csv_bom(name, header, rows):
    with open(os.path.join(HERE, name), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_rest():
    write_csv_bom(
        "reservations.csv",
        ["reservation_id", "application_id", "object_id", "capacity_kw", "status",
         "valid_from", "valid_to", "allocation_ref", "hierarchy_path"],
        [],
    )
    write_csv_bom(
        "equipment_limits.csv",
        ["equipment_id", "limit_type", "value", "unit", "direction",
         "duration_minutes", "rule_ref"],
        [
            ["LN-MV-001", "continuous_current", 400, "A", "forward", 0, "THERMAL_LIMIT"],
            ["LN-MV-002", "continuous_current", 400, "A", "forward", 0, "THERMAL_LIMIT"],
            ["LN-LV-001", "continuous_current", 250, "A", "forward", 0, "THERMAL_LIMIT"],
            ["DT-001", "continuous_current", 577, "A", "forward", 0, "THERMAL_LIMIT"],
        ],
    )
    write_csv_bom(
        "fault_models.csv",
        ["resource_id", "model_type", "parameter_ref", "manufacturer_ref",
         "valid_from", "valid_to"],
        [
            ["PV001", "inverter_fault_current", "FM-PV001", "MFG-A",
             "2026-06-01T00:00:00+08:00", "2026-12-31T00:00:00+08:00"],
            ["PV002", "inverter_fault_current", "FM-PV002", "MFG-A",
             "2026-06-01T00:00:00+08:00", "2026-12-31T00:00:00+08:00"],
        ],
    )


if __name__ == "__main__":
    build_network()
    build_profiles()
    build_rules()
    build_task()
    build_rest()
    print("built OK ->", HERE)
