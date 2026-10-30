"""Verify the ported case converters (numerical rulings)."""
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '.')

from hc_agent.errors import InputBlockedError
from hc_agent.io.converters import (
    ZERO_IMPEDANCE_TOL, b_pu_to_c1_nf, check_zero_impedance, convert_case,
    load_kv_for_phase, ohm_to_pu, parse_phase_identity,
    transformer_percent_impedance,
)
from hc_agent.io.schema import IssueCollector

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


tmp = Path(tempfile.mkdtemp())

print("=" * 74)
print("1) 变压器阻抗裁定（移植自天枢一号，最高价值的一条）")
print("=" * 74)
r_pct, x_pct = transformer_percent_impedance(0.005, 0.04)
check("串联 %Rs 对半拆", abs(r_pct - 0.25) < 1e-12, f"got {r_pct}")
check("XHL 不拆", abs(x_pct - 4.0) < 1e-12, f"got {x_pct}")
check("两者不相等（证明确实拆分不同）", abs(r_pct - 2.0) > 1e-9)

print()
print("=" * 74)
print("2) 单相负荷 kV = 线电压/√3")
print("=" * 74)
check("3φ 用线电压", abs(load_kv_for_phase(0.4, "ABC") - 0.4) < 1e-12)
check("2φ 用线电压", abs(load_kv_for_phase(0.4, "AB") - 0.4) < 1e-12)
kv1 = load_kv_for_phase(0.4, "A")
check("1φ 用相电压", abs(kv1 - 0.4 / math.sqrt(3)) < 1e-9, f"got {kv1}")
check("1φ 比线电压小 √3 倍", abs(0.4 / kv1 - math.sqrt(3)) < 1e-9)

print()
print("=" * 74)
print("3) 标幺换算 Zbase = Vbase²/Sbase")
print("=" * 74)
r, x = ohm_to_pu(1.0, 2.0, 10.0, 100.0)   # Zbase = 100/100 = 1.0
check("10kV/100MVA 下 Zbase=1Ω", abs(r - 1.0) < 1e-12 and abs(x - 2.0) < 1e-12,
      f"got {r},{x}")
r2, _ = ohm_to_pu(0.5, 0.0, 0.4, 1.0)     # Zbase = 0.16/1 = 0.16
check("0.4kV/1MVA 下换算正确", abs(r2 - 0.5 / 0.16) < 1e-9, f"got {r2}")

print()
print("=" * 74)
print("4) 充电电纳 → 电容（频率不可写死）")
print("=" * 74)
c50 = b_pu_to_c1_nf(0.01, 1.0, 50.0)
c60 = b_pu_to_c1_nf(0.01, 1.0, 60.0)
check("50Hz 有值", c50 > 0, f"got {c50}")
check("60Hz 与 50Hz 不同", abs(c50 - c60) > 1e-6, f"{c50} vs {c60}")
check("f 越大 C 越小", c60 < c50)
check("b=0 时 C=0", b_pu_to_c1_nf(0.0, 1.0, 50.0) == 0.0)

print()
print("=" * 74)
print("5) 零阻抗门禁：只拦严格为零")
print("=" * 74)
check("阈值常量存在且为 1e-12", ZERO_IMPEDANCE_TOL == 1e-12)
try:
    check_zero_impedance(0.0, 0.0, "L1")
    check("r=x=0 应拒绝", False, "没抛异常")
except InputBlockedError:
    check("r=x=0 应拒绝", True)
try:
    check_zero_impedance(1e-13, 1e-13, "L2")
    check("1e-13/1e-13 应拒绝", False, "没抛异常")
except InputBlockedError:
    check("1e-13/1e-13 应拒绝", True)
try:
    check_zero_impedance(1e-9, 0.5, "L3")   # 只有一个极小
    check("1e-9 但 x 正常 -> 放行", True)
except InputBlockedError:
    check("1e-9 但 x 正常 -> 放行", False, "被误拦")
try:
    check_zero_impedance(1e-9, 1e-9, "L4")  # 都很小但非零
    check("1e-9/1e-9 -> 放行（非严格为零）", True)
except InputBlockedError:
    check("1e-9/1e-9 -> 放行（非严格为零）", False, "被误拦")
try:
    check_zero_impedance(1e-13, 0.0, "L5")
    check("r 极小 x=0 -> 应拒绝", False, "被放行了")
except InputBlockedError:
    # x 严格为 0，确实小于阈值；两个分量都在阈下 ⇒ 按判据应当拒绝。
    # "只拦严格为零"指的是**不拦非零小值**（见下一条），不是"必须恰好等于 0"。
    check("r 极小 x=0 -> 拒绝（x 严格为零）", True)

print()
print("=" * 74)
print("6) 相别身份解析（相数可默认，身份不可猜）")
print("=" * 74)
check("A -> (1,)", parse_phase_identity("A") == (1,))
check("AB -> (1,2)", parse_phase_identity("AB") == (1, 2))
check("ABC -> (1,2,3)", parse_phase_identity("ABC") == (1, 2, 3))
check("1.2 -> (1,2)", parse_phase_identity("1.2") == (1, 2))
check("a,b -> (1,2)", parse_phase_identity("a,b") == (1, 2))
check("A0 -> (1,0)", parse_phase_identity("A0") == (1, 0))
check("空 -> None", parse_phase_identity("") is None)
check("None -> None", parse_phase_identity(None) is None)
check("'5' 无法解析 -> None", parse_phase_identity("5") is None)

print()
print("=" * 74)
print("7) MATPOWER .m 转换（含核心裁定）")
print("=" * 74)
mfile = tmp / "case9.m"
mfile.write_text("""function mpc = case9
mpc.baseMVA = 100;
mpc.bus = [
    1 3   0    0   0 0 1 1.00 345  1 1.1 0.9;
    2 2   0    0   0 0 1 1.00 345  1 1.1 0.9;
    3 1 100   35   0 0 1 1.00 345  1 1.1 0.9;
    4 1  80   30   0 0 1 1.00  13.8 1 1.1 0.9;
];
mpc.gen = [
    1 0 0 300 -300 1.0;
    2 50 0 300 -300 1.02;
];
mpc.branch = [
    1 2 0.005 0.04 0.001 250 250 250 0 0 1;
    2 3 0.010 0.08 0.002 250 250 250 0 0 1;
    3 4 0.005 0.05 0.000 100 100 100 0.98 0 1;
];
""", encoding="utf-8")
col = IssueCollector()
bundle, rep = convert_case(mfile, collector=col)
check("转换成功", len(bundle.buses) == 4, f"buses={len(bundle.buses)}")
check("识别出 3 条支路", len(bundle.lines) + len(bundle.transformers) == 3,
      f"lines={len(bundle.lines)} xfmr={len(bundle.transformers)}")
check("按电压等级判定变压器（345->13.8）", len(bundle.transformers) == 1,
      f"xfmr={len(bundle.transformers)}")
check("变压器 kVA = 系统基准 100MVA", 
      bundle.transformers and abs(bundle.transformers[0]["sn_kva"] - 100000.0) < 1e-6,
      str(bundle.transformers[0]["sn_kva"] if bundle.transformers else None))
check("变压器阻抗由 %Rs 对半 / XHL 不拆 反算",
      bundle.transformers
      and abs(bundle.transformers[0]["vkr_percent"] - 0.5) < 1e-9,
      f"vkr={bundle.transformers[0]['vkr_percent'] if bundle.transformers else None}")
check("Vg 覆盖 Vm：母线2 的 Vg=1.02 已生效",
      bundle.sources
      and abs(bundle.sources[0].get("_vm_by_bus", {}).get("2", 0) - 1.02) < 1e-9,
      str(bundle.sources[0].get("_vm_by_bus") if bundle.sources else None))
check("电源电压取平衡母线自身（母线1 Vg=1.0）",
      bundle.sources and abs(bundle.sources[0]["vm_pu"] - 1.0) < 1e-9,
      str(bundle.sources[0]["vm_pu"] if bundle.sources else None))
check("无阻断缺陷", not col.blocking,
      "; ".join(f"{i.code}:{i.message[:40]}" for i in col.blocking))
check("报告含近似声明", rep.approximation is True)
check("报告含 kVA 基准说明",
      any("rateA" in n for n in rep.notes), str(rep.notes)[:120])

print()
print("=" * 74)
print("8) JSON 表格转换 + 零阻抗拒绝")
print("=" * 74)
j = {
    "bus": [
        {"bus_id": "1", "base_kv": 10.0, "type": "slack", "v_set_pu": 1.02},
        {"bus_id": "2", "base_kv": 10.0, "type": "pq", "p_kw": 500.0,
         "q_kvar": 150.0},
    ],
    "line": [
        {"line_id": "L1", "from_bus": "1", "to_bus": "2",
         "r_ohm": 0.5, "x_ohm": 1.0, "b_pu": 0.01},
    ],
}
jf = tmp / "case_tbl.json"
jf.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
col2 = IssueCollector()
b2, r2 = convert_case(jf, collector=col2)
check("JSON 转换成功", len(b2.buses) == 2 and len(b2.lines) == 1,
      f"buses={len(b2.buses)} lines={len(b2.lines)}")
check("负荷已提取", len(b2.loads) == 1, f"loads={len(b2.loads)}")
check("b_pu 已换算为 C1", b2.lines and b2.lines[0]["c1_nf_per_km"] > 0,
      str(b2.lines[0]["c1_nf_per_km"] if b2.lines else None))
check("JSON 无阻断", not col2.blocking)

# 零阻抗应拒绝
j_zero = dict(j)
j_zero["line"] = [{"line_id": "L0", "from_bus": "1", "to_bus": "2",
                   "r_ohm": 0.0, "x_ohm": 0.0}]
zf = tmp / "zero.json"
zf.write_text(json.dumps(j_zero), encoding="utf-8")
try:
    convert_case(zf, collector=IssueCollector())
    check("零阻抗应拒绝", False, "没抛异常")
except InputBlockedError:
    check("零阻抗应拒绝", True)

print()
print("=" * 74)
print("9) 不支持的格式应明确拒绝")
print("=" * 74)
try:
    convert_case(tmp / "x.parquet", collector=IssueCollector())
    check("parquet 应拒绝", False)
except InputBlockedError:
    check("parquet 应拒绝", True)

print()
print("=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
