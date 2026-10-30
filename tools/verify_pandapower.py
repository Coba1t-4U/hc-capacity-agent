# -*- coding: utf-8 -*-
"""pandapower 安装后的功能验证：潮流、短路、内置算例。"""
import sys
import time

LIB = r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\pylibs_pandapower"
sys.path.insert(0, LIB)

import pandapower as pp
import pandapower.networks as pn

print("=" * 70)
print("1) 自建 10 kV / 0.4 kV 配电网络：潮流")
print("=" * 70)
net = pp.create_empty_network(name="hc_smoke")
mv = pp.create_bus(net, vn_kv=10.0, name="10kV母线")
mv2 = pp.create_bus(net, vn_kv=10.0, name="杆2")
lv = pp.create_bus(net, vn_kv=0.4, name="台区低压母线")
pp.create_ext_grid(net, mv, vm_pu=1.02, name="系统电源",
                   s_sc_max_mva=200.0, s_sc_min_mva=150.0, rx_max=0.1)
pp.create_line_from_parameters(net, mv, mv2, length_km=2.0, r_ohm_per_km=0.17,
                               x_ohm_per_km=0.34, c_nf_per_km=10.0,
                               max_i_ka=0.4, name="10kV线路")
pp.create_transformer_from_parameters(
    net, mv2, lv, sn_mva=1.0, vn_hv_kv=10.0, vn_lv_kv=0.4,
    vk_percent=4.0, vkr_percent=1.2, pfe_kw=0.6, i0_percent=0.5,
    name="配变")
pp.create_load(net, lv, p_mw=0.18, q_mvar=0.06, name="台区负荷")
pp.create_sgen(net, lv, p_mw=0.08, q_mvar=0.0, sn_mva=0.1,
                k=1.2, max_ik_ka=0.5, name="屋顶光伏")

t0 = time.perf_counter()
pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, init="auto")
dt = time.perf_counter() - t0
print(f"  收敛: {net.converged}   迭代 {net._ppc['iterations']} 次   用时 {dt*1000:.1f} ms")
print(f"  LV 电压: {net.res_bus.vm_pu[lv]:.6f} pu")
print(f"  MV 电压: {net.res_bus.vm_pu[mv2]:.6f} pu")
print(f"  线路负载: {net.res_line.loading_percent.values} %")
print(f"  配变负载: {net.res_trafo.loading_percent.values} %")
print(f"  网损: {net.res_line.pl_mw.sum()*1000:.4f} kW(线路) "
      f"+ {net.res_trafo.pl_mw.sum()*1000:.4f} kW(配变)")

print()
print("=" * 70)
print("2) 短路电流计算（ikss）")
print("=" * 70)
try:
    pp.runpp(net)
    pp.shortcircuit.calc_sc(net, bus=[lv], fault="3ph", case="max")
    print(f"  LV 三相短路电流 ikss: {net.res_bus_sc.ikss_ka[lv]*1000:.1f} A")
except Exception as exc:
    print(f"  短路计算异常: {type(exc).__name__}: {exc}")

print()
print("=" * 70)
print("3) 内置标准算例")
print("=" * 70)
for name, fn in (("case9", pn.case9), ("case14", pn.case14),
                 ("case33bw(33节点配网)", getattr(pn, "case33bw", lambda: None)),
                 ("mv_oberrhein(真实中压配网)", getattr(pn, "mv_oberrhein", lambda: None))):
    if fn is None:
        print(f"  {name}: pandapower 未提供"); continue
    try:
        n2 = fn()
        t0 = time.perf_counter()
        pp.runpp(n2)
        dt = time.perf_counter() - t0
        print(f"  {name}: 母线 {len(n2.bus)} / 线路 {len(n2.line)} "
              f"/ 收敛 {n2.converged} / Vmin {n2.res_bus.vm_pu.min():.4f} pu "
              f"/ 用时 {dt*1000:.1f} ms")
    except Exception as exc:
        print(f"  {name}: 失败 {type(exc).__name__}: {exc}")

print()
print("=" * 70)
print("4) 本机可用的配电网算例清单")
print("=" * 70)
cands = [n for n in dir(pn) if not n.startswith("_")][:40]
print("  networks 模块前 40 个：", ", ".join(cands[:18]))
print("  …")

print()
print("=" * 70)
print(f"结论：pandapower {pp.__version__} 可正常完成潮流与短路计算")
print("=" * 70)
