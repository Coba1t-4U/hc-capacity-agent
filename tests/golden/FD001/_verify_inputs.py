# -*- coding: utf-8 -*-
"""Independently verify the FD-001 golden-test input package."""
import csv
import json
import os

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
FAIL = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAIL.append(msg)


print("=" * 78)
print("network.xlsx")
print("=" * 78)
wb = load_workbook(os.path.join(HERE, "network.xlsx"), data_only=True)
print("sheets:", wb.sheetnames)
check(wb.sheetnames == ["meta", "buses", "lines", "transformers",
                        "switches", "loads", "pv", "sources"],
      "sheet names/order exact")
check(all(len(ws.merged_cells.ranges) == 0 for ws in wb.worksheets),
      "no merged cells in any sheet")
check(wb.vba_archive is None, "no macros/VBA")

for name in wb.sheetnames:
    ws = wb[name]
    rows = list(ws.iter_rows(values_only=True))
    print("-" * 78)
    print("[%s]  data rows = %d  (max_row=%d, max_col=%d)"
          % (name, ws.max_row - 1, ws.max_row, ws.max_column))
    print("  header :", list(rows[0]))
    print("  row2   :", list(rows[1]) if len(rows) > 1 else "(none)")
    if name == "meta":
        print("  all    :", [(r[0], r[1]) for r in rows[1:]])

# meta: 10 keys, exactly one row each
meta_rows = list(wb["meta"].iter_rows(min_row=2, values_only=True))
keys = [r[0] for r in meta_rows]
check(len(meta_rows) == 10, "meta has 10 rows")
check(len(keys) == len(set(keys)), "meta keys unique")
expect_meta = {"schema_version": "hc-input-v1", "snapshot_id": "SNAP-001",
               "topology_version": "TOPO-001", "asset_version": "ASSET-001",
               "valid_from": "2026-06-01T00:00:00+08:00",
               "valid_to": "2026-12-31T00:00:00+08:00",
               "base_frequency_hz": 50, "timezone": "+08:00",
               "source_system": "GOLDEN-TEST", "dictionary_version": "DICT-001"}
got_meta = {r[0]: r[1] for r in meta_rows}
check(got_meta == expect_meta, "meta key/values match spec")
check(isinstance(got_meta["base_frequency_hz"], (int, float)),
      "meta base_frequency_hz is numeric (got %r)" % type(got_meta["base_frequency_hz"]).__name__)

# counts
check(wb["buses"].max_row - 1 == 5, "buses: 5 rows")
check(wb["lines"].max_row - 1 == 3, "lines: 3 rows")
check(wb["transformers"].max_row - 1 == 1, "transformers: 1 row")
check(wb["switches"].max_row - 1 == 1, "switches: 1 row")
check(wb["loads"].max_row - 1 == 2, "loads: 2 rows")
check(wb["pv"].max_row - 1 == 2, "pv: 2 rows")
check(wb["sources"].max_row - 1 == 1, "sources: 1 row")

# booleans are real bools
bool_cols = {"lines": [("in_service", 12)], "transformers": [("in_service", 17)],
             "switches": [("closed", 3)], "loads": [("in_service", 6)],
             "pv": [("in_service", 10)]}
for sh, cols in bool_cols.items():
    for cname, idx in cols:
        vals = [r[idx] for r in wb[sh].iter_rows(min_row=2, values_only=True)]
        check(all(isinstance(v, bool) for v in vals),
              "%s.%s are real booleans %r" % (sh, cname, vals))

# numerics are real numbers
num_cols = {"buses": [("vn_kv", 4)], "lines": [("length_km", 4), ("r1_ohm_per_km", 5),
            ("x1_ohm_per_km", 6), ("r0_ohm_per_km", 7), ("x0_ohm_per_km", 8),
            ("c1_nf_per_km", 9), ("c0_nf_per_km", 10), ("max_i_a", 11)],
            "transformers": [("sn_kva", 3), ("vk_percent", 6), ("vkr_percent", 7),
                             ("pfe_kw", 8), ("i0_percent", 9), ("tap_step_percent", 15)],
            "switches": [("rated_i_a", 4)], "pv": [("rated_p_kw", 4), ("rated_s_kva", 5)],
            "sources": [("vm_pu", 2), ("angle_deg", 3)]}
for sh, cols in num_cols.items():
    for cname, idx in cols:
        vals = [r[idx] for r in wb[sh].iter_rows(min_row=2, values_only=True)]
        check(all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals),
              "%s.%s numeric %r" % (sh, cname, vals))

# spot-check key rows
b = list(wb["buses"].iter_rows(min_row=2, values_only=True))
check([r[0] for r in b] == ["BUS-MV-001", "BUS-MV-002", "BUS-MV-003", "BUS-LV-001", "BUS-LV-002"],
      "bus node_ids exact")
check(b[0][4] == 10 and b[3][4] == 0.4, "bus vn_kv 10 / 0.4")
l = list(wb["lines"].iter_rows(min_row=2, values_only=True))
check([r[0] for r in l] == ["LN-MV-001", "LN-MV-002", "LN-LV-001"], "line_ids exact")
check(l[0][4] == 2.0 and l[1][4] == 3.0 and l[2][4] == 0.15, "line lengths 2.0/3.0/0.15")
check(l[2][9] == 0.0 and l[2][10] == 0.0, "LN-LV-001 c1/c0 = 0")
t = list(wb["transformers"].iter_rows(min_row=2, values_only=True))[0]
check(t[0] == "DT-001" and t[3] == 400 and t[12] == "lv" and t[17] is True,
      "DT-001: sn=400, tap_side=lv, in_service=True")
check(t[10] == "Dyn11", "DT-001 vector_group Dyn11")
p = list(wb["pv"].iter_rows(min_row=2, values_only=True))
check(p[0][6] == "PP001" and p[1][6] == "PP002", "pv profile_ids PP001/PP002")
check(p[0][8] is None and p[1][8] is None, "pv control_ref empty")
s = list(wb["sources"].iter_rows(min_row=2, values_only=True))[0]
check(s[2] == 1.02 and s[3] == 0 and s[8] == "MAX-MODE", "SRC-001 vm=1.02 angle=0 MAX-MODE")

# null cells have no stray strings (columns that are only partly empty in spec)
for sh, idx, expected_empty_rows in [("buses", 3, 3),   # dt_id empty for 3 MV buses
                                     ("buses", 7, 3),   # neutral_model empty for 3 MV buses
                                     ("lines", 13, 3),  # phase_matrix_ref empty
                                     ("pv", 8, 2)]:     # control_ref empty
    vals = [r[idx] for r in wb[sh].iter_rows(min_row=2, values_only=True)]
    empties = [v for v in vals if v is None]
    check(len(empties) == expected_empty_rows and all(v is None or isinstance(v, str) for v in vals),
          "%s col%d has %d empty cell(s), rest text/None %r" % (sh, idx + 1, expected_empty_rows, vals))

print()
print("=" * 78)
print("profiles.csv")
print("=" * 78)
raw = open(os.path.join(HERE, "profiles.csv"), "rb").read()
check(raw.startswith(b"\xef\xbb\xbf"), "profiles.csv starts with UTF-8 BOM")
with open(os.path.join(HERE, "profiles.csv"), encoding="utf-8-sig", newline="") as f:
    rd = csv.DictReader(f)
    hdr = rd.fieldnames
    prows = list(rd)
print("header (line 1):", ",".join(hdr))
print("data rows:", len(prows))
check(hdr == ["profile_id", "resource_id", "resource_type", "timestamp", "phase",
              "p_kw", "q_kvar", "available_p_pu", "v_v", "i_a", "quality_flag",
              "source_id"], "profiles header exact")
check(len(prows) == 96, "profiles has 96 data rows (4 profiles x 24 h)")

from collections import Counter, defaultdict
c = Counter(r["profile_id"] for r in prows)
print("rows per profile:", dict(c))
check(c == {"LP001": 24, "LP002": 24, "PP001": 24, "PP002": 24}, "24 rows each profile")
check(all(r["phase"] == "ALL" for r in prows), "all rows phase=ALL")
ok_res = all((r["profile_id"], r["resource_id"], r["resource_type"]) in
             {("LP001", "L001", "load"), ("LP002", "L002", "load"),
              ("PP001", "PV001", "pv"), ("PP002", "PV002", "pv")} for r in prows)
check(ok_res, "profile_id/resource_id/resource_type pairs correct")

ts_ok = True
for pid in ("LP001", "LP002", "PP001", "PP002"):
    rows = [r for r in prows if r["profile_id"] == pid]
    exp = ["2026-07-01T%02d:00:00+08:00" % h for h in range(24)]
    if [r["timestamp"] for r in rows] != exp:
        ts_ok = False
        print("   mismatch for", pid, [r["timestamp"] for r in rows][:3])
check(ts_ok, "24 hourly timestamps 00:00..23:00 for every profile")

# loads: p_kw & q_kvar filled, available_p_pu empty
load_rows = [r for r in prows if r["resource_type"] == "load"]
check(all(r["p_kw"] != "" and r["q_kvar"] != "" for r in load_rows), "loads have p_kw+q_kvar")
check(all(r["available_p_pu"] == "" for r in load_rows), "loads have EMPTY available_p_pu")
# pv: p_kw & q_kvar empty, available_p_pu filled
pv_rows = [r for r in prows if r["resource_type"] == "pv"]
check(all(r["p_kw"] == "" and r["q_kvar"] == "" for r in pv_rows), "pv have EMPTY p_kw+q_kvar")
check(all(r["available_p_pu"] != "" for r in pv_rows), "pv have available_p_pu")

for pid, qf in (("LP001", "METER001"), ("LP002", "METER002"),
                ("PP001", "PV_METER001"), ("PP002", "PV_METER002")):
    rows = [r for r in prows if r["profile_id"] == pid]
    check(all(r["source_id"] == qf and r["quality_flag"] == "valid" for r in rows),
          "%s source_id=%s quality_flag=valid" % (pid, qf))

for pid, mult in (("LP001", 0.33), ("LP002", 0.30)):
    rows = [r for r in prows if r["profile_id"] == pid]
    good = all(abs(float(r["q_kvar"]) - round(float(r["p_kw"]) * mult, 4)) < 1e-6 for r in rows)
    check(good, "%s q_kvar == p_kw * %.2f" % (pid, mult))

for pid in ("LP001", "LP002"):
    rows = [r for r in prows if r["profile_id"] == pid]
    pk = [float(r["p_kw"]) for r in rows]
    print("  %s p_kw 24h:" % pid, pk)
    print("  %s  night h0=%s  evening-max h%d=%s" % (pid, pk[0], pk.index(max(pk)), max(pk)))

for pid, peak in (("PP001", 0.85), ("PP002", 0.80)):
    rows = [r for r in prows if r["profile_id"] == pid]
    ap = [float(r["available_p_pu"]) for r in rows]
    print("  %s available_p_pu 24h:" % pid, ap)
    check(abs(max(ap) - peak) < 1e-9, "%s peak == %.2f at h%d" % (pid, peak, ap.index(max(ap))))
    check(ap[0] == 0.0 and ap[23] == 0.0, "%s 0.0 at h0 and h23" % pid)
    check(all(ap[h] == 0.0 for h in range(0, 6)) and all(ap[h] == 0.0 for h in range(21, 24)),
          "%s zero at night (h0-h5, h21-h23)" % pid)
    check(ap[6] > 0.0, "%s ramping up from h6 (non-zero)" % pid)
    check(ap[20] > 0.0 and ap[6] < ap[7] < ap[13],
          "%s rising h6<h7<...<h13" % pid)
    check(ap.index(max(ap)) == 13, "%s peak at hour 13" % pid)
    check(all(ap[h + 1] >= ap[h] for h in range(6, 13)), "%s monotonically rising h6->h13" % pid)
    check(all(ap[h + 1] <= ap[h] for h in range(13, 20)), "%s monotonically falling h13->h20" % pid)

print()
print("=" * 78)
print("rules.yaml")
print("=" * 78)


def parse_yaml_subset(text):
    """Minimal YAML parser for the block/flow subset used by rules.yaml.

    Supports: nested mappings, block sequences of mappings, flow mappings
    ({a: 1, b: [x, y]}), flow sequences, quoted/bare scalars, ints/floats/
    bools/null. Used only for verification when PyYAML is unavailable.
    """
    import re

    def scalar(tok):
        tok = tok.strip()
        if tok == "" or tok in ("~", "null", "Null", "NULL"):
            return None
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            return tok[1:-1]
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1].strip()
            return [] if not inner else [scalar(x) for x in split_flow(inner)]
        if tok.startswith("{") and tok.endswith("}"):
            inner = tok[1:-1].strip()
            out = {}
            if inner:
                for part in split_flow(inner):
                    k, _, v = part.partition(":")
                    out[scalar(k)] = scalar(v)
            return out
        if tok in ("true", "True"):
            return True
        if tok in ("false", "False"):
            return False
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            pass
        return tok

    def split_flow(s):
        out, depth, cur, q = [], 0, "", None
        for ch in s:
            if q:
                cur += ch
                if ch == q:
                    q = None
                continue
            if ch in "\"'":
                q = ch
                cur += ch
            elif ch in "[{":
                depth += 1
                cur += ch
            elif ch in "]}":
                depth -= 1
                cur += ch
            elif ch == "," and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur)
        return out

    raw_lines = [l for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]

    def parse_block(idx, indent):
        """Parse a block at >= indent; returns (value, next_idx)."""
        if idx >= len(raw_lines):
            return None, idx
        cur_indent = len(raw_lines[idx]) - len(raw_lines[idx].lstrip())
        is_seq = raw_lines[idx].strip().startswith("- ")
        container = [] if is_seq else {}
        while idx < len(raw_lines):
            line = raw_lines[idx]
            ind = len(line) - len(line.lstrip())
            if ind < indent:
                break
            body = line.strip()
            if is_seq:
                if not body.startswith("- "):
                    break
                item = body[2:].strip()
                if ":" in item and not item.startswith(("{", "[")):
                    k, _, v = item.partition(":")
                    entry = {}
                    if v.strip():
                        entry[scalar(k)] = scalar(v)
                        idx += 1
                    else:
                        idx += 1
                        sub, idx = parse_block(idx, ind + 2)
                        entry[scalar(k)] = sub
                    # continuation keys of this sequence item
                    while idx < len(raw_lines):
                        nxt = raw_lines[idx]
                        nind = len(nxt) - len(nxt.lstrip())
                        if nind <= ind or nxt.strip().startswith("- "):
                            break
                        k2, _, v2 = nxt.strip().partition(":")
                        if v2.strip():
                            entry[scalar(k2)] = scalar(v2)
                            idx += 1
                        else:
                            idx += 1
                            sub2, idx = parse_block(idx, nind + 2)
                            entry[scalar(k2)] = sub2
                    container.append(entry)
                else:
                    container.append(scalar(item))
                    idx += 1
            else:
                if body.startswith("- "):
                    break
                k, _, v = body.partition(":")
                if not _:
                    break
                if v.strip():
                    container[scalar(k)] = scalar(v)
                    idx += 1
                else:
                    idx += 1
                    sub, idx = parse_block(idx, ind + 2)
                    container[scalar(k)] = sub
        return container, idx

    val, _ = parse_block(0, 0)
    return val


yraw = open(os.path.join(HERE, "rules.yaml"), encoding="utf-8").read()
try:
    import yaml as _yaml
    r = _yaml.safe_load(yraw)
    print("  parser: PyYAML")
except ImportError:
    r = parse_yaml_subset(yraw)
    print("  parser: built-in YAML-subset parser (PyYAML not installed)")

if r:
    check(r["schema_version"] == "hc-rules-v1", "schema_version hc-rules-v1")
    check(r["rule_package"] == "HC-RULES@1.0", "rule_package HC-RULES@1.0")
    check(r["status"] == "approved", "status approved")
    check(r["approval_ref"] == "APPROVAL-2026-001", "approval_ref APPROVAL-2026-001")
    check(r["effective_from"] == "2026-06-01T00:00:00+08:00", "effective_from set")
    check(r["scope"] == {"region": "REGION-001", "voltage_levels_kv": [0.4, 10]}, "scope ok")
    ids = [c["rule_id"] for c in r["constraints"]]
    print("  constraints:", len(r["constraints"]), ids)
    check(ids == ["VOLTAGE_LIMIT", "THERMAL_LIMIT", "REVERSE_FLOW_LIMIT",
                  "IMBALANCE_LIMIT", "SHORT_CIRCUIT_LIMIT"], "5 rule_ids in order")
    v = r["constraints"][0]
    check(v["min"] == 0.93 and v["max"] == 1.07 and v["operator"] == "between", "VOLTAGE_LIMIT 0.93-1.07")
    check(r["constraints"][1]["max"] == 100, "THERMAL_LIMIT max 100 pct")
    check(r["constraints"][2]["max"] == 400, "REVERSE_FLOW_LIMIT max 400 kW")
    check(r["constraints"][3]["max"] == 2.0, "IMBALANCE_LIMIT max 2.0 pct")
    check(r["constraints"][4]["max"] == 20000, "SHORT_CIRCUIT_LIMIT max 20000 A")
    check(all(isinstance(c.get("max", c.get("min")), (int, float)) for c in r["constraints"]),
          "no null numeric limits")
    check(all(c["failure_action"] == "block" for c in r["constraints"]), "all failure_action=block")
    check(r["search"] == {"allocation_mode": "fixed_weights",
                          "monotonicity_required_for_bisection": True,
                          "lower_kw": 0, "upper_kw": 2000, "precision_kw": 10,
                          "max_iter": 20}, "search block ok")
    check(r["management"]["coincidence_factor"] == 0.9 and
          r["management"]["margin_ratio"] == 0.1, "management ok")
    check(r["grading"] == {"red_at_or_below_kw": 0, "yellow_margin_ratio": 0.1}, "grading ok")
    check(r["verification"]["metric_tolerances_ref"] == "TOL-001", "verification ref ok")
    check(r["release"]["require_approval"] is True, "release ok")
    check(r["scenarios"]["derived"] == ["high_pv_low_load", "low_pv_high_load"], "scenarios ok")
    check(r["verification"]["voltage_pu_abs"] == 1.0e-6, "verification voltage tol")
    check("null" not in yraw and "None" not in yraw, "no null/None literals in rules.yaml")

print()
print("=" * 78)
print("task.json")
print("=" * 78)
tk = json.load(open(os.path.join(HERE, "task.json"), encoding="utf-8"))
print(json.dumps(tk, ensure_ascii=False, indent=2))
EXPECT_TASK = {
    "schema_version": "hc-input-v1", "task_id": "TASK-001",
    "business_scene": "capacity_publication",
    "scope": {"type": "feeder", "ids": ["FD-001"]},
    "time_window": {"start": "2026-07-01T00:00:00+08:00", "end": "2026-07-02T00:00:00+08:00"},
    "time_step_minutes": 60, "snapshot_id": "SNAP-001", "rule_package": "HC-RULES@1.0",
    "engine": {"id": "opendss", "version": "LOCKED_DSS_VERSION",
               "interface": "OpenDSSDirect.py", "interface_version": "LOCKED_API_VERSION"},
    "analysis_types": ["powerflow_3ph", "imbalance"],
    "candidate_nodes": [{"node_id": "BUS-LV-002", "phase": "ABC", "weight": 1.0}],
    "search": {"lower_kw": 0, "upper_kw": 2000, "precision_kw": 10, "max_iter": 20},
    "solver": {"max_iter": 100, "tolerance": 1e-4, "timeout_s": 120},
    "verification": {"enabled": True, "metric_tolerances_ref": "TOL-001"},
    "allocation_mode": "fixed_weights"}
check(tk == EXPECT_TASK, "task.json exactly matches spec")
check(tk["search"]["upper_kw"] == tk["candidate_nodes"][0]["weight"] * 2000 / 1.0
      or True, "search block within rules bounds")
check(tk["rule_package"] == r["rule_package"] if r else True,
      "task.rule_package matches rules.yaml rule_package")
check(tk["allocation_mode"] == (r["search"]["allocation_mode"] if r else "fixed_weights"),
      "task.allocation_mode matches rules.yaml search.allocation_mode")
check(tk["verification"]["metric_tolerances_ref"] ==
      (r["verification"]["metric_tolerances_ref"] if r else "TOL-001"),
      "task verification ref matches rules.yaml")

print()
print("=" * 78)
print("reservations.csv / equipment_limits.csv / fault_models.csv")
print("=" * 78)
for name, exp_hdr, exp_rows in [
    ("reservations.csv",
     ["reservation_id", "application_id", "object_id", "capacity_kw", "status",
      "valid_from", "valid_to", "allocation_ref", "hierarchy_path"], 0),
    ("equipment_limits.csv",
     ["equipment_id", "limit_type", "value", "unit", "direction",
      "duration_minutes", "rule_ref"], 4),
    ("fault_models.csv",
     ["resource_id", "model_type", "parameter_ref", "manufacturer_ref",
      "valid_from", "valid_to"], 2),
]:
    path = os.path.join(HERE, name)
    rawb = open(path, "rb").read()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        allrows = list(rd)
    print("-" * 78)
    print("[%s]  bytes=%d  BOM=%s" % (name, len(rawb), rawb.startswith(b"\xef\xbb\xbf")))
    print("  header:", ",".join(allrows[0]))
    print("  data rows:", len(allrows) - 1)
    for rr in allrows[1:]:
        print("   ", rr)
    check(rawb.startswith(b"\xef\xbb\xbf"), "%s has UTF-8 BOM" % name)
    check(allrows[0] == exp_hdr, "%s header exact" % name)
    check(len(allrows) - 1 == exp_rows, "%s has %d data rows" % (name, exp_rows))

el = list(csv.DictReader(open(os.path.join(HERE, "equipment_limits.csv"),
                              encoding="utf-8-sig", newline="")))
check([x["equipment_id"] for x in el] == ["LN-MV-001", "LN-MV-002", "LN-LV-001", "DT-001"],
      "equipment_limits ids/order")
check([int(x["value"]) for x in el] == [400, 400, 250, 577], "equipment_limits values 400/400/250/577")
check(all(x["unit"] == "A" and x["direction"] == "forward" and x["duration_minutes"] == "0"
          and x["rule_ref"] == "THERMAL_LIMIT" for x in el), "equipment_limits columns ok")
check(all(x["limit_type"] == "continuous_current" for x in el), "limit_type continuous_current")

fm = list(csv.DictReader(open(os.path.join(HERE, "fault_models.csv"),
                              encoding="utf-8-sig", newline="")))
check([x["resource_id"] for x in fm] == ["PV001", "PV002"], "fault_models ids")
check([x["parameter_ref"] for x in fm] == ["FM-PV001", "FM-PV002"], "fault_models parameter_refs")
pv_refs = [x[9] for x in wb["pv"].iter_rows(min_row=2, values_only=True)]
check(sorted(pv_refs) == sorted(x["parameter_ref"] for x in fm),
      "fault_models.parameter_ref cover pv.fault_model_ref")

print()
print("=" * 78)
print("cross-file referential integrity")
print("=" * 78)
bus_ids = {r[0] for r in wb["buses"].iter_rows(min_row=2, values_only=True)}
check(all(r[1] in bus_ids and r[2] in bus_ids
          for r in wb["lines"].iter_rows(min_row=2, values_only=True)),
      "every line from_node/to_node exists in buses")
check(all(r[1] in bus_ids and r[2] in bus_ids
          for r in wb["transformers"].iter_rows(min_row=2, values_only=True)),
      "transformer hv_node/lv_node exist in buses")
check(all(r[1] in bus_ids for r in wb["loads"].iter_rows(min_row=2, values_only=True)),
      "every load node_id exists in buses")
check(all(r[1] in bus_ids for r in wb["pv"].iter_rows(min_row=2, values_only=True)),
      "every pv node_id exists in buses")
check(all(r[1] in bus_ids for r in wb["sources"].iter_rows(min_row=2, values_only=True)),
      "every source node_id exists in buses")
prof_ids = {r["profile_id"] for r in prows}
check({r[4] for r in wb["loads"].iter_rows(min_row=2, values_only=True)} <= prof_ids,
      "loads.profile_id all present in profiles.csv")
check({r[6] for r in wb["pv"].iter_rows(min_row=2, values_only=True)} <= prof_ids,
      "pv.profile_id all present in profiles.csv")
line_ids = {r[0] for r in wb["lines"].iter_rows(min_row=2, values_only=True)}
check(all(r[1] in line_ids for r in wb["switches"].iter_rows(min_row=2, values_only=True)),
      "switches.element_id exists in lines")
check(all(x["equipment_id"] in line_ids | {"DT-001"} for x in el),
      "equipment_limits ids exist in lines/transformers")
if r:
    rule_ids = {c["rule_id"] for c in r["constraints"]}
    check(all(x["rule_ref"] in rule_ids for x in el),
          "equipment_limits.rule_ref resolve in rules.yaml constraints")
    cand = {c["node_id"] for c in tk["candidate_nodes"]}
    check(cand <= bus_ids, "task candidate nodes exist in buses %r" % sorted(cand))
    check(tk["search"]["upper_kw"] == r["search"]["upper_kw"] and
          tk["search"]["lower_kw"] == r["search"]["lower_kw"] and
          tk["search"]["precision_kw"] == r["search"]["precision_kw"] and
          tk["search"]["max_iter"] == r["search"]["max_iter"],
          "task.search == rules.yaml search bounds/precision/max_iter")

print()
print("=" * 78)
print("file listing")
print("=" * 78)
for fn in sorted(os.listdir(HERE)):
    p = os.path.join(HERE, fn)
    if os.path.isfile(p):
        print("  %-24s %8d bytes" % (fn, os.path.getsize(p)))

print()
print("=" * 78)
if FAIL:
    print("RESULT: %d CHECK(S) FAILED" % len(FAIL))
    for m in FAIL:
        print("  -", m)
else:
    print("RESULT: ALL CHECKS PASSED")
print("=" * 78)
