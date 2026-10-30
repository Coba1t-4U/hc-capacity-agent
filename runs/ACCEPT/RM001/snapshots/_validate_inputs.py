# -*- coding: utf-8 -*-
"""Independent validator for the RM001 input package.

Checks the package against the consumers' actual requirements WITHOUT the
pipeline-level bugs: it calls the individual loaders directly and asserts the
task-required physical/structural invariants.
"""
import csv
import json
import os
import sys

ROOT = r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project"
HERE = os.path.join(ROOT, "tests", "research", "RM001")
sys.path.insert(0, ROOT)

from hc_agent.io.schema import read_csv                     # noqa: E402
from hc_agent.coordination.atc import load_interfaces       # noqa: E402
from hc_agent.research.capacity_outer import load_capacity_direction  # noqa: E402
from hc_agent.uncertainty.probability_adversary import load_probability_set  # noqa: E402
from hc_agent.optimization.local_dispatch import (          # noqa: E402
    load_generators, load_storage, load_flex_loads,
)

ok = True


def check(name, cond, detail=""):
    global ok
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  -> " + str(detail)) if detail else ""))
    if not cond:
        ok = False


def rows(fn):
    with open(os.path.join(HERE, fn), "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


print("== structural files ==")
task = json.load(open(os.path.join(HERE, "research_task.json"), encoding="utf-8"))
for k in ("study_id", "mode", "scope", "capacity_direction", "solver", "seed"):
    check("research_task has " + k, k in task and task[k] is not None)
check("mode in (fixed,atc,atc_dro)", task["mode"] in ("fixed", "atc", "atc_dro"), task["mode"])
check("scope in (dm_equivalent,tdm_full)", task["scope"] in ("dm_equivalent", "tdm_full"), task["scope"])

reg = read_csv(os.path.join(HERE, "regions.csv"))
check("regions header exact",
      list(reg[0].keys()) == ["region_id", "level", "parent_region_id", "model_id"],
      list(reg[0].keys()))
ids = {r["region_id"] for r in reg}
check("all parents exist", all((not r["parent_region_id"]) or r["parent_region_id"] in ids for r in reg))
check("levels valid", all(r["level"] in ("T", "D", "M") for r in reg))

print("== pcc load_interfaces ==")
ifaces = load_interfaces(read_csv(os.path.join(HERE, "pcc.csv")))
check("4 interfaces loaded", len(ifaces) == 4, len(ifaces))
for i in ifaces:
    check("iface %s has P limits+limit_ref" % i.pcc_id,
          i.p_min_mw is not None and i.p_max_mw is not None and bool(i.limit_ref))
    check("iface %s regions exist" % i.pcc_id,
          i.parent_region_id in ids and i.child_region_id in ids)

print("== capacity_direction ==")
d = load_capacity_direction(read_csv(os.path.join(HERE, "capacity_direction.csv")))
check("4 direction assets", len(d.assets) == 4, len(d.assets))
check("weights non-negative non-zero",
      all(a["direction_weight"] >= 0 for a in d.assets) and sum(a["direction_weight"] for a in d.assets) > 0)
check("normalized sums to 1", abs(sum(d.normalized().values()) - 1.0) < 1e-9)

print("== probability set ==")
prows = read_csv(os.path.join(HERE, "scenario_probability.csv"))
ps = load_probability_set(prows)
check("4 scenarios", len(ps.p0) == 4, len(ps.p0))
check("probs non-negative", all(p >= 0 for p in ps.p0))
check("probs sum to 1", abs(sum(ps.p0) - 1.0) < 1e-9, sum(ps.p0))
check("radius_l1 > 0 from samples", ps.radius_l1 > 0, ps.radius_l1)
check("probability_set valid", ps.validate() == [], ps.validate())

print("== asset loaders ==")
gens = load_generators(read_csv(os.path.join(HERE, "generators.csv")))
stors = load_storage(read_csv(os.path.join(HERE, "storage.csv")))
flex = load_flex_loads(read_csv(os.path.join(HERE, "flex_load.csv")))
check("3 generators", len(gens) == 3, len(gens))
check("3 storage", len(stors) == 3, len(stors))
check("2 flex loads", len(flex) == 2, len(flex))
check("eta in (0,1]", all(0 < s.eta_charge <= 1 and 0 < s.eta_discharge <= 1 for s in stors))

print("== timeseries ==")
ts = read_csv(os.path.join(HERE, "scenario_timeseries.csv"))
check("unit column present on every row", all(r.get("unit") for r in ts))
qtys = {r["quantity"] for r in ts}
check("only recognised quantities", qtys <= {"load_p", "load_q", "pv_available", "wind_available"}, qtys)
scen = {r["scenario_id"] for r in ts}
check("4 scenarios", scen == {"s01", "s02", "s03", "s04"}, scen)
regs = {r["region_id"] for r in ts}
check("4 regions", regs == {"D1", "M1", "M2", "M3"}, regs)
tslist = sorted({r["timestamp"] for r in ts})
check("24 timestamps", len(tslist) == 24, len(tslist))
check("first ts", tslist[0] == "2026-01-01T00:00:00+08:00", tslist[0])
check("last ts", tslist[-1] == "2026-01-01T23:00:00+08:00", tslist[-1])
check("M3 has no wind rows",
      not any(r["region_id"] == "M3" and r["quantity"] == "wind_available" for r in ts))
vals = []
for r in ts:
    try:
        vals.append(float(r["value"]))
    except ValueError:
        check("numeric value %r" % r["value"], False)
check("all values numeric and >= 0", all(v >= 0 for v in vals), min(vals))

# load_q == load_p * ratio, per (scenario, region, hour)
by = {}
for r in ts:
    by[(r["scenario_id"], r["region_id"], r["timestamp"], r["quantity"])] = float(r["value"])
bad = []
for (s, rg, t, q), v in by.items():
    if q == "load_q":
        ratio = 0.30 if rg == "D1" else 0.25
        if abs(v - by[(s, rg, t, "load_p")] * ratio) > 1e-6:
            bad.append((s, rg, t))
check("load_q == load_p * ratio (0.30 D1 / 0.25 M)", not bad, bad[:5])

# PV shape: peak at h13 normally, h14 for s04
for s in ("s01", "s02", "s03"):
    peak_h = max(range(24), key=lambda h: by[(s, "D1", tslist[h], "pv_available")])
    check("%s D1 PV peaks at h13" % s, peak_h == 13, peak_h)
peak_h4 = max(range(24), key=lambda h: by[("s04", "D1", tslist[h], "pv_available")])
check("s04 D1 PV peaks at h14", peak_h4 == 14, peak_h4)

# load scaling between scenarios
for s, f in (("s02", 1.05), ("s03", 0.95), ("s04", 1.00)):
    a = by[(s, "D1", tslist[0], "load_p")]
    b = by[("s01", "D1", tslist[0], "load_p")]
    check("%s load scale %.2f" % (s, f), abs(a - b * f) < 1e-6, (a, b * f))

print("== market ==")
mk = read_csv(os.path.join(HERE, "market.csv"))
check("24 market rows", len(mk) == 24, len(mk))
check("market ts match timeseries", sorted(r["timestamp"] for r in mk) == tslist)
check("buy ToU night=320", float(mk[0]["buy_yuan_per_mwh"]) == 320.0)
check("buy ToU h12=1150", float(mk[12]["buy_yuan_per_mwh"]) == 1150.0)
check("sell = 0.35*buy", all(abs(float(r["sell_yuan_per_mwh"]) - round(0.35 * float(r["buy_yuan_per_mwh"]), 2)) < 1e-9 for r in mk))
check("carbon constant 581", all(float(r["carbon_kg_per_mwh"]) == 581.0 for r in mk))

print("== metadata ==")
md = json.load(open(os.path.join(HERE, "metadata.json"), encoding="utf-8"))
check("interval_hours 1.0", md["interval_hours"] == 1.0)
check("schema_version", md["schema_version"] == "hc-research-v1")

print()
print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
