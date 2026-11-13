# -*- coding: utf-8 -*-
"""E1：构造主—配—微三层算例并跑通潮流，输出算例文件与参数表。

层次结构：
  主网层：110 kV 等值电源（Thevenin）+ 110/10 kV 主变
  配网层：10 kV 馈线 3 段（架空线），沿线 2 个配变台区
  微网层：2 个微网单元（光伏 + 储能），分别接在两个台区低压侧

刻意自建而非改造 IEEE 123：本机对 IEEE 123 做注入测试时出现"加 1000 kW 负荷仅响应 1/8"的
异常（原因未定位），继续在其上叠加微网会把问题带入实验结论。自建算例参数完全可控。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
CASE = ROOT / "runs" / "E1_three_layer"
OUT_DOCX = Path.home() / "Desktop" / "配网承载力" / "论文_实验E1_三层算例参数表.docx"

DSS = r"""
Clear
New Circuit.E1 basekv=110 pu=1.0 phases=3
! ---------- 主网层：等值电源 + 主变 ----------
New Transformer.T_MV phases=3 windings=2 buses=[E1.1.2.3, MV.1.2.3] conns=[Delta, Wye] kvs=[110, 10] kvas=[40000, 40000]
! ---------- 配网层：三段馈线 ----------
New Linecode.LC_MV nphases=3 basefreq=50 r1=0.17 x1=0.38 r0=0.34 x0=0.76 c1=10 c0=5 normamps=400
New Line.L1 phases=3 bus1=MV bus2=MV2 length=2 units=km linecode=LC_MV
New Line.L2 phases=3 bus1=MV2 bus2=MV3 length=2.5 units=km linecode=LC_MV
New Line.L3 phases=3 bus1=MV3 bus2=MV4 length=1.5 units=km linecode=LC_MV
! 两个配变台区
New Transformer.T1 phases=3 windings=2 buses=[MV2.1.2.3, LV1.1.2.3] conns=[Delta, Wye] kvs=[10, 0.4] kvas=[630, 630]
New Transformer.T2 phases=3 windings=2 buses=[MV4.1.2.3, LV2.1.2.3] conns=[Delta, Wye] kvs=[10, 0.4] kvas=[400, 400]
! 台区负荷
New Load.LD1 phases=3 bus1=LV1.1.2.3 kW=380 kvar=120 model=1
New Load.LD2 phases=3 bus1=LV2.1.2.3 kW=220 kvar=70 model=1
! 中压侧负荷
New Load.LD_MV phases=3 bus1=MV3.1.2.3 kW=600 kvar=200 model=1
! ---------- 微网层：两个单元（光伏 + 储能） ----------
New Generator.PV1 phases=3 bus1=LV1.1.2.3 kW=300 pf=1.0 model=1
New Generator.PV2 phases=3 bus1=LV2.1.2.3 kW=200 pf=1.0 model=1
New Storage.BESS1 phases=3 bus1=LV1.1.2.3 kWhrated=200 kWrated=100 %reserve=10 dispmode=follow
New Storage.BESS2 phases=3 bus1=LV2.1.2.3 kWhrated=100 kWrated=50 %reserve=10 dispmode=follow
Set VoltageBases=[110, 10, 0.4]
CalcVoltageBases
Set Mode=Snap
Set ControlMode=Static
Solve
"""


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from hc_agent import runtime_env
    runtime_env.ensure_engine_on_path()
    import opendssdirect as dss

    CASE.mkdir(parents=True, exist_ok=True)
    master = CASE / "E1_three_layer.dss"
    master.write_text(DSS.strip() + "\n", encoding="utf-8")

    for line in DSS.strip().splitlines():
        cmd = line.strip()
        if cmd and not cmd.startswith("!"):
            dss.Text.Command(cmd)

    conv = dss.Solution.Converged()
    iters = dss.Solution.Iterations()
    nb = dss.Circuit.NumBuses()
    ne = dss.Circuit.NumCktElements()
    v = dss.Circuit.AllBusMagPu()
    tp = dss.Circuit.TotalPower()

    # 逐母线电压
    rows = []
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        try:
            pu = dss.Bus.puVmagAngle()[0::2]
            vmin = min(pu) if pu else None
        except Exception:
            vmin = None
        rows.append((name, round(dss.Bus.kVBase(), 3), vmin))

    result = {
        "converged": bool(conv),
        "iterations": iters,
        "buses": nb,
        "elements": ne,
        "v_min": round(min(v), 4) if v else None,
        "v_max": round(max(v), 4) if v else None,
        "total_kw": round(tp[0], 1),
        "total_kvar": round(tp[1], 1),
        "bus_table": [(n, kv, round(vm, 4) if isinstance(vm, float) else None)
                      for n, kv, vm in rows],
    }
    (CASE / "e1_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  收敛: {conv}  迭代: {iters}  母线: {nb}  元件: {ne}")
    print(f"  电压范围: {result['v_min']} ~ {result['v_max']} pu")
    print(f"  总功率: {result['total_kw']} kW / {result['total_kvar']} kvar")
    for n, kv, vm in result["bus_table"]:
        print(f"    {n:<6} {kv:>6} kV  {vm if vm is not None else '—'}")
    return 0 if conv else 1


if __name__ == "__main__":
    raise SystemExit(main())
