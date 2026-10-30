"""acceptance.py — 验收指标矩阵 + 可观测性汇总（开发文档 11.2 / 12.3）。

## 回答什么问题

- "这套系统达到验收门槛了吗？"（11.2 的九条指标）
- "最近运行的健康状况怎么样？"（12.3 的可观测性指标）

## 设计原则

**每条指标都要能追到具体数据**，不做"看起来是绿的"。

| 原则 | 做法 |
|---|---|
| 能算的就算 | 凑合数据来自真实产物（verification.json、metric_diff.csv…） |
| 算不了的就说算不了 | 需要专家标签的（红黄绿一致率）标 `unavailable` 并说明原因 |
| 不给假绿 | 没数据 → `unknown`，不是 `pass` |
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------ 指标定义
#: (key, 名称, 门槛, 说明)
MATRIX: List[Tuple[str, str, str, str]] = [
    ("constraint_coverage", "评估覆盖（约束类数）", "≥5 类",
     "电压/载流/不平衡/短路/反向潮流"),
    ("level_agreement", "红黄绿一致率", "≥90%",
     "需要专家复核标签作独立测试集"),
    ("capacity_deviation", "可开放容量偏差", "≤3%",
     "主算与复算比对（metric_diff.csv）"),
    ("cross_version", "跨版本复现", "完全一致",
     "引擎/规则升级后黄金算例不漂移"),
    ("verification_pass", "独立复算通过率", "100%",
     "verification.json 的 pass 字段"),
    ("data_protection", "原始数据字节保护率", "100%",
     "输入指纹比对（input_match）"),
    ("traceability", "可追溯率", "100%",
     "审计链完整性 + 证据文件齐备"),
    ("engine_count", "适配引擎数", "≥2 种",
     "引擎适配器清单"),
    ("runtime_saving", "单台区耗时降幅", "≥50%",
     "相对人工基准"),
]


def _read_json(p: Path) -> Dict[str, Any]:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _read_csv(p: Path) -> List[Dict[str, str]]:
    import csv
    if not p.is_file():
        return []
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except Exception:
        return []


def _find(run_dir: Path, *names: str) -> Optional[Path]:
    """在运行目录里找文件（results/ 与根目录都试）。"""
    for name in names:
        for base in (run_dir / "results", run_dir):
            f = base / name
            if f.is_file():
                return f
    return None


# ------------------------------------------------------------------ 单次运行
def single_run(run_dir) -> Dict[str, Any]:
    """对一次运行算验收指标。"""
    root = Path(str(run_dir))
    man = _read_json(root / "manifest.json")
    summ = _read_json(root / "results" / "run_summary.json")
    ver = _read_json(root / "verification" / "verification.json")
    gate = _read_json(root / "review" / "release_gate.json")

    items: List[Dict[str, Any]] = []

    # ① 约束覆盖：从 constraint_results.csv 里数不同的 constraint_id
    cpath = _find(root, "constraint_results.csv")
    rows = _read_csv(cpath) if cpath else []
    kinds = sorted({str(r.get("constraint_id") or "") for r in rows
                    if r.get("constraint_id")})
    items.append({
        "key": "constraint_coverage", "value": len(kinds),
        "status": "pass" if len(kinds) >= 5 else ("fail" if kinds else "unknown"),
        "detail": "、".join(kinds) if kinds else "没有约束校核记录",
    })

    # ② 红黄绿一致率：需要专家标签，本工具没有
    items.append({
        "key": "level_agreement", "value": None, "status": "unavailable",
        "detail": "需要专家复核标签作独立测试集；本工具不出专家标签",
    })

    # ③ 容量偏差：metric_diff.csv
    mpath = _find(root, "metric_diff.csv")
    mrows = _read_csv(mpath) if mpath else []
    worst = None
    for r in mrows:
        try:
            tol = float(r.get("tolerance") or 0)
        except Exception:
            tol = 0.0
        try:
            d = abs(float(r.get("abs_diff") or 0))
        except Exception:
            continue
        pct = (d / abs(tol) * 100.0) if tol else None
        if pct is not None and (worst is None or pct > worst):
            worst = pct
    if mrows:
        ok = all(str(r.get("status") or "").lower() in
                 ("pass", "ok", "true") for r in mrows)
        items.append({
            "key": "capacity_deviation",
            "value": (round(worst, 2) if worst is not None else None),
            "status": "pass" if ok else "fail",
            "detail": f"{len(mrows)} 项指标比对，"
                      f"{'全部' if ok else '存在'}在容差内",
        })
    else:
        items.append({"key": "capacity_deviation", "value": None,
                      "status": "unknown", "detail": "没有 metric_diff.csv"})

    # ④ 跨版本复现：需要多次不同版本的运行
    items.append({
        "key": "cross_version", "value": None, "status": "unavailable",
        "detail": "需要同一算例在不同引擎/规则版本下的运行记录",
    })

    # ⑤ 独立复算
    if ver:
        ok = bool(ver.get("pass"))
        items.append({
            "key": "verification_pass", "value": "通过" if ok else "未通过",
            "status": "pass" if ok else "fail",
            "detail": f"recalc={ver.get('verify_run_id') or '—'}",
        })
    else:
        items.append({"key": "verification_pass", "value": None,
                      "status": "unknown", "detail": "没有 verification.json"})

    # ⑥ 原始数据保护：input_match
    if ver:
        im = ver.get("input_match")
        items.append({
            "key": "data_protection",
            "value": ("一致" if im else "不一致") if im is not None else None,
            "status": ("pass" if im else
                       ("fail" if im is False else "unknown")),
            "detail": "主算与复算输入指纹一致",
        })
    else:
        items.append({"key": "data_protection", "value": None,
                      "status": "unknown", "detail": "没有复算记录"})

    # ⑦ 可追溯率：审计链 + 证据文件
    from hc_agent.audit_chain import chain_for_run
    chain = chain_for_run(root).summary()
    evid = ["manifest.json", "results/run_summary.json",
            "results/constraint_results.csv", "verification/verification.json",
            "review/release_gate.json", "quality_issues.csv"]
    present = sum(1 for e in evid if (root / e).is_file())
    trace_ok = bool(chain.get("ok")) and present >= 5
    items.append({
        "key": "traceability",
        "value": f"{present}/{len(evid)} 文件，链{'完好' if chain.get('ok') else '异常'}",
        "status": "pass" if trace_ok else "fail",
        "detail": (f"审计链 {chain.get('count')} 条"
                   + ("" if chain.get("ok") else
                      f"（第 {chain.get('broken_at')} 条断开）")),
    })

    # ⑧ 适配引擎数：以**引擎注册表**为准 —— 能导入、能算才算一个
    try:
        from hc_agent.engines import registry as eng_reg
        eng_sum = eng_reg.summary()
        n_eng = int(eng_sum.get("count") or 0)
        eng_detail = (f"{eng_sum.get('names')}；主算 {eng_sum.get('primary') or '—'}，"
                      f"交叉校核 {eng_sum.get('crosscheck') or '—'}")
    except Exception as exc:      # 注册表本身坏了要暴露，不能悄悄当成 0 个
        n_eng = 0
        eng_detail = f"引擎注册表不可用：{type(exc).__name__}: {exc}"
    items.append({
        "key": "engine_count", "value": n_eng,
        "status": "pass" if n_eng >= 2 else "fail",
        "detail": eng_detail,
    })

    # ⑨ 耗时降幅：需要人工基准
    items.append({
        "key": "runtime_saving", "value": None, "status": "unavailable",
        "detail": "需要人工评估的基准耗时作对照",
    })

    # 汇总
    by_status: Dict[str, int] = {}
    for it in items:
        by_status[it["status"]] = by_status.get(it["status"], 0) + 1

    return {
        "run_id": root.name,
        "items": _decorate(items),
        "summary": by_status,
        "gate": {"passed": gate.get("passed"),
                 "failed": gate.get("failed_gates") or []},
    }


def _decorate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """补上门槛与名称（来自 MATRIX），保证顺序与文档一致。"""
    spec = {k: (name, target, why) for k, name, target, why in MATRIX}
    out = []
    for k, name, target, why in MATRIX:
        got = next((x for x in items if x["key"] == k), None)
        row = dict(got) if got else {"key": k, "value": None,
                                     "status": "unknown", "detail": "未评估"}
        row.update({"name": name, "target": target, "why": why})
        out.append(row)
    return out


STATUS_LABEL = {
    "pass": "达标",
    "fail": "未达标",
    "unknown": "无数据",
    "unavailable": "不适用",
}

STATUS_KIND = {
    "pass": "ok",
    "fail": "err",
    "unknown": "gray",
    "unavailable": "gray",
}


# ------------------------------------------------------------------ 多运行
def overview(runs: List[Path]) -> Dict[str, Any]:
    """可观测性：对一批运行做汇总（开发文档 12.3）。"""
    total = len(runs)
    ok = 0
    fails: Dict[str, int] = {}
    level_dist: Dict[str, int] = {}
    rule_dist: Dict[str, int] = {}
    no_conv = 0
    unknown_cnt = 0
    chain_bad = 0
    elapsed: List[float] = []

    from hc_agent.audit_chain import chain_for_run

    for d in runs:
        try:
            out = _read_json(Path(d) / "run_outcome.json")
            summ = _read_json(Path(d) / "results" / "run_summary.json")
            st = str(out.get("status") or "")
            if st in ("ok", "awaiting_approval", "completed"):
                ok += 1
            else:
                key = st or "unknown"
                fails[key] = fails.get(key, 0) + 1
            lv = str(summ.get("level") or out.get("level") or "")
            if lv:
                level_dist[lv] = level_dist.get(lv, 0) + 1
            rp = str(summ.get("rule_package") or "")
            if rp:
                rule_dist[rp] = rule_dist.get(rp, 0) + 1
            if summ.get("all_converged") is False:
                no_conv += 1
            bs = summ.get("baseline_constraint_summary") or {}
            unknown_cnt += int(bs.get("unknown") or 0)
            c = chain_for_run(d).summary()
            if c.get("exists") is False or not c.get("ok"):
                chain_bad += 1
            if summ.get("elapsed_s") is not None:
                try:
                    elapsed.append(float(summ["elapsed_s"]))
                except Exception:
                    pass
        except Exception:
            continue

    return {
        "total": total,
        "success": ok,
        "success_rate": round(ok / total * 100.0, 1) if total else 0.0,
        "failures": fails,
        "convergence_fail": no_conv,
        "unknown_constraints": unknown_cnt,
        "chain_broken": chain_bad,
        "level_dist": level_dist,
        "rule_dist": rule_dist,
        "avg_elapsed_s": (round(sum(elapsed) / len(elapsed), 3)
                          if elapsed else None),
    }


def format_matrix(res: Dict[str, Any]) -> str:
    """排成可读文本（问答直接用）。"""
    lines = [f"**{res.get('run_id')}** 的验收指标", ""]
    mark = {"pass": "✅", "fail": "❌", "unknown": "—", "unavailable": "○"}
    for it in res.get("items") or []:
        lines.append(
            f"{mark.get(it['status'], '?')} **{it['name']}**（门槛 {it['target']}）"
            f"　{STATUS_LABEL.get(it['status'], it['status'])}"
            + (f"：{it['value']}" if it.get("value") is not None else ""))
        if it.get("detail"):
            lines.append(f"　　{it['detail']}")
    s = res.get("summary") or {}
    lines.append("")
    lines.append("汇总：" + "、".join(
        f"{STATUS_LABEL.get(k, k)} {v}" for k, v in
        sorted(s.items(), key=lambda x: -x[1])))
    gate = res.get("gate") or {}
    if gate.get("failed"):
        lines.append(f"发布门禁：未通过（{'、'.join(str(x) for x in gate['failed'])}）")
    return "\n".join(lines)
