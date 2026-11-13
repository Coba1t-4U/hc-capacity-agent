# -*- coding: utf-8 -*-
"""最小验证：电源 + 变压器 + 负荷，确认母线基准电压与功率正确后，再验证三层结构。

上一轮的问题是所有母线基准电压都被设成 63.509 kV（110/√3），
怀疑两点：① brackets 列表里带空格导致解析异常；② VoltageBases 设定时机。
本脚本先跑三元件最小算例，通过后再跑三层结构。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
CASE = ROOT / "runs" / "E1_three_layer"

MINIMAL = """
Clear
New Circuit.T basekv=110 pu=1.0 phases=3
New Transformer.TR phases=3 windings=2 buses=[sourcebus.1.2.3,Sec.1.2.3] conns=[Delta,Wye] kvs=[110,10] kvas=[40000,40000]
New Load.LD phases=3 bus1=Sec.1.2.3 kW=1000 kvar=300 model=1
Set VoltageBases=[110, 10]
CalcVoltageBases
Set ControlMode=Static
Set Mode=Snap
Solve
"""

THREE = """
Clear
New Circuit.E1 basekv=110 pu=1.0 phases=3
New Transformer.T_MV phases=3 windings=2 buses=[sourcebus.1.2.3,MV.1.2.3] conns=[Delta,Wye] kvs=[110,10] kvas=[40000,40000]
New Linecode.LC_MV nphases=3 basefreq=50 r1=0.17 x1=0.38 r0=0.34 x0=0.76 normamps=400
New Line.L1 phases=3 bus1=MV bus2=MV2 length=2 units=km linecode=LC_MV
New Line.L2 phases=3 bus1=MV2 bus2=MV3 length=2.5 units=km linecode=LC_MV
New Line.L3 phases=3 bus1=MV3 bus2=MV4 length=1.5 units=km linecode=LC_MV
New Transformer.T1 phases=3 windings=2 buses=[MV2.1.2.3,LV1.1.2.3] conns=[Delta,Wye] kvs=[10,0.4] kvas=[630,630]
New Transformer.T2 phases=3 windings=2 buses=[MV4.1.2.3,LV2.1.2.3] conns=[Delta,Wye] kvs=[10,0.4] kvas=[400,400]
New Load.LD_MV phases=3 bus1=MV3.1.2.3 kW=600 kvar=200 model=1
New Load.LD1 phases=3 bus1=LV1.1.2.3 kW=380 kvar=120 model=1
New Load.LD2 phases=3 bus1=LV2.1.2.3 kW=220 kvar=70 model=1
New Generator.PV1 phases=3 bus1=LV1.1.2.3 kW=300 pf=1.0 model=1
New Generator.PV2 phases=3 bus1=LV2.1.2.3 kW=200 pf=1.0 model=1
Set VoltageBases=[110, 10, 0.4]
CalcVoltageBases
Set ControlMode=Static
Set Mode=Snap
Solve
"""


def run_block(dss, text: str, label: str) -> dict:
    for raw in text.strip().splitlines():
        cmd = raw.strip()
        if cmd and not cmd.startswith("!"):
            dss.Text.Command(cmd)
    dss.Text.Command("Solve")
    rows = []
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        kv = dss.Bus.kVBase()
        pu = dss.Bus.puVmagAngle()[0::2]
        rows.append((name, round(kv, 3), round(min(pu), 4) if pu else None))
    tp = dss.Circuit.TotalPower()
    res = {"converged": bool(dss.Solution.Converged()),
           "iterations": dss.Solution.Iterations(),
           "total_kw": round(tp[0], 1), "total_kvar": round(tp[1], 1),
           "buses": rows}
    print(f"\n[{label}] 收敛={res['converged']} 迭代={res['iterations']} "
          f"总功率={res['total_kw']} kW / {res['total_kvar']} kvar")
    for n, kv, v in rows:
        print(f"   {n:<8} {kv:>8} kV   {v}")
    return res


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from hc_agent import runtime_env
    runtime_env.ensure_engine_on_path()
    import opendssdirect as dss

    CASE.mkdir(parents=True, exist_ok=True)
    (CASE / "minimal.dss").write_text(MINIMAL.strip() + "\n", encoding="utf-8")
    (CASE / "three_layer.dss").write_text(THREE.strip() + "\n", encoding="utf-8")

    r1 = run_block(dss, MINIMAL, "最小三元件")
    ok1 = r1["converged"] and r1["total_kw"] < -900   # 负荷 1000 kW，受电应为负
    print("  最小算例判定:", "通过" if ok1 else "未通过")

    if not ok1:
        (CASE / "e1_result.json").write_text(
            json.dumps({"minimal": r1}, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    r2 = run_block(dss, THREE, "三层结构")
    vmin = min(v for _, _, v in r2["buses"] if v is not None)
    vmax = max(v for _, _, v in r2["buses"] if v is not None)
    ok2 = r2["converged"] and r2["total_kw"] < -1500 and 0.85 <= vmin and vmax <= 1.15
    print(f"  电压范围 {vmin} ~ {vmax} pu　三层判定: {'通过' if ok2 else '未通过'}")

    (CASE / "e1_result.json").write_text(
        json.dumps({"minimal": r1, "three_layer": r2,
                    "ok_minimal": ok1, "ok_three_layer": ok2},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
