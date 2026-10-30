"""Verify the extended agent: process view, OpenDSS case analysis, report button.

Three capabilities added on request:
  1. 运算过程（阶段轨迹 / 容量搜索 / 分场景结果）
  2. OpenDSS 原生算例分析（.dss）—— 直接跑引擎，不重建模型
  3. 每次运行可打开计算报告
"""
from __future__ import annotations

import json
import os
import re
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
print("过程视图 / OpenDSS 算例 / 报告按钮 验证")
print("=" * 74)

# ---------------------------------------------------------------- 1. 过程数据
print("\n1) 运算过程数据（阶段 / 搜索 / 场景）")
from hc_agent.desktop import process as P
from hc_agent.desktop import runs as R

run_dir = R.resolve_run_dir("FD002") or R.resolve_run_dir("DESKTOP")
have_run = run_dir is not None
check("能定位一次运行", have_run, str(run_dir))
if have_run:
    p = P.load(run_dir)
    check("读到阶段轨迹", len(p.stages) >= 5, f"{len(p.stages)} 个")
    check("阶段有中文标签", all(s.label for s in p.stages))
    check("读到搜索轨迹", len(p.trials) >= 5, f"{len(p.trials)} 次")
    check("搜索轨迹保留原始顺序（seq 连续）",
          [getattr(t, "seq", 0) for t in p.trials] ==
          list(range(1, len(p.trials) + 1)),
          str([getattr(t, 'seq', 0) for t in p.trials][:5]))

    # 时间场景 vs 短路校核必须分开
    ids = {s.scenario_id for s in p.scenarios}
    check("识别出时间场景", len(p.time_scenarios) >= 1,
          str([s.scenario_id for s in p.time_scenarios]))
    if "SC" in ids:
        check("短路校核不混进时间场景",
              all(s.scenario_id != "SC" for s in p.time_scenarios))
        check("短路校核单独归类", len(p.special_blocks) >= 1)

    # 分场景统计必须与原始 CSV 一致
    import csv
    csvp = run_dir / "results" / "constraint_results.csv"
    if csvp.exists():
        with open(csvp, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        total = sum(len(s.rows) for s in p.scenarios)
        check("分场景明细行数与 CSV 一致", total == len(rows),
              f"{total} vs {len(rows)}")
        # 每个场景的最差电压必须真能对上
        for sc in p.time_scenarios:
            vals = [float(r["value"]) for r in sc.rows
                    if r["constraint_id"] == "VOLTAGE_LIMIT"
                    and r.get("value")]
            if vals and sc.worst_voltage() is not None:
                check(f"{sc.scenario_id} 最低电压与明细一致",
                      abs(min(vals) - sc.worst_voltage()) < 1e-9,
                      f"{min(vals)} vs {sc.worst_voltage()}")

# ---------------------------------------------------------------- 2. 报告按钮
print("\n2) 计算报告可定位/可打开")
if have_run:
    rid = run_dir.name
    rep = R.find_report(rid)
    check(f"{rid} 能找到报告", rep is not None,
          "没有 report.docx")
    if rep:
        check("报告在运行目录内", str(rep.resolve()).startswith(str(run_dir.resolve())),
              str(rep))
    # 不真的去启动 Word（会弹窗），只验证拒绝越界路径
    bad = R.open_report("../../hc_agent")
    check("拒绝打开越界路径", bad.get("ok") is False, str(bad)[:100])

# ---------------------------------------------------------------- 3. OpenDSS 算例
print("\n3) OpenDSS 原生算例分析（.dss）")
from hc_agent.io import dss_case

SAMPLE = (ROOT.parent / "opendss_demo" / "examples" / "13Bus"
          / "IEEE13Nodeckt.dss")
if SAMPLE.is_file():
    a = dss_case.analyze(SAMPLE)
    check("求解收敛", a.converged is True)
    check("母线数正确（IEEE13 = 16）", a.bus_count == 16, f"{a.bus_count}")
    check("支路非空", len(a.branches) > 0, f"{len(a.branches)}")
    # 与独立验证过的读数核对
    check("总负荷 3466 kW", abs(a.total_load_kw - 3466.0) < 1.0,
          f"{a.total_load_kw}")
    check("网损 ≈101.02 kW", abs(a.losses_kw - 101.02) < 0.5,
          f"{a.losses_kw}")
    check("网损率 ≈2.9%", abs(a.losses_pct - 2.91) < 0.2,
          f"{a.losses_pct}")
    wv = a.worst_voltage()
    check("最低电压 0.96085 pu @ 611",
          wv is not None and abs(wv.vmin_pu - 0.96085) < 1e-4
          and wv.name == "611",
          f"{wv.vmin_pu if wv else None} @ {wv.name if wv else None}")
    wb = a.worst_branch()
    check("最大负载率来自实际支路", wb is not None and wb.name == "650632",
          f"{wb.name if wb else None}")
    check("过载支路被识别", len(a.overloaded()) >= 1,
          str(len(a.overloaded())))

    # 问答摘要必须带上"没有开放容量"的声明
    q = dss_case.summarize_for_qa(a)
    check("摘要含容量口径声明",
          "不产出开放容量" in str(q.get("capacity_note")),
          str(q.get("capacity_note"))[:80])
    check("摘要数字与实测一致",
          abs(q["min_voltage_pu"] - 0.96085) < 1e-4,
          str(q.get("min_voltage_pu")))
else:
    print(f"  (跳过：找不到样例 {SAMPLE})")

# ---------------------------------------------------------------- 4. 路径解析
print("\n4) 算例路径解析（文件 / 目录 / Master.dss）")
tmp = ROOT / "runs" / "_dss_path_test"
tmp.mkdir(parents=True, exist_ok=True)
(tmp / "Master.dss").write_text("Clear\n", encoding="utf-8")
(tmp / "sub.dss").write_text("Clear\n", encoding="utf-8")
check("目录 → Master.dss", dss_case.resolve_master(tmp).name == "Master.dss")
check("子文件 → 优先同目录 Master.dss",
      dss_case.resolve_master(tmp / "sub.dss").name == "Master.dss")
try:
    dss_case.resolve_master(tmp / "nope.dss")
    check("不存在的文件应报错", False)
except Exception:
    check("不存在的文件应报错", True)
try:
    dss_case.resolve_master(ROOT / "hc_agent")
    check("无 .dss 的目录应报错", False)
except Exception:
    check("无 .dss 的目录应报错", True)
import shutil
shutil.rmtree(tmp, ignore_errors=True)

# ---------------------------------------------------------------- 5. 界面接线
print("\n5) 界面接线（原生 Tk）")
pages_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(encoding="utf-8")
app_src = (ROOT / "hc_agent" / "desktop" / "app.py").read_text(encoding="utf-8")
dss_src = (ROOT / "hc_agent" / "desktop" / "dss_page.py").read_text(encoding="utf-8")
check("导航含「运算过程」", '("process", "运算过程")' in app_src)
check("导航含「算例分析」", '("dss", "算例分析")' in app_src)
check("ProcessPage 已注册", '"process": ProcessPage' in pages_src)
check("DssCasePage 已注册", '"dss": DssCasePage' in pages_src)
check("运行记录有「打开计算报告」", "打开计算报告" in pages_src)
check("运行记录有「查看运算过程」", "查看运算过程" in pages_src)
check("算例页是原生控件（无 HTML）",
      "<html" not in dss_src and "<div" not in dss_src)
check("算例页直接调用引擎", "dss_case.analyze" in dss_src)
check("算例页可在后台线程跑", "threading.Thread" in dss_src)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
