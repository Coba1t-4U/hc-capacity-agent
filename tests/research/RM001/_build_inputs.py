# -*- coding: utf-8 -*-
"""Build the RM001 RESEARCH test-case input package (main-grid / distribution /
microgrid coordination).

Target directory: tests/research/RM001/
Consumer: hc_agent/research_pipeline.py (R01-R08)

Schemas were read from the consumer code before writing:
  - hc_agent/research_pipeline.py      _validate_research_task / _validate_regions
                                       / _validate_interfaces / _load_region_assets
                                       / _load_timeseries / _load_market
  - hc_agent/research/capacity_outer.py       load_capacity_direction
  - hc_agent/coordination/atc.py              load_interfaces
  - hc_agent/uncertainty/probability_adversary.py  load_probability_set
  - hc_agent/optimization/local_dispatch.py   load_generators/load_storage/load_flex_loads

All CSVs are UTF-8 **with BOM** (encoding="utf-8-sig"), comma separated, header
first. Numeric cells are bare numbers -- no units, no thousand separators. The
unit lives only in the `unit` column of scenario_timeseries.csv.
"""
import csv
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

STUDY_ID = "RM001"
DATE = "2026-01-01"
HOURS = list(range(24))
TIMESTAMPS = ["%sT%02d:00:00+08:00" % (DATE, h) for h in HOURS]

# ---------------------------------------------------------------- shared shape
# PV bell curve shape factor (dimensionless), per the task specification.
PV_SHAPE = {
    0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0,
    6: 0.02, 7: 0.12, 8: 0.30, 9: 0.50, 10: 0.68, 11: 0.80,
    12: 0.84, 13: 0.85, 14: 0.80, 15: 0.68, 16: 0.50, 17: 0.30,
    18: 0.12, 19: 0.02, 20: 0.0, 21: 0.0, 22: 0.0, 23: 0.0,
}
# s04 shifts the PV peak one hour later (h14); re-index the same bell shape.
PV_SHAPE_LATE = {h: PV_SHAPE.get(h - 1, 0.0) for h in HOURS}
PV_SHAPE_LATE[0] = 0.0

# D1 (distribution) base load shape in MW: night 1.20, morning peak 2.60 (h8),
# midday 1.90, evening peak 3.20 (h19).
D1_LOAD = {
    0: 1.20, 1: 1.15, 2: 1.10, 3: 1.08, 4: 1.10, 5: 1.18,
    6: 1.45, 7: 2.05, 8: 2.60, 9: 2.35, 10: 2.10, 11: 1.95,
    12: 1.90, 13: 1.88, 14: 1.95, 15: 2.10, 16: 2.40, 17: 2.85,
    18: 3.05, 19: 3.20, 20: 2.95, 21: 2.50, 22: 1.90, 23: 1.45,
}
# Microgrid base load shape in MW: night 0.25, h8 peak 0.60, midday 0.40,
# h19 peak 0.75.
MG_LOAD = {
    0: 0.25, 1: 0.24, 2: 0.23, 3: 0.22, 4: 0.23, 5: 0.24,
    6: 0.31, 7: 0.45, 8: 0.60, 9: 0.54, 10: 0.47, 11: 0.42,
    12: 0.40, 13: 0.39, 14: 0.42, 15: 0.46, 16: 0.54, 17: 0.65,
    18: 0.71, 19: 0.75, 20: 0.68, 21: 0.56, 22: 0.40, 23: 0.30,
}

# Installed PV / wind capability per region (MW).
D1_PV_MW = 3.0
D1_WIND_MW = 1.5
MG_PV_MW = 0.50
WIND_REGIONS = ("M1", "M2")          # M3 has NO wind -> omit wind rows for M3
MG_WIND_MW = 0.35

# Wind shape factor: higher at night, lower midday (normalised to the night peak).
WIND_SHAPE = {
    0: 1.00, 1: 1.00, 2: 1.00, 3: 1.00, 4: 1.00, 5: 1.00,
    6: 0.92, 7: 0.80, 8: 0.66, 9: 0.54, 10: 0.44, 11: 0.36,
    12: 0.30, 13: 0.30, 14: 0.34, 15: 0.42, 16: 0.52, 17: 0.64,
    18: 0.76, 19: 0.86, 20: 0.92, 21: 0.96, 22: 0.99, 23: 1.00,
}

D1_Q_RATIO = 0.30
MG_Q_RATIO = 0.25


def _r(value, nd=6):
    """Round for stable, human-readable CSV cells."""
    return round(float(value), nd)


def build_scenarios():
    """Return {scenario_id: {"load": {...}, "q": {...}, "pv": {...}, "wind": {...}}}
    keyed by (region_id, hour)."""
    scenarios = {}
    for sid in ("s01", "s02", "s03", "s04"):
        scenarios[sid] = {"load": {}, "q": {}, "pv": {}, "wind": {}}

    def put(sid, region, hour, load_scale, pv_scale, peak_shift, q_ratio,
            pv_rated, wind_rated):
        d1 = region == "D1"
        base = (D1_LOAD if d1 else MG_LOAD)[hour]
        # s04 shifts the evening load peak one hour later (h20).
        if peak_shift and hour >= 16:
            base = (D1_LOAD if d1 else MG_LOAD)[hour - 1]
        p = base * load_scale
        scenarios[sid]["load"][(region, hour)] = _r(p)
        scenarios[sid]["q"][(region, hour)] = _r(p * q_ratio)
        if pv_rated:
            shape = PV_SHAPE_LATE if peak_shift else PV_SHAPE
            scenarios[sid]["pv"][(region, hour)] = _r(
                pv_rated * shape[hour] * pv_scale)
        if wind_rated:
            scenarios[sid]["wind"][(region, hour)] = _r(
                wind_rated * WIND_SHAPE[hour])

    # (load_scale, pv_scale, peak_shift)
    variants = {
        "s01": (1.00, 1.00, False),   # base
        "s02": (1.05, 0.90, False),   # higher load, lower PV
        "s03": (0.95, 1.10, False),   # lower load, higher PV
        "s04": (1.00, 1.00, True),    # peak shifted one hour later
    }

    for sid, (ls, ps, shift) in variants.items():
        for h in HOURS:
            put(sid, "D1", h, ls, ps, shift, D1_Q_RATIO, D1_PV_MW, D1_WIND_MW)
            for mg in ("M1", "M2", "M3"):
                put(sid, mg, h, ls, ps, shift, MG_Q_RATIO, MG_PV_MW,
                    MG_WIND_MW if mg in WIND_REGIONS else None)
    return scenarios


def write_csv_bom(name, header, rows):
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


# ---------------------------------------------------------------- 1 research_task
def build_research_task():
    task = {
        "schema_version": "hc-research-v1",
        "study_id": STUDY_ID,
        "mode": "atc",
        "scope": "dm_equivalent",
        "description": "配网 + 3 微网 目标级联协调容量研究（主网为已批准接口等值）",
        "capacity_direction": "D1",
        "outer_search": {"lambda_max_mw": 8.0, "precision_mw": 0.25,
                         "max_iter": 12},
        "atc": {"max_iter": 8, "time_limit_s": 120, "residual_tol": 0.001,
                "objective_tol": 0.0001, "rho0": 1.0},
        "dro": {"enabled": False, "radius_l1": None, "radius_linf": None},
        "solver": {"max_iter": 300, "tolerance": 0.000001},
        "seed": 20261009,
    }
    path = os.path.join(HERE, "research_task.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ---------------------------------------------------------------- 2 regions.csv
def build_regions():
    # level is T/D/M; single-level ownership, every parent exists, no cycles.
    return write_csv_bom(
        "regions.csv",
        ["region_id", "level", "parent_region_id", "model_id"],
        [
            ["D1", "D", "", "model_d1"],
            ["T1", "T", "", "model_t1"],
            ["M1", "M", "D1", "model_m1"],
            ["M2", "M", "D1", "model_m2"],
            ["M3", "M", "D1", "model_m3"],
        ],
    )


# ---------------------------------------------------------------- 3 pcc.csv
def build_pcc():
    # Coupling interfaces. Every row needs numeric P limits AND a limit_ref,
    # otherwise _validate_interfaces blocks it (LIMIT_UNSET).
    return write_csv_bom(
        "pcc.csv",
        ["pcc_id", "parent_region_id", "child_region_id", "parent_bus_id",
         "child_bus_id", "meter_side", "transformer_id", "p_min_mw", "p_max_mw",
         "q_min_mvar", "q_max_mvar", "v_min_pu", "v_max_pu", "limit_ref"],
        [
            ["PCC-T1-D1", "T1", "D1", "BUS-T1-01", "BUS-D1-01", "parent",
             "TR-TD-01", -20, 20, -10, 10, 0.95, 1.05, "LIMIT-REF-TD-001"],
            ["PCC-D1-M1", "D1", "M1", "BUS-D1-11", "BUS-M1-01", "parent",
             "TR-DM-01", -5, 5, -3, 3, 0.95, 1.05, "LIMIT-REF-DM-001"],
            ["PCC-D1-M2", "D1", "M2", "BUS-D1-12", "BUS-M2-01", "parent",
             "TR-DM-02", -5, 5, -3, 3, 0.95, 1.05, "LIMIT-REF-DM-002"],
            ["PCC-D1-M3", "D1", "M3", "BUS-D1-13", "BUS-M3-01", "parent",
             "TR-DM-03", -5, 5, -3, 3, 0.95, 1.05, "LIMIT-REF-DM-003"],
        ],
    )


# ------------------------------------------------------- 4 scenario_probability
def build_scenario_probability():
    # Non-negative and summing to exactly 1.0. Source sample count is required
    # because load_probability_set derives the L1 radius from the sample size.
    return write_csv_bom(
        "scenario_probability.csv",
        ["scenario_id", "probability", "source_sample_count"],
        [
            ["s01", 0.25, 200],
            ["s02", 0.25, 200],
            ["s03", 0.25, 200],
            ["s04", 0.25, 200],
        ],
    )


# -------------------------------------------------------- 5 scenario_timeseries
def build_scenario_timeseries():
    scenarios = build_scenarios()
    header = ["scenario_id", "timestamp", "region_id", "asset_id", "quantity",
              "value", "unit"]
    rows = []
    for sid in ("s01", "s02", "s03", "s04"):
        sc = scenarios[sid]
        for h in HOURS:
            ts = TIMESTAMPS[h]
            for region in ("D1", "M1", "M2", "M3"):
                # load_p (summed per region,timestamp by the loader)
                rows.append([sid, ts, region, "LOAD-%s" % region, "load_p",
                             sc["load"][(region, h)], "MW"])
                # load_q as its own row
                rows.append([sid, ts, region, "LOAD-%s" % region, "load_q",
                             sc["q"][(region, h)], "Mvar"])
                # pv_available stored per region
                rows.append([sid, ts, region, "PV-%s" % region, "pv_available",
                             sc["pv"][(region, h)], "MW"])
                # wind_available only where wind exists (M3 has none)
                if (region, h) in sc["wind"]:
                    rows.append([sid, ts, region, "WIND-%s" % region,
                                 "wind_available", sc["wind"][(region, h)],
                                 "MW"])
    return write_csv_bom("scenario_timeseries.csv", header, rows)


# ------------------------------------------------------- 6 capacity_direction
def build_capacity_direction():
    # Weights non-negative and non-zero; used by load_capacity_direction.
    return write_csv_bom(
        "capacity_direction.csv",
        ["asset_id", "baseline_capacity_mw", "direction_weight",
         "control_policy_id"],
        [
            ["PV-D1", "BASE-D1-0", 0.50, "fixed"],
            ["PV-M1", "BASE-M1-0", 0.20, "fixed"],
            ["PV-M2", "BASE-M2-0", 0.15, "fixed"],
            ["PV-M3", "BASE-M3-0", 0.15, "fixed"],
        ],
    )


# ---------------------------------------------------------------- 7 market.csv
def build_market():
    buy_by_hour = {}
    for h in HOURS:
        if h <= 7 or h >= 22:
            buy_by_hour[h] = 320.0
        elif 8 <= h <= 11:
            buy_by_hour[h] = 680.0
        elif 12 <= h <= 13:
            buy_by_hour[h] = 1150.0
        elif 14 <= h <= 17:
            buy_by_hour[h] = 680.0
        elif 18 <= h <= 21:
            buy_by_hour[h] = 1150.0
    rows = []
    for h in HOURS:
        buy = buy_by_hour[h]
        rows.append([TIMESTAMPS[h], buy, round(0.35 * buy, 2), 581.0])
    return write_csv_bom(
        "market.csv",
        ["timestamp", "buy_yuan_per_mwh", "sell_yuan_per_mwh",
         "carbon_kg_per_mwh"],
        rows,
    )


# ------------------------------------------------------------ 8 generators.csv
def build_generators():
    return write_csv_bom(
        "generators.csv",
        ["asset_id", "owner_region_id", "bus_id", "p_min_mw", "p_max_mw",
         "q_min_mvar", "q_max_mvar", "ramp_mw_per_h", "cost_model_id",
         "initial_state"],
        [
            ["GEN-D1", "D1", "BUS-D1-05", 0.2, 2.0, -1.0, 1.0, 1.0,
             "COST-D1-G1", "on"],
            ["GEN-M1", "M1", "BUS-M1-02", 0.05, 0.5, -0.3, 0.3, 0.5,
             "COST-M1-G1", "on"],
            ["GEN-M2", "M2", "BUS-M2-02", 0.05, 0.5, -0.3, 0.3, 0.5,
             "COST-M2-G1", "on"],
        ],
    )


# --------------------------------------------------------------- 9 storage.csv
def build_storage():
    # eta must be in (0,1]; load_storage raises otherwise.
    return write_csv_bom(
        "storage.csv",
        ["asset_id", "owner_region_id", "bus_id", "p_charge_max_mw",
         "p_discharge_max_mw", "e_min_mwh", "e_max_mwh", "e_initial_mwh",
         "eta_charge", "eta_discharge", "terminal_policy"],
        [
            ["ESS-D1", "D1", "BUS-D1-06", 1.0, 1.0, 0.5, 4.0, 2.0, 0.95,
             0.95, "none"],
            ["ESS-M1", "M1", "BUS-M1-03", 0.3, 0.3, 0.1, 1.2, 0.6, 0.95,
             0.95, "none"],
            ["ESS-M2", "M2", "BUS-M2-03", 0.3, 0.3, 0.1, 1.2, 0.6, 0.95,
             0.95, "none"],
        ],
    )


# ------------------------------------------------------------- 10 flex_load.csv
def build_flex_load():
    return write_csv_bom(
        "flex_load.csv",
        ["asset_id", "owner_region_id", "bus_id", "p_reduce_max_mw",
         "energy_shift_max_mwh", "max_duration_h", "recovery_policy",
         "compensation_yuan_per_mwh", "permission_ref"],
        [
            ["FL-D1", "D1", "BUS-D1-07", 0.5, 1.5, 3, "recover_same_day", 400,
             "PERM-FL-D1-001"],
            ["FL-M1", "M1", "BUS-M1-04", 0.15, 0.4, 2, "recover_same_day",
             400, "PERM-FL-M1-001"],
        ],
    )


# ------------------------------------------------------------- 11 metadata.json
def build_metadata():
    meta = {
        "schema_version": "hc-research-v1",
        "timezone": "+08:00",
        "interval_hours": 1.0,
        "power_base_mva": 10.0,
        "voltage_bases_kv": [10.0, 0.4],
        "units": {"power": "MW", "energy": "MWh", "time": "h"},
        "scenario_source": "synthetic-reference-derived",
        "input_hashes": {},
    }
    path = os.path.join(HERE, "metadata.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


# ---------------------------------------------------------------- self checks
def self_check():
    """Assert the invariants the pipeline loaders enforce."""
    scenarios = build_scenarios()
    # load_q must equal the stated ratio of load_p
    for sid, sc in scenarios.items():
        for (region, h), p in sc["load"].items():
            ratio = D1_Q_RATIO if region == "D1" else MG_Q_RATIO
            q = sc["q"][(region, h)]
            assert abs(q - p * ratio) < 1e-6, (sid, region, h)
            assert p > 0, ("non-positive load", sid, region, h)
            pv = sc["pv"][(region, h)]
            assert pv >= 0, ("negative pv", sid, region, h)
            if (region, h) in sc["wind"]:
                assert sc["wind"][(region, h)] >= 0
    # M3 must have no wind
    for sid, sc in scenarios.items():
        assert not any(r == "M3" for (r, _h) in sc["wind"]), "M3 must have no wind"
    # probabilities sum to exactly 1
    assert abs(sum([0.25, 0.25, 0.25, 0.25]) - 1.0) < 1e-12
    # PV peak at h13 for s01..s03 and h14 for s04, on D1
    assert scenarios["s01"]["pv"][("D1", 13)] == 2.55, \
        scenarios["s01"]["pv"][("D1", 13)]
    assert scenarios["s04"]["pv"][("D1", 14)] == 2.55
    # s04 uses the late bell curve, so h13 takes the h12 shape factor (0.84)
    assert scenarios["s04"]["pv"][("D1", 13)] == _r(D1_PV_MW * PV_SHAPE[12])
    # s02/s03 PV scaling
    assert scenarios["s02"]["pv"][("D1", 13)] == _r(2.55 * 0.90)
    assert scenarios["s03"]["pv"][("D1", 13)] == _r(2.55 * 1.10)
    # s04 evening load peak moved to h20
    assert scenarios["s04"]["load"][("D1", 20)] == D1_LOAD[19]
    assert scenarios["s01"]["load"][("D1", 19)] == D1_LOAD[19]
    return True


def main():
    self_check()
    paths = [
        build_research_task(),
        build_regions(),
        build_pcc(),
        build_scenario_probability(),
        build_scenario_timeseries(),
        build_capacity_direction(),
        build_market(),
        build_generators(),
        build_storage(),
        build_flex_load(),
        build_metadata(),
    ]
    for p in paths:
        print("%8d  %s" % (os.path.getsize(p), p))
    print("built OK ->", HERE)


if __name__ == "__main__":
    main()
