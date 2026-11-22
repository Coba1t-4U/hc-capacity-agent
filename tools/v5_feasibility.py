# -*- coding: utf-8 -*-
"""五电压等级可行性验证：220 / 110 / 35 / 10 / 0.4 kV 串接，各层挂负荷，末端挂光伏。

目的只有一个：确认 OpenDSS 能否正确建立多电压等级网络并收敛。
不产出承载力结论——判据与搜索区间尚未按电压等级改造。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
CASE = ROOT / "runs" / "V5_feasibility"

DECK = r"""
Clear
New Circuit.V5 basekv=220 pu=1.0 phases=3

! 220 -> 110 kV
New Transformer.T1 phases=3 windings=2 buses=[sourcebus.1.2.3,B110.1.2.3] conns=[wye,wye] kvs=[220,110] kvas=[240000,240000] xhl=12
! 110 -> 35 kV
New Transformer.T2 phases=3 windings=2 buses=[B110.1.2.3,B35.1.2.3] conns=[wye,delta] kvs=[110,35] kvas=[50000,50000] xhl=10
! 35 -> 10 kV
New Transformer.T3 phases=3 windings=2 buses=[B35.1.2.3,B10.1.2.3] conns=[delta,wye] kvs=[35,10] kvas=[20000,20000] xhl=8
! 10 -> 0.4 kV
New Transformer.T4 phases=3 windings=2 buses=[B10.1.2.3,B04.1.2.3] conns=[delta,wye] kvs=[10,0.4] kvas=[630,630] xhl=4

New Linecode.LC110 nphases=3 basefreq=50 r1=0.08 x1=0.40 normamps=1200
New Linecode.LC35  nphases=3 basefreq=50 r1=0.17 x1=0.38 normamps=600
New Linecode.LC10  nphases=3 basefreq=50 r1=0.27 x1=0.35 normamps=400
New Linecode.LC04  nphases=3 basefreq=50 r1=0.64 x1=0.08 normamps=200

New Line.L110 phases=3 bus1=B110 bus2=B110b length=8 units=km linecode=LC110
New Line.L35  phases=3 bus1=B35  bus2=B35b  length=5 units=km linecode=LC35
New Line.L10  phases=3 bus1=B10  bus2=B10b  length=3 units=km linecode=LC10
New Line.L04  phases=3 bus1=B04  bus2=B04b  length=0.3 units=km linecode=LC04

! 各层负荷
New Load.D220 phases=3 bus1=B110b.1.2.3 kW=60000 kvar=15000 model=1
New Load.D110 phases=3 bus1=B110b.1.2.3 kW=20000 kvar=6000 model=1
New Load.D35  phases=3 bus1=B35b.1.2.3 kW=8000 kvar=2500 model=1
New Load.D10  phases=3 bus1=B10b.1.2.3 kW=2000 kvar=600 model=1
New Load.D04  phases=3 bus1=B04b.1.2.3 kW=200 kvar=60 model=1

! 分布式光伏：110 kV 集中式、10 kV 中型、0.4 kV 屋顶
New Generator.PV110 phases=3 bus1=B110b.1.2.3 kW=15000 pf=1.0 model=1
New Generator.PV10  phases=3 bus1=B10b.1.2.3 kW=1200 pf=1.0 model=1
New Generator.PV04  phases=3 bus1=B04b.1.2.3 kW=150 pf=1.0 model=1

Set VoltageBases=[220, 110, 35, 10, 0.4]
CalcVoltageBases
Set ControlMode=Static
Set Mode=Snap
Solve
"""


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from hc_agent import runtime_env
    runtime_env.ensure_engine_on_path()
    import opendssdirect as dss

    CASE.mkdir(parents=True, exist_ok=True)
    (CASE / "v5.dss").write_text(DECK.strip() + "\n", encoding="utf-8")

    for line in DECK.strip().splitlines():
        cmd = line.strip()
        if cmd and not cmd.startswith("!"):
            dss.Text.Command(cmd)

    conv = dss.Solution.Converged()
    it = dss.Solution.Iterations()
    print(f"  收敛: {conv}  迭代: {it}")
    print(f"  母线数: {dss.Circuit.NumBuses()}  元件: {dss.Circuit.NumCktElements()}")

    print("  各母线电压：")
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        pu = dss.Bus.puVmagAngle()[0::2]
        kv = dss.Bus.kVBase()
        v = f"{min(pu):.4f}" if pu else "—"
        print(f"    {name:<10} {kv:>8.3f} kV   {v} pu")

    p, q = dss.Circuit.TotalPower()
    loss = dss.Circuit.Losses()
    print(f"  主网受电: {p:.0f} kW / {q:.0f} kvar")
    print(f"  网络损耗: {loss[0]/1000:.0f} kW")

    # 与负荷/电源汇总对照（判断功率平衡是否成立）
    dss.Loads.First()
    total_load = 0.0
    while True:
        total_load += dss.Loads.kW()
        if not dss.Loads.Next():
            break
    dss.Generators.First()
    total_gen = 0.0
    while True:
        total_gen += dss.Generators.kW()
        if not dss.Generators.Next():
            break
    print(f"  负荷合计: {total_load:.0f} kW  电源合计: {total_gen:.0f} kW")
    print(f"  理论受电应为: {total_load - total_gen + loss[0]/1000:.0f} kW")
    return 0 if conv else 1


if __name__ == "__main__":
    raise SystemExit(main())
