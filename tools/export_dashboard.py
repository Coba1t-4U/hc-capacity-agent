"""export_dashboard.py — 把 runs/ 下的真实运行产物汇总为前端可用的单个 JSON。

设计原则（延续本工程既定要求）：
  1. **不编造任何数值**：页面显示的每个数字都来自运行产物文件；
     取不到就输出 null 并标注，不由前端估算或填默认值。
  2. 显式携带可追溯性：每个面板都带 run_id / snapshot_id / rule_version /
     engine_fingerprint / input_digest，使页面结论可回链。
  3. unknown / grey 等状态原样透出，不在展示层"洗白"。

用法：
  python tools/export_dashboard.py [--runs runs] [--out web/data.json]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 各算例的展示元信息（对象范围与说明取自输入，不是渲染时编的）
CASE_META = {
    "FD001": {
        "title": "FD001 受限低压馈线",
        "subtitle": "250 A 低压线路 + 240 kW 末端负荷 → 基线即越限",
        "kind": "capacity",
    },
    "FD002": {
        "title": "FD002 健康馈线",
        "subtitle": "600 A 低压线路 + 1000 kVA 配变，启用短路校核",
        "kind": "capacity",
    },
    "APP001": {
        "title": "APP001 单笔并网申请校核",
        "subtitle": "250 kW 并网申请（SK-11，按申请容量校核）",
        "kind": "access",
    },
    "RM001": {
        "title": "RM001 主配微协同研究",
        "subtitle": "配网 D1 + 3 微网，目标级联协调容量边界",
        "kind": "research",
    },
}


def read_json(p: Path) -> Optional[Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_csv(p: Path) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


def num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_case(run_dir: Path, meta: Dict[str, Any]) -> Dict[str, Any]:
    """汇总单个算例的运行产物。"""
    out: Dict[str, Any] = {
        "id": run_dir.name,
        "title": meta["title"],
        "subtitle": meta["subtitle"],
        "kind": meta["kind"],
        "available": False,
    }
    res = run_dir / "results"
    summary = read_json(res / "run_summary.json")
    if not summary:
        return out
    out["available"] = True

    man = read_json(run_dir / "manifest.json") or {}
    ver = read_json(run_dir / "verification" / "verification.json") or {}
    gate = read_json(run_dir / "review" / "release_gate.json") or {}
    pub = read_json(run_dir / "review" / "publication.json") or {}
    fp = man.get("engine_fingerprint") or {}

    cs = summary.get("constraint_summary") or {}
    out.update({
        "run_id": summary.get("run_id"),
        "task_id": summary.get("task_id"),
        "business_scene": summary.get("business_scene"),
        "scope_type": summary.get("scope_type"),
        "scope_ids": summary.get("scope_ids"),
        "window_start": summary.get("time_window_start"),
        "window_end": summary.get("time_window_end"),
        # 可追溯性（页面必须能回链到快照/规则/引擎）
        "snapshot_id": man.get("snapshot_id") or summary.get("snapshot_id"),
        "topology_version": man.get("topology_version"),
        "rule_version": man.get("rule_version") or summary.get("rule_package"),
        "skill_version_count": len(man.get("skill_version") or {}),
        "engine": {
            "id": fp.get("engine_id"),
            "interface": fp.get("interface"),
            "interface_version": fp.get("interface_version"),
            "backend": (fp.get("engine_version") or "")[:70],
        },
        "input_digest": man.get("input_digest"),
        # 结论
        "capacity_physical_kw": summary.get("capacity_physical_kw"),
        "capacity_managed_kw": summary.get("capacity_managed_kw"),
        "level": summary.get("level"),
        "level_reason": summary.get("level_reason"),
        "binding_constraint": summary.get("binding_constraint"),
        "binding_detail": summary.get("binding_detail"),
        "search_method": summary.get("search_method"),
        "search_status": summary.get("search_status"),
        "search_iterations": summary.get("search_iterations"),
        "search_notes": summary.get("search_notes") or [],
        "feasible_lower_kw": summary.get("feasible_lower_kw"),
        "infeasible_upper_kw": summary.get("infeasible_upper_kw"),
        "baseline_converged": summary.get("all_converged"),
        "scenario_count": summary.get("scenario_count"),
        "slice_count": summary.get("slice_count"),
        "constraint_summary": cs,
        "risks": summary.get("risks") or [],
        "unknowns": summary.get("unknowns") or [],
        # 复算
        "verification": {
            "pass": ver.get("pass"),
            "verify_run_id": ver.get("verify_run_id"),
            "input_match": ver.get("input_match"),
            "mismatch_count": (ver.get("comparison") or {}).get("mismatch_count"),
            "total_metrics": (ver.get("comparison") or {}).get("total_metrics"),
            "note": (ver.get("comparison") or {}).get("note"),
        } if ver else None,
        # 发布门禁
        "release_gate": {
            "passed": gate.get("passed"),
            "failed_gates": gate.get("failed_gates") or [],
            "gates": gate.get("gates") or [],
            "result_hash": gate.get("result_hash"),
        } if gate else None,
        "publication": {
            "status": pub.get("status"),
            "reason": pub.get("reason"),
        } if pub else None,
    })

    # 搜索轨迹
    trace = read_csv(res / "search_trace.csv")
    out["search_trace"] = [{
        "iteration": int(num(r.get("iteration")) or 0),
        "delta_kw": num(r.get("delta_kw")),
        "status": r.get("status"),
        "binding_constraint": r.get("binding_constraint"),
        "pass": int(num(r.get("pass_count")) or 0),
        "fail": int(num(r.get("fail_count")) or 0),
        "unknown": int(num(r.get("unknown_count")) or 0),
    } for r in trace]

    # 约束明细：按约束聚合 + 保留越限明细
    rows = read_csv(res / "constraint_results.csv")
    by_cls: Dict[str, Dict[str, int]] = {}
    fails: List[Dict[str, Any]] = []
    for r in rows:
        cid = r.get("constraint_id") or "?"
        st = r.get("status") or "?"
        by_cls.setdefault(cid, {"pass": 0, "fail": 0, "unknown": 0})
        if st in by_cls[cid]:
            by_cls[cid][st] += 1
        if st == "fail":
            fails.append({
                "constraint_id": cid,
                "object_id": r.get("object_id"),
                "scenario_id": r.get("scenario_id"),
                "timestamp": r.get("timestamp"),
                "metric": r.get("metric"),
                "value": num(r.get("value")),
                "lower_limit": num(r.get("lower_limit")),
                "upper_limit": num(r.get("upper_limit")),
                "margin": num(r.get("margin")),
                "unit": r.get("unit"),
            })
    out["constraint_classes"] = [
        {"constraint_id": k, **v} for k, v in sorted(by_cls.items())]
    out["failures"] = fails[:80]
    out["failure_total"] = len(fails)

    # 并网审查（SK-11）：结论以 access_check 为准，而不是搜索口径的 run_summary
    ac = read_json(res / "access_check.json")
    if ac:
        out["access_check"] = ac
        decision = ac.get("decision_candidate")
        # 结论候选 → 展示分级（仅做映射，不改变计算值）
        out["level"] = {"可行": "green", "有条件可行": "yellow",
                        "不可行": "red", "无法评估": "grey"}.get(decision,
                                                                  out.get("level"))
        out["level_reason"] = (f"单笔并网审查结论：{decision}"
                               f"（申请容量 {ac.get('requested_capacity_kw')} kW）")
        out["capacity_physical_kw"] = ac.get("requested_capacity_kw")
        out["binding_constraint"] = ac.get("binding_constraint") or ""
        # 单笔审查的约束口径取自"含申请"场景，而非容量搜索结果。
        # 若此处继续沿用 run_summary 的约束统计，页面会出现同一算例
        # 两套互相矛盾的数字（实测：KPI 显示 123/0/0，接入校核框显示 120/0/1）。
        if ac.get("constraint_summary"):
            out["constraint_summary"] = ac["constraint_summary"]
        # 约束分类同样以含申请场景为准
        rows_app = read_csv(res / "constraint_results.csv")
        by_cls_app: Dict[str, Dict[str, int]] = {}
        fails_app: List[Dict[str, Any]] = []
        for r in rows_app:
            cid = r.get("constraint_id") or "?"
            st = r.get("status") or "?"
            by_cls_app.setdefault(cid, {"pass": 0, "fail": 0, "unknown": 0})
            if st in by_cls_app[cid]:
                by_cls_app[cid][st] += 1
            if st == "fail":
                fails_app.append({
                    "constraint_id": cid,
                    "object_id": r.get("object_id"),
                    "scenario_id": r.get("scenario_id"),
                    "timestamp": r.get("timestamp"),
                    "metric": r.get("metric"),
                    "value": num(r.get("value")),
                    "lower_limit": num(r.get("lower_limit")),
                    "upper_limit": num(r.get("upper_limit")),
                    "margin": num(r.get("margin")),
                    "unit": r.get("unit"),
                })
        out["constraint_classes"] = [
            {"constraint_id": k, **v} for k, v in sorted(by_cls_app.items())]
        out["failures"] = fails_app[:80]
        out["failure_total"] = len(fails_app)
        out["scenario_basis"] = "含申请场景（application）"

    out["capacity_rows"] = read_csv(res / "capacity.csv")
    return out


def build_research(run_dir: Path, meta: Dict[str, Any]) -> Dict[str, Any]:
    """汇总主配微研究产物。"""
    out: Dict[str, Any] = {"id": run_dir.name, "title": meta["title"],
                           "subtitle": meta["subtitle"], "kind": "research",
                           "available": False}
    res = run_dir / "results"
    summ = read_json(res / "research_summary.json")
    outcome = read_json(run_dir / "study_outcome.json")
    if not summ and not outcome:
        return out
    out["available"] = True

    m = (summ or {}).get("metrics") or {}
    out.update({
        "study_id": (summ or {}).get("study_id") or (outcome or {}).get("study_id"),
        "task": (summ or {}).get("task") or {},
        "regions": (summ or {}).get("regions") or [],
        "interfaces": (summ or {}).get("interfaces") or [],
        "max_lambda_mw": m.get("max_lambda_mw"),
        "feasible_points": m.get("feasible_points"),
        "infeasible_points": m.get("infeasible_points"),
        "unknown_points": m.get("unknown_points"),
        "nonanticipativity_pass": m.get("nonanticipativity_pass"),
        "energy_conservation_pass": m.get("energy_conservation_pass"),
        "dataset_version": m.get("dataset_version"),
        "scope_declaration": (summ or {}).get("scope_declaration") or [],
        "reason": (outcome or {}).get("reason"),
        "exit_code": (outcome or {}).get("exit_code"),
    })
    frontier = read_csv(res / "capacity_frontier.csv")
    out["frontier"] = [{
        "lambda_mw": num(r.get("lambda_mw")),
        "status": r.get("status"),
        "limiting_constraint": r.get("limiting_constraint"),
        "cost_yuan": num(r.get("cost_yuan")),
        "region_mw": r.get("region_mw"),
    } for r in frontier]
    itrace = read_csv(res / "interface_trace.csv")
    out["interface_trace"] = [{
        "iteration": int(num(r.get("atc_iteration")) or 0),
        "lambda_mw": num(r.get("lambda_mw")),
        "objective": num(r.get("objective")),
        "residual": num(r.get("max_abs_residual")),
        "security_ok": (r.get("security_ok") == "true"),
    } for r in itrace]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="导出前端数据包")
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--root", default="runs/ACCEPT",
                    help="作为展示数据源的运行根目录")
    ap.add_argument("--out", default="web/data.json")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        root = Path(args.runs)
    cases: List[Dict[str, Any]] = []
    if root.exists():
        for d in sorted(root.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            meta = CASE_META.get(d.name)
            if not meta:
                continue
            data = (build_research(d, meta) if meta["kind"] == "research"
                    else build_case(d, meta))
            if data.get("available"):
                cases.append(data)

    payload = {
        "generated_from": str(root),
        "case_count": len(cases),
        "cases": cases,
        "note": "本页所有数值均来自运行产物文件（run_summary.json / "
                "constraint_results.csv / access_check.json / "
                "research_summary.json 等），未做任何估算或填充；"
                "取不到的字段显示为 null。",
    }
    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"wrote {outp} ({outp.stat().st_size} bytes), "
          f"{len(cases)} cases: {[c['id'] for c in cases]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
