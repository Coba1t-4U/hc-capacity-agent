# -*- coding: utf-8 -*-
"""E2：加性检验——单点容量之和 vs 多点联合容量。

做法：以 FD002 输入包为模板，构造三个变体（只改 pv 表）：
  A：仅节点 LV-001 有光伏
  B：仅节点 LV-002 有光伏
  C：两节点各有光伏（联合）
分别跑容量评估，比较 C_A + C_B 与 C_C。
若 C_C < C_A + C_B，说明容量边界非加性（本实验要验证的现象）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
SRC = ROOT / "runs" / "ACCEPT" / "FD002"
OUT = ROOT / "runs" / "E2_additivity"
PY = sys.executable


def make_variant(tag: str, nodes: list[str]) -> Path:
    """复制输入包，按 nodes 重写 pv 表（每个节点一条，功率均分）。"""
    dst = OUT / tag
    if dst.exists():
        # 快照文件是只读的，rmtree 前先清掉只读位
        import os, stat
        for root, _dirs, files in os.walk(dst):
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), stat.S_IWRITE)
                except Exception:
                    pass
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(SRC / "snapshots", dst)
    # 复制会带上只读属性，改表前必须清掉
    import os, stat
    for root, _dirs, files in os.walk(dst):
        for f in files:
            try:
                os.chmod(os.path.join(root, f), stat.S_IWRITE)
            except Exception:
                pass
    import os
    for junk in ("_build_inputs.py", "_verify_inputs.py"):
        p = dst / junk
        if p.exists():
            try:
                os.chmod(p, 0o666)
                p.unlink()
            except Exception:
                pass    # 快照文件可能只读，留着也不影响评估

    wb = load_workbook(dst / "network.xlsx")
    ws = wb["pv"]
    header = [c.value for c in ws[1]]
    idx = {h: i + 1 for i, h in enumerate(header)}
    old = [[c.value for c in row] for row in ws.iter_rows(min_row=2) if row[0].value]
    total_kw = sum(float(r[idx["rated_p_kw"] - 1] or 0) for r in old) or 2000.0
    rated_s = float(old[0][idx["rated_s_kva"] - 1] or total_kw) if old else total_kw
    profile = old[0][idx["profile_id"] - 1] if old else "PV_STD"

    for r in range(ws.max_row, 1, -1):
        ws.delete_rows(r)
    for i, node in enumerate(nodes, 1):
        row = ws.max_row + 1
        ws.cell(row=row, column=idx["pv_id"], value=f"PV-E2-{i}")
        ws.cell(row=row, column=idx["node_id"], value=node)
        ws.cell(row=row, column=idx["phase"], value="ABC")
        ws.cell(row=row, column=idx["connection"], value="wye")
        ws.cell(row=row, column=idx["rated_p_kw"], value=round(total_kw / len(nodes), 3))
        ws.cell(row=row, column=idx["rated_s_kva"], value=round(rated_s / len(nodes), 3))
        ws.cell(row=row, column=idx["profile_id"], value=profile)
        ws.cell(row=row, column=idx["control_mode"], value="unity_pf")
    wb.save(dst / "network.xlsx")
    return dst


def run(bundle: Path, tag: str) -> dict:
    out = OUT / f"_run_{tag}"
    cmd = [PY, "-m", "hc_agent.bridge", "capacity_run",
           "--input", str(bundle), "--output", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=600)
    line = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else "{}"
    try:
        j = json.loads(line)
    except Exception:
        j = {"ok": False, "raw": line[:200], "stderr": (r.stderr or "")[-200:]}
    return j


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    nodes = ["BUS-LV-001", "BUS-LV-002"]

    variants = {"A_LV001": [nodes[0]], "B_LV002": [nodes[1]], "C_both": nodes}
    res = {}
    for tag, ns in variants.items():
        bundle = make_variant(tag, ns)
        j = run(bundle, tag)
        res[tag] = {"nodes": ns, "physical_kw": j.get("capacity_physical_kw"),
                    "binding": j.get("binding_constraint"), "ok": j.get("ok"),
                    "note": j.get("note") or j.get("raw")}
        print(f"  {tag:<9} 节点={len(ns)} 容量={j.get('capacity_physical_kw')} kW "
              f"绑约束={j.get('binding_constraint')}")

    a, b, c = (res["A_LV001"]["physical_kw"], res["B_LV002"]["physical_kw"],
               res["C_both"]["physical_kw"])
    summary = {"A": a, "B": b, "C": c}
    if all(isinstance(x, (int, float)) for x in (a, b, c)):
        s = a + b
        summary.update({"sum_AB": round(s, 3), "joint": c,
                        "ratio": round(c / s, 4),
                        "non_additive": c < s * 0.98})
        print(f"  A+B = {s:.3f} kW　联合 C = {c:.3f} kW　比值 {c/s:.4f}"
              f"　非加性: {c < s * 0.98}")
    (OUT / "e2_result.json").write_text(
        json.dumps({"variants": res, "summary": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("  结果：", OUT / "e2_result.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
