# -*- coding: utf-8 -*-
"""Verify 第二计算引擎（pandapower）接入。

覆盖四件事：
  1) 引擎注册表如实反映"两个引擎都可用"，且各自口径写清；
  2) 适配器能从同一份输入包真建网、真求解（不是只有壳）；
  3) 交叉校核与 OpenDSS 产物对得上（网损/电压），且**不参与容量判定**；
  4) 验收矩阵的「适配引擎数」来自注册表，不是写死的 1。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 74)
print("1) 引擎注册表")
print("=" * 74)
from hc_agent.engines import registry as R

entries = R.adapters()
ids = [e["engine_id"] for e in entries]
check("注册了两个引擎", len(entries) == 2, str(ids))
check("含 opendss 与 pandapower", {"opendss", "pandapower"} <= set(ids), str(ids))
check("两个都可用", R.count_available() == 2, str(R.count_available()))

primary = [e for e in entries if e.get("role") == "primary"]
cross = [e for e in entries if e.get("role") == "crosscheck"]
check("恰好一个主算引擎", len(primary) == 1, str(len(primary)))
check("主算是 OpenDSS", primary and primary[0]["engine_id"] == "opendss")
check("恰好一个交叉校核引擎", len(cross) == 1, str(len(cross)))
check("交叉校核是 pandapower",
      cross and cross[0]["engine_id"] == "pandapower")
check("pandapower 口径写明是正序平衡",
      cross and "正序平衡" in cross[0].get("scope_label", ""),
      cross[0].get("scope_label") if cross else "")
check("交叉校核标注不参与容量判定",
      cross and "不参与" in cross[0].get("role_label", ""),
      cross[0].get("role_label") if cross else "")

_sum = R.summary()
check("摘要计数为 2", _sum["count"] == 2, str(_sum["count"]))
check("摘要点名两个引擎",
      "OpenDSS" in _sum["names"] and "pandapower" in _sum["names"],
      _sum["names"])

print()
print("=" * 74)
print("2) 适配器：真建网、真求解")
print("=" * 74)
from hc_agent.engines import pandapower_adapter as PA

info = PA.available()
check("pandapower 可用", info.get("available"), str(info.get("error"))[:80])
if info.get("available"):
    check("版本号可读", bool(info.get("version")), str(info.get("version")))
    # pandas 可能已被主流程导入（3.x），此时 pandapower 沿用已加载版本。
    # 实测 pandapower 3.5.6 在 pandas 3.0.1 下建网/求解/校核全部正常，
    # 所以这里只要求 >= 2.3，不强制必须是 2.3.x。
    _pv = str(info.get("pandas_version") or "0")
    _pvt = tuple(int(x) for x in _pv.split(".")[:2] if x.isdigit()) or (0, 0)
    check("pandas 版本满足 pandapower 要求（>=2.3）", _pvt >= (2, 3),
          _pv)

STAGE = ROOT / "runs" / "ACCEPT" / "FD002" / "_stage"
check("FD002 冻结输入存在", STAGE.is_dir(), str(STAGE))

if info.get("available") and STAGE.is_dir():
    ts_map = PA.pick_timestamps(STAGE)
    check("能自动挑出两个代表时刻", len(ts_map) == 2, str(ts_map))
    check("有光伏峰与负荷峰",
          "pv_peak" in ts_map and "load_peak" in ts_map, str(ts_map))

    net, meta = PA.build_net(STAGE, ts_map["load_peak"])
    check("建网后有母线", meta["buses"] == 5, str(meta["buses"]))
    check("建网后有线路", meta["lines"] == 3, str(meta["lines"]))
    check("建网后有配变", meta["trafo"] == 1, str(meta["trafo"]))
    check("负荷已加进网（不是 0）", meta["loads"] == 2, str(meta["loads"]))
    check("光伏已加进网", meta["sgen"] >= 1, str(meta["sgen"]))

    sol = PA.solve(net)
    check("潮流收敛", sol.get("converged"), str(sol)[:80])
    check("负荷功率非零（按 profile_id 取对了）",
          (sol.get("load_kw") or 0) > 100, str(sol.get("load_kw")))
    check("网损为正", (sol.get("losses_kw") or -1) > 0, str(sol.get("losses_kw")))
    check("电压在合理区间",
          sol.get("vmin_pu") is not None and 0.8 < sol["vmin_pu"] < 1.1,
          str(sol.get("vmin_pu")))

print()
print("=" * 74)
print("3) 交叉校核 vs OpenDSS 产物")
print("=" * 74)
RUN = ROOT / "runs" / "ACCEPT" / "FD002"
if info.get("available") and RUN.is_dir():
    rep = PA.crosscheck(RUN)
    check("校核有结论", rep.get("verdict") in ("match", "differ", "unavailable"),
          str(rep.get("verdict")))
    check("两个时刻都算了", len(rep.get("points") or []) == 2,
          str(len(rep.get("points") or [])))
    check("结论是 match（两引擎量级一致）", rep.get("verdict") == "match",
          str(rep.get("summary")))
    check("口径差异已写进结论",
          "口径" in str(rep.get("note")), str(rep.get("note"))[:60])
    check("注明不参与容量判定",
          "不参与容量判定" in str(rep.get("note")), str(rep.get("note"))[:60])
    for p in rep.get("points") or []:
        d = (p.get("diff") or {}).get("losses_kw")
        if d:
            check(f"{p['point']} 网损差 < 1 kW", d["abs"] < 1.0, str(d))
        dv = (p.get("diff") or {}).get("vmin_pu")
        if dv:
            check(f"{p['point']} 最低电压差 < 0.01 pu", dv["abs"] < 0.01, str(dv))

    # 关键：交叉校核不得改动任何容量结论
    summ = json.loads((RUN / "results" / "run_summary.json").read_text(
        encoding="utf-8"))
    before = summ.get("capacity_physical_kw")
    PA.crosscheck(RUN)
    summ2 = json.loads((RUN / "results" / "run_summary.json").read_text(
        encoding="utf-8"))
    check("交叉校核不改容量结论",
          before == summ2.get("capacity_physical_kw"),
          f"{before} -> {summ2.get('capacity_physical_kw')}")

    txt = PA.format_crosscheck(rep)
    check("可读文本含『交叉校核』", "交叉校核" in txt, txt[:40])
    check("可读文本含网损对比", "网损" in txt, txt[:40])

print()
print("=" * 74)
print("4) 验收矩阵的引擎数来自注册表")
print("=" * 74)
from hc_agent import acceptance as A

res = A.single_run(RUN)
row = next((x for x in res.get("items", [])
            if x.get("key") == "engine_count"), None)
check("矩阵里有 engine_count", row is not None)
if row:
    check("计数为 2", row.get("value") == 2, str(row.get("value")))
    check("判定为达标", row.get("status") == "pass", str(row.get("status")))
    check("说明里点名了两种引擎",
          "OpenDSS" in row.get("detail", "")
          and "pandapower" in row.get("detail", ""),
          str(row.get("detail"))[:70])

print()
print("=" * 74)
print("5) 输入框里的引擎选择")
print("=" * 74)
from hc_agent.engines import selector as S

check("三种模式", len(S.MODES) == 3, str([m["id"] for m in S.MODES]))
check("默认 OpenDSS", S.DEFAULT_MODE == "opendss", S.DEFAULT_MODE)
_old_mode = S.get_mode()
S.set_mode("both")
check("切换能持久化", S.get_mode() == "both", S.get_mode())
check("非法值回退默认", S.set_mode("bogus") == "opendss", S.get_mode())

_ans = {"text": "容量 333.98 kW", "source": str(RUN), "engine": "rule"}
S.set_mode("opendss")
_o = S.apply_mode(dict(_ans))
check("OpenDSS 模式不改答案", _o["text"] == _ans["text"], _o["text"][:40])
S.set_mode("both")
_b = S.apply_mode(dict(_ans))
check("双引擎模式追加交叉校核", "交叉校核" in _b["text"], _b["text"][:60])
check("双引擎模式保留原答案", _b["text"].startswith(_ans["text"]), _b["text"][:40])
S.set_mode("pandapower")
_p = S.apply_mode(dict(_ans))
check("pandapower 模式声明不产出容量",
      "不产出开放容量" in _p["text"], _p["text"][:60])
check("pandapower 模式首行就点明口径",
      _p["text"].startswith("【pandapower 模式】"), _p["text"][:40])
S.set_mode(_old_mode)

print()
print("=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
