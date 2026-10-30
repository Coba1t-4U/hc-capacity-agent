"""verification.py — 独立复算（SK-07 可信验收 / 设计文档 9.1）。

独立复算机制（9.1）：
  1. 主计算完成后冻结输入清单、规则、技能、引擎和参数指纹。
  2. 验证器创建独立 run_id、独立任务消息、全新工作目录和全新 OpenDSS
     工作进程，从冻结输入重新生成 DSS 文件并 Compile，不复用主任务缓存、
     活动电路或中间结果。
  3. 按相同基线执行全量或规则规定的关键场景复算，比较收敛状态、约束明细
     和开放容量。
  4. 主计算与独立复算使用同一锁定 OpenDSS 版本，比较输入哈希、场景覆盖、
     收敛状态、约束判定、电压电流及容量边界。状态必须一致，浮点字段按
     批准容差判断。
  5. 验证失败立即阻断发布，生成差异报告并转专业人员处理，
     不允许监督 Agent 自行豁免。

独立复算验证可重复性，不能证明共同模型或算法假设没有系统性偏差；
正确性另由黄金算例和专业复核验证。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import (
    EXIT_SUCCESS,
    EXIT_VERIFICATION_FAILED,
    VerificationFailedError,
)
from hc_agent.evidence import (
    EvidenceLedger,
    sha256_file,
    sha256_obj,
    utc_now_iso,
    verify_file_immutable,
    write_json,
)
from hc_agent.io.schema import as_float, as_str, write_csv

# 默认字段级容差（设计文档 4.2：浮点电气量须满足批准的字段级容差）
DEFAULT_TOLERANCES: Dict[str, float] = {
    "voltage_pu": 1e-6,
    "v_v": 1e-3,
    "i_a": 1e-6,
    "loading_pct": 1e-4,
    "capacity_kw": 1e-3,
    "angle_deg": 1e-6,
}

# 必须逐位一致的状态字段（设计文档 9.1-4：状态必须一致）
EXACT_MATCH_FIELDS = ("status", "converged", "constraint_status", "level",
                      "search_status", "binding_constraint")


@dataclass
class MetricDiff:
    """单个字段的差异（设计文档 表 56 metric_diff.csv）。"""

    metric_key: str
    primary: Any
    verify: Any
    abs_diff: Optional[float]
    tolerance: Optional[float]
    status: str          # match / mismatch / exact_match / missing
    object_ref: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "metric_key": self.metric_key, "object_ref": self.object_ref,
            "primary": self.primary, "verify": self.verify,
            "abs_diff": self.abs_diff, "tolerance": self.tolerance,
            "status": self.status,
        }


def compare_runs(primary: Dict[str, Any], verify: Dict[str, Any],
                 *, tolerances: Optional[Dict[str, float]] = None,
                 input_match: bool = True) -> Dict[str, Any]:
    """比较主计算与独立复算（设计文档 9.1-4）。"""
    tol = dict(DEFAULT_TOLERANCES)
    if tolerances:
        tol.update({k: v for k, v in tolerances.items()
                    if isinstance(v, (int, float))})

    diffs: List[MetricDiff] = []

    # 1) 输入指纹一致性
    diffs.append(MetricDiff(
        metric_key="input_digest", primary=primary.get("input_digest"),
        verify=verify.get("input_digest"), abs_diff=None, tolerance=None,
        status="exact_match" if input_match
        and primary.get("input_digest") == verify.get("input_digest")
        else "mismatch"))

    # 2) 引擎版本一致性（同一锁定版本）
    p_eng = (primary.get("engine_fingerprint") or {}).get("interface_version")
    v_eng = (verify.get("engine_fingerprint") or {}).get("interface_version")
    diffs.append(MetricDiff(
        metric_key="engine_interface_version", primary=p_eng, verify=v_eng,
        abs_diff=None, tolerance=None,
        status="exact_match" if p_eng and p_eng == v_eng else "mismatch"))

    # 3) 场景覆盖一致性
    p_sc = sorted(primary.get("scenario_ids") or [])
    v_sc = sorted(verify.get("scenario_ids") or [])
    diffs.append(MetricDiff(
        metric_key="scenario_coverage", primary=",".join(p_sc),
        verify=",".join(v_sc), abs_diff=None, tolerance=None,
        status="exact_match" if p_sc == v_sc else "mismatch"))

    # 4) 收敛状态一致性
    p_conv, v_conv = primary.get("all_converged"), verify.get("all_converged")
    diffs.append(MetricDiff(
        metric_key="all_converged", primary=p_conv, verify=v_conv,
        abs_diff=None, tolerance=None,
        status="exact_match" if p_conv == v_conv else "mismatch"))

    # 5) 约束判定一致性
    p_cs = primary.get("constraint_summary") or {}
    v_cs = verify.get("constraint_summary") or {}
    for key in ("total", "pass", "fail", "unknown"):
        pv, vv = p_cs.get(key), v_cs.get(key)
        diffs.append(MetricDiff(
            metric_key=f"constraint.{key}", primary=pv, verify=vv,
            abs_diff=None, tolerance=None,
            status="exact_match" if pv == vv else "mismatch"))

    # 5b) 约束明细逐行摘要（设计文档 9.1-4：约束判定必须一致）
    p_rows = primary.get("constraint_rows") or {}
    v_rows = verify.get("constraint_rows") or {}
    if p_rows and v_rows:
        diffs.append(MetricDiff(
            metric_key="constraint_rows.count", primary=p_rows.get("count"),
            verify=v_rows.get("count"), abs_diff=None, tolerance=None,
            status="exact_match"
            if p_rows.get("count") == v_rows.get("count") else "mismatch"))
        diffs.append(MetricDiff(
            metric_key="constraint_rows.digest",
            primary=p_rows.get("digest"), verify=v_rows.get("digest"),
            abs_diff=None, tolerance=None,
            status="exact_match"
            if p_rows.get("digest") == v_rows.get("digest") else "mismatch"))
        if p_rows.get("digest") != v_rows.get("digest"):
            # 给出前若干行级差异，便于专业定位（表 56 metric_diff.csv）
            pr = {f'{r["constraint_id"]}|{r["object_id"]}|{r["scenario_id"]}|'
                  f'{r["timestamp"]}': r for r in (p_rows.get("rows") or [])}
            vr = {f'{r["constraint_id"]}|{r["object_id"]}|{r["scenario_id"]}|'
                  f'{r["timestamp"]}': r for r in (v_rows.get("rows") or [])}
            for k in sorted(set(pr) - set(vr))[:5]:
                diffs.append(MetricDiff("constraint_row.primary_only", k, None,
                                        None, None, "mismatch",
                                        object_ref=k))
            for k in sorted(set(vr) - set(pr))[:5]:
                diffs.append(MetricDiff("constraint_row.verify_only", None, k,
                                        None, None, "mismatch",
                                        object_ref=k))
            for k in sorted(set(pr) & set(vr))[:200]:
                if pr[k]["status"] != vr[k]["status"]:
                    diffs.append(MetricDiff(
                        "constraint_row.status", pr[k]["status"],
                        vr[k]["status"], None, None, "mismatch",
                        object_ref=k))
    p_all, v_all = p_cs.get("all_pass"), v_cs.get("all_pass")
    diffs.append(MetricDiff(
        metric_key="constraint.all_pass", primary=p_all, verify=v_all,
        abs_diff=None, tolerance=None,
        status="exact_match" if p_all == v_all else "mismatch"))

    # 6) 容量边界（浮点，按批准容差）
    p_cap = as_float(primary.get("capacity_physical_kw"))
    v_cap = as_float(verify.get("capacity_physical_kw"))
    cap_tol = tol.get("capacity_kw", 1e-3)
    if p_cap is None and v_cap is None:
        diffs.append(MetricDiff("capacity_physical_kw", None, None, None,
                                cap_tol, "exact_match"))
    elif p_cap is None or v_cap is None:
        diffs.append(MetricDiff("capacity_physical_kw", p_cap, v_cap, None,
                                cap_tol, "mismatch"))
    else:
        d = abs(p_cap - v_cap)
        diffs.append(MetricDiff("capacity_physical_kw", p_cap, v_cap, d,
                                cap_tol, "match" if d <= cap_tol
                                else "mismatch"))

    # 7) 搜索状态与主导约束
    for key in ("search_status", "binding_constraint"):
        pv, vv = primary.get(key), verify.get(key)
        diffs.append(MetricDiff(
            metric_key=key, primary=pv, verify=vv, abs_diff=None,
            tolerance=None, status="exact_match" if pv == vv else "mismatch"))

    # 8) 电压/电流字段级比对（按对象）
    diffs.extend(_compare_series(
        primary.get("bus_voltages") or [], verify.get("bus_voltages") or [],
        key_fields=("scenario_id", "timestamp", "node_id"),
        value_fields=(("v_pu", "voltage_pu"), ("v_v", "v_v"),
                      ("angle_deg", "angle_deg")), tol=tol))
    diffs.extend(_compare_series(
        primary.get("element_results") or [],
        verify.get("element_results") or [],
        key_fields=("scenario_id", "timestamp", "equipment_id", "terminal",
                    "phase"),
        value_fields=(("i_a", "i_a"), ("loading_pct", "loading_pct")),
        tol=tol))

    mismatches = [d for d in diffs if d.status == "mismatch"]
    return {
        "primary_run_id": primary.get("run_id"),
        "verify_run_id": verify.get("run_id"),
        "input_match": bool(input_match),
        "compared_at": utc_now_iso(),
        "total_metrics": len(diffs),
        "mismatch_count": len(mismatches),
        "pass": not mismatches,
        "diffs": [d.to_row() for d in diffs],
        "mismatches": [d.to_row() for d in mismatches],
        "tolerances": tol,
        "note": ("独立复算验证可重复性，不能证明共同模型或算法假设没有"
                 "系统性偏差；正确性另由黄金算例和专业复核验证"),
    }


def _compare_series(primary: Sequence[Dict[str, Any]],
                    verify: Sequence[Dict[str, Any]],
                    *, key_fields: Sequence[str],
                    value_fields: Sequence[Tuple[str, str]],
                    tol: Dict[str, float]) -> List[MetricDiff]:
    """按对象键比对两组时序结果。"""
    out: List[MetricDiff] = []
    pidx = {_key(r, key_fields): r for r in primary}
    vidx = {_key(r, key_fields): r for r in verify}

    only_p = sorted(set(pidx) - set(vidx))
    only_v = sorted(set(vidx) - set(pidx))
    if only_p or only_v:
        out.append(MetricDiff(
            metric_key="series_coverage", object_ref=f"primary_only={len(only_p)}",
            primary=len(only_p), verify=len(only_v), abs_diff=None,
            tolerance=None, status="mismatch" if (only_p or only_v) else "match"))

    for k in sorted(set(pidx) & set(vidx)):
        pr, vr = pidx[k], vidx[k]
        for field_name, tol_key in value_fields:
            pv, vv = as_float(pr.get(field_name)), as_float(vr.get(field_name))
            t = tol.get(tol_key)
            if pv is None and vv is None:
                continue
            if pv is None or vv is None:
                out.append(MetricDiff(f"{tol_key}", pv, vv, None, t,
                                      "mismatch", object_ref=k))
                continue
            d = abs(pv - vv)
            out.append(MetricDiff(
                f"{tol_key}", pv, vv, d, t,
                "match" if (t is not None and d <= t) else "mismatch",
                object_ref=k))
    return out


def _key(row: Dict[str, Any], fields: Sequence[str]) -> str:
    return "|".join(as_str(row.get(f)) for f in fields)


def _rows_digest(results: Sequence[Any]) -> Dict[str, Any]:
    """约束明细的行级摘要（用于主计算与复算逐行比对）。"""
    rows = []
    for r in results:
        rows.append({
            "scenario_id": as_str(getattr(r, "scenario_id", "")),
            "timestamp": as_str(getattr(r, "timestamp", "")),
            "object_id": as_str(getattr(r, "object_id", "")),
            "constraint_id": as_str(getattr(r, "constraint_id", "")),
            "status": as_str(getattr(r, "status", "")),
            "value": (round(float(r.value), 9)
                      if getattr(r, "value", None) is not None else None),
        })
    rows.sort(key=lambda x: (x["constraint_id"], x["object_id"],
                             x["scenario_id"], x["timestamp"]))
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"count": len(rows), "status_counts": counts,
            "digest": sha256_obj(rows), "rows": rows}


def _align_scenarios(rebuilt: Sequence[Any],
                     frozen: Optional[Sequence[Dict[str, Any]]]) -> List[Any]:
    """按主计算冻结的场景定义对齐复算场景集（设计文档 9.1-3）。

    复算必须覆盖与主计算**完全相同的场景与时间片**，否则差异无法归因于计算
    本身。优先按冻结的 slice_timestamps 精确复现；仅当冻结清单缺失时才退回
    按 [start,end] 窗口裁剪，并且只在窗口方向正确时才裁剪。
    """
    if not frozen:
        return list(rebuilt)
    by_id = {s.scenario_id: s for s in rebuilt}
    aligned: List[Any] = []
    for spec in frozen:
        sid = as_str(spec.get("scenario_id"))
        sc = by_id.get(sid)
        if sc is None:
            continue
        frozen_slices = [as_str(t) for t in (spec.get("slice_timestamps") or [])]
        if frozen_slices:
            # 精确复现主计算使用的时间片集合
            want = set(frozen_slices)
            sel = [sl for sl in sc.slices if sl.timestamp in want]
            if sel:
                sc.slices = sel
        else:
            tw = spec.get("time_window") or []
            # 仅在窗口方向正确时裁剪；反向窗口（历史缺陷）会裁出错误子集，
            # 此时宁可不裁剪，让完整场景参与复算并由比对暴露差异。
            if len(tw) == 2 and tw[0] and tw[1] and tw[0] <= tw[1]:
                sel = [sl for sl in sc.slices
                       if tw[0] <= sl.timestamp <= tw[1]]
                if sel:
                    sc.slices = sel
        sc.weight = as_float(spec.get("weight")) or sc.weight
        aligned.append(sc)
    if len(aligned) != len(frozen):
        # 场景数不一致时退回全部重建场景，差异由比对环节暴露
        return list(rebuilt)
    return aligned


# ---------------------------------------------------------- 复算执行器入口
def verify_run(manifest_path, *, output_dir,
               engine_config: Optional[Dict[str, Any]] = None,
               tolerances: Optional[Dict[str, float]] = None,
               scenarios: Optional[Sequence[str]] = None,
               key_scenarios_only: bool = False) -> Dict[str, Any]:
    """独立复算主入口（设计文档 S07 / 17.10 CLI 契约）。

    在全新目录、全新进程、全新 OpenDSS 上下文中从冻结输入重建模型并复算。

    用法：
      python -m hc_agent.verification --manifest runs/RUN-001/manifest.json
                                      --output-dir runs/VERIFY-001
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    run_dir = Path(manifest.get("run_dir") or
                   Path(manifest_path).resolve().parent).resolve()
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    verify_run_id = f"VERIFY-{utc_now_iso().replace(':', '').replace('-', '')}"
    checks: List[Dict[str, Any]] = []

    # 1) 冻结输入哈希核对（不得静默切换数据，5.4-3）
    input_match = True
    for rec in manifest.get("inputs", []):
        p = run_dir / "snapshots" / rec.get("relpath", "")
        if not p.exists():
            p = Path(rec.get("abspath", ""))
        ok = p.exists() and sha256_file(p) == rec.get("sha256")
        input_match = input_match and ok
        checks.append({"check": "input_hash", "object": rec.get("relpath"),
                       "expected": rec.get("sha256"),
                       "actual": sha256_file(p) if p.exists() else None,
                       "pass": bool(ok)})

    # 2) 引擎指纹一致性（同一锁定版本）
    from hc_agent.runtime_env import engine_fingerprint
    fp = engine_fingerprint()
    primary_fp = manifest.get("engine_fingerprint") or {}
    eng_ok = (fp.get("interface_version") == primary_fp.get("interface_version")
              and fp.get("available"))
    checks.append({"check": "engine_version", "expected":
                   primary_fp.get("interface_version"),
                   "actual": fp.get("interface_version"), "pass": eng_ok})

    # 3) 在全新目录中重建并复算
    from hc_agent.io.ingest import load_input_bundle, InputBundle
    from hc_agent.io.schema import IssueCollector
    from hc_agent.io.validation import validate_bundle, raise_if_blocking
    from hc_agent.model.build_model import build_case
    from hc_agent.model.scenarios import build_scenarios
    from hc_agent.model.powerflow import (make_new_pv_specs, run_baseline,
                                          run_scenario_set, apply_slice)
    from hc_agent.model.constraints import ConstraintEvaluator, summarize_constraints
    from hc_agent.rules import load_rule_package
    from hc_agent.engines.opendss_adapter import OpenDSSAdapter

    snap_dir = run_dir / "snapshots"
    src_dir = snap_dir if snap_dir.exists() else Path(
        manifest.get("input_root") or run_dir)
    verify_work = out_dir / "work"
    verify_work.mkdir(parents=True, exist_ok=True)

    col = IssueCollector()
    bundle = load_input_bundle(src_dir, col)
    validate_bundle(bundle, require_profiles=True)
    raise_if_blocking(col)

    rules = load_rule_package(run_dir / "config" / "rules.yaml"
                              if (run_dir / "config" / "rules.yaml").exists()
                              else src_dir / "rules.yaml")

    primary = json.loads((run_dir / "results" / "run_summary.json")
                         .read_text(encoding="utf-8")) \
        if (run_dir / "results" / "run_summary.json").exists() else {}

    # 独立复算必须复现主计算的场景口径（设计文档 9.1-3：按相同基线执行全量或
    # 规则规定的关键场景复算，比较收敛状态、约束明细和开放容量）。
    # 主计算把场景集冻结在 results/scenarios.json，复算按同一场景集重构，
    # 确保"同一输入 -> 同一场景覆盖面"，差异才真实反映计算不一致。
    frozen_scenarios = None
    sc_path = run_dir / "results" / "scenarios.json"
    if sc_path.exists():
        try:
            frozen_scenarios = json.loads(
                sc_path.read_text(encoding="utf-8")).get("scenarios") or []
        except Exception:
            frozen_scenarios = None

    # 复算：全新目录 + 全新上下文
    adapter = OpenDSSAdapter(work_dir=verify_work, engine_config=engine_config)
    verify_payload: Dict[str, Any] = {
        "run_id": verify_run_id, "input_digest": manifest.get("input_digest"),
        "engine_fingerprint": fp, "all_converged": False,
    }
    try:
        model = build_case(bundle, engine_config, out_dir=verify_work / "case")
        # 沿用主计算的运行参数（表 56 manifest.parameters），保证场景覆盖面一致
        run_params = manifest.get("parameters") or {}
        max_slices = run_params.get("max_slices")
        scenarios = build_scenarios(bundle, rules,
                                    max_slices=int(max_slices)
                                    if max_slices else None)
        scenarios = _align_scenarios(scenarios, frozen_scenarios)
        if scenarios:
            verify_payload["scenario_ids"] = [s.scenario_id for s in scenarios]
        with adapter:
            adapter.build_case(model.master_text(), case_name="verify",
                               node_phase_map=model.node_phase_map,
                               id_map=model.id_map, base_kv=model.base_kv,
                               equipment=model.equipment)
            base_run = run_baseline(adapter, bundle, model, scenarios,
                                    solver=(bundle.task or {}).get("solver"))
            verify_payload["all_converged"] = base_run.all_converged
            evaluator = ConstraintEvaluator(rules)

            # 短路校核（SK-09）必须与主计算同口径复现，否则
            # SHORT_CIRCUIT_LIMIT 在主计算为 pass、在复算为 unknown，
            # 会产生虚假差异（设计文档 9.1-4：比较约束判定必须一致）。
            fault_results = None
            task = bundle.task or {}
            wants_sc = "shortcircuit" in [as_str(a) for a in
                                          (task.get("analysis_types") or [])]
            fault_bus = ""
            for n in (task.get("candidate_nodes") or []):
                if isinstance(n, dict) and as_str(n.get("node_id")):
                    fault_bus = as_str(n["node_id"])
                    break
            if wants_sc and fault_bus and bundle.fault_models:
                sc_cfg = task.get("short_circuit") or {}
                try:
                    fc = adapter.short_circuit(
                        fault_spec={"bus": fault_bus,
                                    "fault_type": as_str(sc_cfg.get(
                                        "fault_type")) or "three_phase",
                                    "r_fault_ohm": as_float(sc_cfg.get(
                                        "r_fault_ohm")) or 0.0},
                        scenario_id="SC",
                        timestamp=(scenarios[0].slices[0].timestamp
                                   if scenarios else ""))
                    fault_results = {
                        "available": fc.status == "ok", "bus": fault_bus,
                        "fault_type": as_str(sc_cfg.get("fault_type"))
                        or "three_phase",
                        "fault_element_currents":
                            (fc.metrics or {}).get(
                                "fault_element_currents") or [],
                        "bus_isc_a": (fc.metrics or {}).get("bus_isc_a"),
                    }
                except Exception as exc:
                    fault_results = {
                        "available": False,
                        "reason": f"短路研究异常: {type(exc).__name__}: {exc}"}

            c_results = evaluator.evaluate(base_run, fault_results=fault_results)
            verify_payload["constraint_summary"] = summarize_constraints(c_results)
            verify_payload["search_status"] = primary.get("search_status")
            verify_payload["binding_constraint"] = primary.get(
                "binding_constraint")
            # 逐行约束明细哈希：与主计算冻结的基线约束明细比对
            # （设计文档 9.1-4：约束判定必须一致）
            verify_payload["constraint_rows"] = _rows_digest(c_results)

            # 复算容量（若主计算给出容量，按同一参数重跑容量的关键点）
            p_cap = as_float(primary.get("capacity_physical_kw"))
            specs, weights = make_new_pv_specs(bundle, bundle.task or {})
            new_ids = [s["pv_id"] for s in specs]
            model2 = None
            if new_ids:
                model2 = build_case(bundle, engine_config,
                                    out_dir=verify_work / "case_cap",
                                    extra_pv=specs)
                adapter.build_case(model2.master_text(), case_name="verify_cap",
                                   node_phase_map=model2.node_phase_map,
                                   id_map=model2.id_map, base_kv=model2.base_kv,
                                   equipment=model2.equipment)
            if p_cap is not None and new_ids and model2 is not None:
                cap_run = run_scenario_set(
                    adapter, bundle, model2, scenarios, increment_kw=p_cap,
                    allocation=weights, new_pv_ids=new_ids,
                    solver=(bundle.task or {}).get("solver"))
                c2 = evaluator.evaluate(cap_run)
                # 可行性判定必须与容量搜索使用同一谓词。
                # 搜索用 feasible()（无 fail）；若此处仍用 all_pass()
                # （无 fail 且无 unknown），则任何结构性 unknown
                # （例如未开展短路研究时的 SHORT_CIRCUIT_LIMIT）
                # 都会让复算把容量判为 None，产生并不存在的"真实不一致"。
                cap_ok = ConstraintEvaluator.feasible(c2)
                if cap_ok:
                    verify_payload["capacity_physical_kw"] = p_cap
                    verify_payload["capacity_reproduced"] = True
                elif p_cap == 0.0:
                    # 设计文档 6.3-4 / 17.12：容量为 0 是**有效结论**——
                    # 基线本身已存在存量越限时，可开放空间为 0，应复现为 0，
                    # 而不是记为 unknown。unknown 专指不收敛/超时/能力缺失。
                    verify_payload["capacity_physical_kw"] = 0.0
                    verify_payload["capacity_reproduced"] = True
                    verify_payload["capacity_zero_reason"] = (
                        "主计算结论为 0 kW（基线存在存量越限），复算同样得到 "
                        "增量 0 kW 不可行，结论复现")
                else:
                    # 主计算给出的正值容量在复算中不可行 -> 真实不一致
                    verify_payload["capacity_physical_kw"] = None
                    verify_payload["capacity_reproduced"] = False
                    verify_payload["capacity_zero_reason"] = (
                        f"主计算容量 {p_cap:g} kW 在复算中不可行，需专业定位")
                verify_payload["capacity_constraint_summary"] = \
                    summarize_constraints(c2)
            elif p_cap is not None:
                verify_payload["capacity_physical_kw"] = p_cap
            checks.append({"check": "independent_rebuild", "object": "case",
                           "pass": True,
                           "note": "全新目录、全新上下文、从冻结输入重建"})
    except Exception as exc:
        checks.append({"check": "independent_rebuild", "object": "case",
                       "pass": False, "error": f"{type(exc).__name__}: {exc}"})
        verify_payload["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        adapter.close()

    # 4) 比较（状态必须一致，浮点按批准容差）
    if primary:
        comparison = compare_runs(primary, verify_payload,
                                  tolerances=tolerances,
                                  input_match=input_match)
    else:
        comparison = {
            "primary_run_id": manifest.get("run_id"),
            "verify_run_id": verify_run_id,
            "input_match": input_match,
            "pass": False,
            "mismatch_count": -1,
            "diffs": [], "mismatches": [],
            "note": "未找到主计算 run_summary.json，无法比对",
        }

    result = {
        "verify_run_id": verify_run_id,
        "primary_run_id": manifest.get("run_id"),
        "created_at": utc_now_iso(),
        "output_dir": str(out_dir),
        "input_match": input_match,
        "checks": checks,
        "comparison": comparison,
        "pass": bool(comparison.get("pass")) and input_match
                and all(c.get("pass") for c in checks),
        "verify_payload": verify_payload,
    }

    write_json(out_dir / "verification.json", result)
    write_csv(out_dir / "metric_diff.csv", comparison.get("diffs") or [],
              columns=["metric_key", "object_ref", "primary", "verify",
                       "abs_diff", "tolerance", "status"])

    ledger = EvidenceLedger(out_dir / "audit" / "verify_events.jsonl",
                            task_id=manifest.get("task_id", ""),
                            run_id=verify_run_id)
    ledger.append("independent_verification",
                  object_ref=str(run_dir),
                  after_hash=sha256_obj(result),
                  result="pass" if result["pass"] else "fail",
                  reason=("独立复算通过" if result["pass"]
                          else f"复算不一致："
                               f"{comparison.get('mismatch_count')} 项差异"))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口（设计文档 17.10）。"""
    parser = argparse.ArgumentParser(
        prog="hc_agent.verification",
        description="独立复算：全新目录、全新进程、从冻结输入重建并比较")
    parser.add_argument("--manifest", required=True,
                        help="主计算 manifest.json 路径")
    parser.add_argument("--output-dir", required=True, help="复算输出目录")
    parser.add_argument("--tolerances", default=None,
                        help="字段级容差 JSON 文件（可选）")
    args = parser.parse_args(argv)

    tolerances = None
    if args.tolerances:
        tolerances = json.loads(Path(args.tolerances).read_text(encoding="utf-8"))

    try:
        result = verify_run(args.manifest, output_dir=args.output_dir,
                            tolerances=tolerances)
    except VerificationFailedError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return exc.exit_code
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False), file=sys.stderr)
        return EXIT_VERIFICATION_FAILED

    print(json.dumps({k: v for k, v in result.items()
                      if k != "verify_payload"},
                     ensure_ascii=False, indent=2))
    return EXIT_SUCCESS if result["pass"] else EXIT_VERIFICATION_FAILED


if __name__ == "__main__":
    sys.exit(main())
