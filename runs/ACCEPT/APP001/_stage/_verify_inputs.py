"""Independent consistency check of the FD002 golden-test input package."""
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


wb = load_workbook(os.path.join(HERE, "network.xlsx"), data_only=True)
print("sheets:", wb.sheetnames)
check(wb.sheetnames == ["meta", "buses", "lines", "transformers",
                        "switches", "loads", "pv", "sources"],
      "sheet names/order exact (same as FD001)")

meta = {r[0]: r[1] for r in wb["meta"].iter_rows(min_row=2, values_only=True)}
check(meta["snapshot_id"] == "SNAP-002", "meta snapshot_id SNAP-002")
check(meta["schema_version"] == "hc-input-v1", "schema_version hc-input-v1")

lines = list(wb["lines"].iter_rows(min_row=2, values_only=True))
lv = [x for x in lines if x[0] == "LN-LV-001"][0]
check(lv[11] == 600, "LN-LV-001 max_i_a = 600 (was 250)")
check(lv[5] == 0.32 and lv[6] == 0.08, "LN-LV-001 r1=0.32 x1=0.08")
mv = [x for x in lines if x[0].startswith("LN-MV")]
check(all(x[11] == 400 for x in mv), "MV lines max_i_a = 400")
check(all((x[5], x[6], x[7], x[8]) == (0.17, 0.34, 0.51, 1.02) for x in mv),
      "MV line impedances 0.17/0.34/0.51/1.02")

tr = list(wb["transformers"].iter_rows(min_row=2, values_only=True))[0]
check(tr[3] == 1000, "DT-001 sn_kva = 1000 (was 400)")
check(tr[6] == 4.0 and tr[7] == 1.2, "DT-001 vk=4.0 vkr=1.2")
check(tr[10] == "Dyn11", "DT-001 Dyn11")

pv = list(wb["pv"].iter_rows(min_row=2, values_only=True))
check(pv[0][4] == 100 and pv[1][4] == 300, "PV001=100 kW, PV002=300 kW")

# loads
prof = list(csv.DictReader(open(os.path.join(HERE, "profiles.csv"),
                               encoding="utf-8-sig", newline="")))
lp1 = [float(r["p_kw"]) for r in prof if r["profile_id"] == "LP001"]
lp2 = [float(r["p_kw"]) for r in prof if r["profile_id"] == "LP002"]
check(max(lp1) == 180, "LP001 peak 180 kW at h%d" % lp1.index(180))
check(lp1[0] == 60, "LP001 night h0 = 60 kW")
check(max(lp2) == 350, "LP002 peak 350 kW at h%d" % lp2.index(350))
check(len(prof) == 96, "profiles has 96 rows")
import math
check(abs(180 / (math.sqrt(3) * 0.4) - 260) < 5,
      "LV current at 180 kW = %.0f A << 600 A" % (180 / (math.sqrt(3) * 0.4)))
check(abs((180 + 350) / (math.sqrt(3) * 10) - 31) < 2,
      "MV current at 530 kW = %.0f A << 400 A" % ((180 + 350) / (math.sqrt(3) * 10)))

# PV bell shape
pp1 = [float(r["available_p_pu"]) for r in prof if r["profile_id"] == "PP001"]
pp2 = [float(r["available_p_pu"]) for r in prof if r["profile_id"] == "PP002"]
for nm, ap, peak in (("PP001", pp1, 0.85), ("PP002", pp2, 0.80)):
    check(abs(max(ap) - peak) < 1e-9, "%s peak == %.2f" % (nm, peak))
    check(ap.index(max(ap)) == 13, "%s peak exactly at h13" % nm)
    check(all(ap[h] == 0.0 for h in range(0, 6)) and
          all(ap[h] == 0.0 for h in range(21, 24)), "%s zero h0-h5, h21-h23" % nm)
    check(ap[6] > 0 and ap[20] > 0, "%s non-zero at h6 and h20" % nm)

# rules.yaml
ry = open(os.path.join(HERE, "rules.yaml"), encoding="utf-8").read()
check("min: 0.90" in ry and "max: 1.10" in ry, "voltage limits 0.90 / 1.10")
check("max: 400" in ry, "REVERSE_FLOW_LIMIT max 400 kW")
check("lower_kw: 0" in ry and "upper_kw: 3000" in ry, "search 0-3000")
check("precision_kw: 10" in ry and "max_iter: 25" in ry, "precision 10 / max_iter 25")
check("approval_ref: APPROVAL-2026-002" in ry, "approval_ref APPROVAL-2026-002")
check("effective_from: 2026-06-01T00:00:00+08:00" in ry, "effective_from set")
check("formula_id: HC-MGMT-STD" in ry, "management HC-MGMT-STD")
check("coincidence_factor: 0.9" in ry and "margin_ratio: 0.1" in ry, "mgmt factors 0.9/0.1")
check("yellow_margin_ratio: 0.15" in ry, "grading yellow_margin_ratio 0.15")

tk = json.load(open(os.path.join(HERE, "task.json"), encoding="utf-8"))
check(tk["task_id"] == "TASK-002", "task_id TASK-002")
check(tk["snapshot_id"] == "SNAP-002", "snapshot_id SNAP-002")
check(tk["search"] == {"lower_kw": 0, "upper_kw": 3000,
                       "precision_kw": 10, "max_iter": 25}, "task search block")
check(tk["candidate_nodes"] == [{"node_id": "BUS-LV-002", "phase": "ABC",
                                 "weight": 1.0}], "candidate_nodes BUS-LV-002")
check(tk["scope"]["ids"] == ["FD-002"], "scope FD-002")

el = list(csv.DictReader(open(os.path.join(HERE, "equipment_limits.csv"),
                              encoding="utf-8-sig", newline="")))
vals = {x["equipment_id"]: int(x["value"]) for x in el}
check(vals == {"LN-MV-001": 400, "LN-MV-002": 400, "LN-LV-001": 600,
               "DT-001": 1443}, "equipment_limits values %r" % vals)

res = list(csv.reader(open(os.path.join(HERE, "reservations.csv"),
                           encoding="utf-8-sig", newline="")))
check(len(res) == 1, "reservations.csv header only")

print()
print("RESULT:", "ALL CHECKS PASSED" if not FAIL else "%d FAILED" % len(FAIL))
for m in FAIL:
    print("  -", m)
