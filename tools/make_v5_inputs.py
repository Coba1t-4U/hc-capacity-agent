# -*- coding: utf-8 -*-
"""构造五电压等级输入包（220/110/35/10/0.4 kV），走系统既有 xlsx → 引擎 通道。

只验证两件事：能否求解、电压与功率平衡是否合理。
判据仍用 FD002 的规则包（未按电压等级改造），因此不产出承载力结论。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
SRC = ROOT / "runs" / "ACCEPT" / "FD002" / "snapshots"
DST = ROOT / "runs" / "V5_inputs"

BUSES = [
    ("BUS-HV-220", "220kV母线", "FD-V5", None, 220, "ABC", "wye", None),
    ("BUS-HV-110", "110kV母线", "FD-V5", None, 110, "ABC", "wye", None),
    ("BUS-MV-035", "35kV母线", "FD-V5", None, 35, "ABC", "wye", None),
    ("BUS-MV-010", "10kV母线", "FD-V5", None, 10, "ABC", "wye", None),
    ("BUS-LV-040", "0.4kV母线", "FD-V5", None, 0.4, "ABC", "wye", None),
    ("BUS-HV-110B", "110kV出线", "FD-V5", None, 110, "ABC", "wye", None),
    ("BUS-MV-035B", "35kV出线", "FD-V5", None, 35, "ABC", "wye", None),
    ("BUS-MV-010B", "10kV出线", "FD-V5", None, 10, "ABC", "wye", None),
    ("BUS-LV-040B", "0.4kV末端", "FD-V5", None, 0.4, "ABC", "wye", None),
]

LINES = [
    ("LN-110-01", "BUS-HV-110", "BUS-HV-110B", "ABC", 8, 0.08, 0.40, 0.24, 1.20, 10, 5, 1200, True, None),
    ("LN-035-01", "BUS-MV-035", "BUS-MV-035B", "ABC", 5, 0.17, 0.38, 0.51, 1.14, 10, 5, 600, True, None),
    ("LN-010-01", "BUS-MV-010", "BUS-MV-010B", "ABC", 3, 0.27, 0.35, 0.81, 1.05, 10, 5, 400, True, None),
    ("LN-040-01", "BUS-LV-040", "BUS-LV-040B", "ABC", 0.3, 0.64, 0.08, 1.92, 0.24, 10, 5, 200, True, None),
]

TRAFOS = [
    ("T-220-110", "BUS-HV-220", "BUS-HV-110", 240000, 220, 110, 12, 0.5, 120, 0.3, "YNyn0", "ABC", "hv", 0, 0, 1.25, None, True),
    ("T-110-035", "BUS-HV-110", "BUS-MV-035", 50000, 110, 35, 10, 0.6, 30, 0.4, "YNd11", "ABC", "hv", 0, 0, 1.25, None, True),
    ("T-035-010", "BUS-MV-035", "BUS-MV-010", 20000, 35, 10, 8, 0.7, 15, 0.5, "Dyn11", "ABC", "hv", 0, 0, 1.25, None, True),
    ("T-010-040", "BUS-MV-010", "BUS-LV-040", 630, 10, 0.4, 4, 1.2, 0.6, 0.5, "Dyn11", "ABC", "lv", 0, 0, 2.5, None, True),
]

LOADS = [
    ("L110", "BUS-HV-110B", "ABC", "wye", "LP001", "constant_pq", True),
    ("L035", "BUS-MV-035B", "ABC", "wye", "LP001", "constant_pq", True),
    ("L010", "BUS-MV-010B", "ABC", "wye", "LP002", "constant_pq", True),
    ("L040", "BUS-LV-040B", "ABC", "wye", "LP001", "constant_pq", True),
]

PVS = [
    ("PV-HV-110", "BUS-HV-110B", "ABC", "wye", 15000, 16500, "PP001", "fixed_pf", None, "FM-PV001", True),
    ("PV-MV-010", "BUS-MV-010B", "ABC", "wye", 1200, 1320, "PP002", "fixed_pf", None, "FM-PV002", True),
    ("PV-LV-040", "BUS-LV-040B", "ABC", "wye", 150, 165, "PP001", "fixed_pf", None, "FM-PV001", True),
]

SOURCES = [
    ("SRC-V5", "BUS-HV-220", 1.0, 0, 0.5, 4.0, 1.5, 12.0, "MAX-MODE", "Z2-EQ-R1"),
]


def rewrite(path: Path) -> None:
    wb = load_workbook(path)

    def fill(sheet: str, rows: list[tuple]) -> None:
        ws = wb[sheet]
        header = [c.value for c in ws[1]]
        ws.delete_rows(2, ws.max_row)
        for r in rows:
            ws.append(list(r))

    fill("buses", BUSES)
    fill("lines", LINES)
    fill("transformers", TRAFOS)
    fill("loads", LOADS)
    fill("pv", PVS)
    fill("sources", SOURCES)

    ws = wb["meta"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value == "snapshot_id":
            row[1].value = "SNAP-V5"
    wb.save(path)


def main() -> None:
    if DST.exists():
        import os, stat
        for r, _d, fs in os.walk(DST):
            for f in fs:
                try:
                    os.chmod(os.path.join(r, f), stat.S_IWRITE)
                except Exception:
                    pass
        shutil.rmtree(DST, ignore_errors=True)
    shutil.copytree(SRC, DST)
    for junk in ("_build_inputs.py", "_verify_inputs.py"):
        p = DST / junk
        if p.exists():
            p.unlink()
    rewrite(DST / "network.xlsx")

    # task.json：候选接入点改为 10 kV 母线，范围收窄到 kW 级（先用 FD002 规则）
    import json
    t = json.loads((DST / "task.json").read_text(encoding="utf-8"))
    t["task_id"] = "TASK-V5"
    t["snapshot_id"] = "SNAP-V5"
    t["scope"] = {"type": "feeder", "ids": ["FD-V5"]}
    t["candidate_nodes"] = [{"node_id": "BUS-MV-010", "phase": "ABC", "weight": 1.0}]
    t["search"] = {"lower_kw": 0, "upper_kw": 5000, "precision_kw": 10, "max_iter": 25}
    (DST / "task.json").write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    print("五级输入包已生成：", DST)
    print("  母线 9 / 线路 4 / 变压器 4 / 负荷 4 / 光伏 3 / 电源 1")


if __name__ == "__main__":
    main()
