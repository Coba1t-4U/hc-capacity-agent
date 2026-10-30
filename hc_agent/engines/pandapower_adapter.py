"""pandapower_adapter.py — 第二计算引擎（正序平衡），用于交叉校核。

**它不参与容量判定。** 本工程的容量搜索、约束判据、红黄绿分级全部由
OpenDSS 三相不平衡潮流给出（设计文档 17.10）。pandapower 在这里的作用是
**用另一套独立实现去核对 OpenDSS 的量级对不对**：

    同一份输入包 ─┬─ OpenDSS（三相不平衡，主算）
                  └─ pandapower（正序平衡，交叉校核）

两者的模型口径不同，**不是逐节点等价**：
  * OpenDSS 保留相别与接线，逐相求解；pandapower 默认是正序单相等值；
  * 因此电压只能比"相平均后的极值"，网损只能比量级（默认容差 10%）。

结论分三态：``match`` / ``differ`` / ``unavailable``。**differ 不隐藏**，
如实写进 ``engine_crosscheck.json``，也如实进入验收矩阵的说明。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_ID = "pandapower"
ENGINE_LABEL = "pandapower"
SCOPE = "balanced_positive_sequence"
SCOPE_LABEL = "正序平衡（交叉校核）"

#: 交叉校核容差：口径不同，不能按 1e-6 比
TOL_V_PU = 0.03          # 正序平衡解 vs 最差相，允许差 0.03 pu
TOL_LOSS_PCT = 10.0      # 网损允许相差 10%


# ---------------------------------------------------------------- 可用性
def available() -> Dict[str, Any]:
    """探测 pandapower 是否可用（真实导入 + 建空网，不做假）。"""
    from hc_agent import runtime_env

    lib = runtime_env.ensure_pandapower_on_path()
    info: Dict[str, Any] = {
        "engine_id": ENGINE_ID, "engine_label": ENGINE_LABEL,
        "scope": SCOPE, "scope_label": SCOPE_LABEL,
        "pylib_path": str(lib) if lib else None,
        "available": False,
    }
    if lib is None:
        info["error"] = ("未找到 pandapower 库目录；请设置 HC_PANDAPOWER_PYLIB "
                         "或将 pandapower 安装到 pylibs_pandapower")
        return info
    try:
        import pandapower as pp
        pp.create_empty_network()
        info["available"] = True
        info["version"] = getattr(pp, "__version__", "unknown")
        try:
            import pandas as pd
            info["pandas_version"] = pd.__version__
        except Exception:
            info["pandas_version"] = None
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


# ---------------------------------------------------------------- 读输入包
def _f(v, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _b(v, default: bool = True) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是", "合")


def _sheet_rows(ws) -> List[Dict[str, Any]]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(c).strip() if c is not None else "" for c in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(c is None for c in r):
            continue
        out.append({head[i]: r[i] for i in range(min(len(head), len(r)))})
    return out


def read_bundle(stage: Path) -> Dict[str, List[Dict[str, Any]]]:
    """从冻结输入包读出各表（只用标准库 + openpyxl）。"""
    import openpyxl

    wb = openpyxl.load_workbook(stage / "network.xlsx", read_only=True,
                                data_only=True)
    try:
        return {name: _sheet_rows(wb[name]) for name in wb.sheetnames}
    finally:
        wb.close()


def read_profiles(stage: Path) -> List[Dict[str, Any]]:
    f = stage / "profiles.csv"
    if not f.is_file():
        return []
    with open(f, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def pick_timestamps(stage: Path) -> Dict[str, str]:
    """挑两个有代表性的时刻：光伏最大、负荷最大。

    不写死 12:00/19:00 —— 不同算例的峰谷时刻不同，按数据挑才不会挑错。
    """
    rows = read_profiles(stage)
    pv: Dict[str, float] = {}
    load: Dict[str, float] = {}
    for r in rows:
        ts = str(r.get("timestamp") or "")
        if not ts:
            continue
        kind = str(r.get("resource_type") or "")
        if kind == "pv":
            pv[ts] = pv.get(ts, 0.0) + _f(r.get("available_p_pu"))
        elif kind == "load":
            load[ts] = load.get(ts, 0.0) + _f(r.get("p_kw"))
    out = {}
    if pv:
        out["pv_peak"] = max(pv.items(), key=lambda kv: kv[1])[0]
    if load:
        out["load_peak"] = max(load.items(), key=lambda kv: kv[1])[0]
    return out


def _at(rows: List[Dict[str, Any]], ts: str,
        kind: str) -> Dict[str, Tuple[float, float]]:
    """取某时刻某类资源的 (p_kw, q_kvar)。"""
    out: Dict[str, Tuple[float, float]] = {}
    for r in rows:
        if str(r.get("timestamp") or "") != ts:
            continue
        if str(r.get("resource_type") or "") != kind:
            continue
        # 输入包（loads/pv 表）引用的是 **profile_id**（LP001/PP001），
        # 不是 profiles 里的 resource_id（L001/PV001）。用错键会静默取到 0，
        # 表现为"负荷没加进去、网损只剩铁损"（实测踩过）。
        rid = str(r.get("profile_id") or r.get("resource_id") or "")
        if not rid:
            continue
        p, q = out.get(rid, (0.0, 0.0))
        out[rid] = (p + _f(r.get("p_kw")), q + _f(r.get("q_kvar")))
    return out


# ---------------------------------------------------------------- 建网
def build_net(stage: Path, timestamp: str):
    """把输入包建成 pandapower 正序平衡网络。返回 (net, meta)。"""
    from hc_agent import runtime_env

    lib = runtime_env.ensure_pandapower_on_path()
    if lib is None:
        raise RuntimeError("pandapower 不可用（未找到库目录）")
    import pandapower as pp

    sheets = read_bundle(stage)
    prof = read_profiles(stage)

    net = pp.create_empty_network(name="hc_balanced")
    idx: Dict[str, int] = {}
    skipped: List[str] = []

    for b in sheets.get("buses", []):
        nid = str(b.get("node_id") or "")
        if not nid:
            continue
        idx[nid] = pp.create_bus(net, vn_kv=_f(b.get("vn_kv"), 0.4), name=nid)

    for ln in sheets.get("lines", []):
        a, z = str(ln.get("from_node") or ""), str(ln.get("to_node") or "")
        if a not in idx or z not in idx or not _b(ln.get("in_service")):
            skipped.append(f"line:{ln.get('line_id')}")
            continue
        max_i = _f(ln.get("max_i_a"), 0.0) / 1000.0
        pp.create_line_from_parameters(
            net, idx[a], idx[z], length_km=_f(ln.get("length_km"), 0.001),
            r_ohm_per_km=_f(ln.get("r1_ohm_per_km")),
            x_ohm_per_km=_f(ln.get("x1_ohm_per_km")),
            c_nf_per_km=_f(ln.get("c1_nf_per_km")),
            max_i_ka=max_i if max_i > 0 else 1.0,
            name=str(ln.get("line_id") or ""))

    for t in sheets.get("transformers", []):
        hv, lv = str(t.get("hv_node") or ""), str(t.get("lv_node") or "")
        if hv not in idx or lv not in idx or not _b(t.get("in_service")):
            skipped.append(f"trafo:{t.get('transformer_id')}")
            continue
        pp.create_transformer_from_parameters(
            net, idx[hv], idx[lv],
            sn_mva=max(_f(t.get("sn_kva"), 1.0) / 1000.0, 1e-3),
            vn_hv_kv=_f(t.get("vn_hv_kv"), 10.0),
            vn_lv_kv=_f(t.get("vn_lv_kv"), 0.4),
            vk_percent=_f(t.get("vk_percent"), 4.0),
            vkr_percent=_f(t.get("vkr_percent"), 1.0),
            pfe_kw=_f(t.get("pfe_kw")), i0_percent=_f(t.get("i0_percent")),
            name=str(t.get("transformer_id") or ""))

    lp = _at(prof, timestamp, "load")
    for ld in sheets.get("loads", []):
        nid = str(ld.get("node_id") or "")
        if nid not in idx or not _b(ld.get("in_service")):
            continue
        pid = str(ld.get("profile_id") or "")
        p_kw, q_kvar = lp.get(pid, (0.0, 0.0))
        pp.create_load(net, idx[nid], p_mw=max(p_kw, 0.0) / 1000.0,
                       q_mvar=max(q_kvar, 0.0) / 1000.0,
                       name=str(ld.get("load_id") or ""))

    pp_pv = _at(prof, timestamp, "pv")
    for g in sheets.get("pv", []):
        nid = str(g.get("node_id") or "")
        if nid not in idx or not _b(g.get("in_service")):
            continue
        rated = _f(g.get("rated_p_kw"))
        # available_p_pu 是**可用率**，乘装机才是实际出力
        avail = 0.0
        for r in prof:
            if (str(r.get("timestamp") or "") == timestamp
                    and str(r.get("profile_id") or "") == str(g.get("profile_id") or "")):
                avail = _f(r.get("available_p_pu"))
                break
        p_kw = rated * avail if (rated and avail) else pp_pv.get(
            str(g.get("profile_id") or ""), (0.0, 0.0))[0]
        if p_kw <= 0:
            continue
        pp.create_sgen(net, idx[nid], p_mw=p_kw / 1000.0, q_mvar=0.0,
                       name=str(g.get("pv_id") or ""))

    for s in sheets.get("sources", []):
        nid = str(s.get("node_id") or "")
        if nid not in idx:
            continue
        pp.create_ext_grid(net, idx[nid], vm_pu=_f(s.get("vm_pu"), 1.0),
                           name=str(s.get("source_id") or ""))

    meta = {"timestamp": timestamp, "buses": len(net.bus),
            "lines": len(net.line), "trafo": len(net.trafo),
            "loads": len(net.load), "sgen": len(net.sgen),
            "ext_grid": len(net.ext_grid), "skipped": skipped[:10]}
    return net, meta


def solve(net) -> Dict[str, Any]:
    """正序平衡潮流，返回与 OpenDSS 可比的量。"""
    import pandapower as pp

    pp.runpp(net, algorithm="nr", calculate_voltage_angles=True, init="auto",
             numba=False)
    if not bool(net.converged):
        return {"converged": False, "error": "pandapower 潮流不收敛"}

    eg_buses = set(net.ext_grid.bus.tolist())
    vb = net.res_bus
    mask = [i for i in vb.index if i not in eg_buses]
    vmin = float(vb.vm_pu[mask].min()) if mask else None
    vmax = float(vb.vm_pu[mask].max()) if mask else None
    vmin_bus = (net.bus.name[int(vb.vm_pu[mask].idxmin())]
                if mask else None)
    vmax_bus = (net.bus.name[int(vb.vm_pu[mask].idxmax())]
                if mask else None)

    loads = [float(x) for x in net.res_line.loading_percent.dropna()]
    tl = [float(x) for x in net.res_trafo.loading_percent.dropna()]
    all_load = loads + tl
    max_loading = max(all_load) if all_load else None

    # res_trafo.pl_mw 已含铁损（pfe）与铜损，不能再单独加 pfe_kw，
    # pandapower 3.x 的 res_trafo 也没有 pfe_kw 这一列（实测踩过）。
    losses = (float(net.res_line.pl_mw.sum())
              + float(net.res_trafo.pl_mw.sum())) * 1000.0
    return {
        "converged": True,
        "vmin_pu": vmin, "vmin_bus": vmin_bus,
        "vmax_pu": vmax, "vmax_bus": vmax_bus,
        "max_loading_pct": max_loading,
        "losses_kw": losses,
        "load_kw": float(net.load.p_mw.sum()) * 1000.0,
        "pv_kw": float(net.sgen.p_mw.sum()) * 1000.0 if len(net.sgen) else 0.0,
        "source_kw": float(net.res_ext_grid.p_mw.sum()) * 1000.0,
        "iterations": int(net._ppc.get("iterations", 0))
        if hasattr(net, "_ppc") else None,
    }


# ---------------------------------------------------------------- OpenDSS 侧
def _opendss_side(run_dir: Path, timestamp: str,
                  scenario: str = "S_BASE") -> Dict[str, Any]:
    """从运行产物里取同一场景、同一时刻的可比量。

    两个坑（实测踩过，别重复）：
      1. **必须按 scenario_id 过滤** —— 三个场景共用同一批时间戳，
         只按 timestamp 过滤会把 S_BASE / S_HIGH_PV / S_LOW_PV 的行混在一起；
      2. **必须跳过 T0/ALL 聚合行** —— 逐相行之外还有一行合计，
         相加等于把功率算两遍（19:00 会算出 2800 kW 这种离谱网损）。

    电压用**最差相**而不是相平均：pandapower 是正序平衡解，
    它落在 OpenDSS 各相极值之间，跟"最差相"才是同一种语义。
    """
    res = run_dir / "results"
    bus_f, el_f = res / "bus_results.csv", res / "element_results.csv"
    out: Dict[str, Any] = {"available": bus_f.is_file() and el_f.is_file(),
                           "scenario": scenario}
    if not out["available"]:
        return out

    vmin, vmax = None, None
    vmin_bus = vmax_bus = None
    per_bus: Dict[str, List[float]] = {}
    with open(bus_f, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("timestamp") or "") != timestamp:
                continue
            if str(r.get("scenario_id") or "") != scenario:
                continue
            bus = str(r.get("bus") or "")
            if not bus or bus.lower() == "sourcebus":
                continue
            v = _f(r.get("v_pu"), -1.0)
            if v <= 0:
                continue
            per_bus.setdefault(bus, []).append(v)
            if vmin is None or v < vmin:
                vmin, vmin_bus = v, bus
            if vmax is None or v > vmax:
                vmax, vmax_bus = v, bus
    if per_bus:
        out.update({"vmin_pu": vmin, "vmax_pu": vmax,
                    "vmin_bus": vmin_bus, "vmax_bus": vmax_bus,
                    "buses": len(per_bus),
                    "v_pu_basis": "worst_phase"})

    losses = 0.0
    pv_kw = 0.0
    with open(el_f, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("timestamp") or "") != timestamp:
                continue
            if str(r.get("scenario_id") or "") != scenario:
                continue
            if str(r.get("phase") or "").upper() not in ("A", "B", "C"):
                continue          # 跳过 ALL 聚合行
            if str(r.get("terminal") or "") not in ("1", "2"):
                continue          # 跳过 T0 合计行
            cls = str(r.get("element_class") or "")
            p_kw = _f(r.get("p_kw"))
            if cls in ("line", "transformer"):
                losses += p_kw    # 两端相加即损耗
            elif cls == "pvsystem":
                pv_kw += -p_kw    # 发电记为负用电
    out["losses_kw"] = losses
    out["pv_kw"] = max(pv_kw, 0.0)
    return out


def _verdict(op: Optional[float], pw: Optional[float], *,
             tol_abs: float = 0.0, tol_pct: float = 0.0) -> str:
    if op is None or pw is None:
        return "unavailable"
    diff = abs(op - pw)
    if tol_abs and diff <= tol_abs:
        return "match"
    if tol_pct:
        base = max(abs(op), 1e-9)
        if diff / base * 100.0 <= tol_pct:
            return "match"
    return "differ"


def crosscheck(run_dir: Path, *, timestamps: Optional[Dict[str, str]] = None
               ) -> Dict[str, Any]:
    """对一次运行做 pandapower 交叉校核，返回结构化结论（不写盘）。"""
    run_dir = Path(run_dir)
    stage = run_dir / "_stage"
    ts_map = timestamps or pick_timestamps(stage)
    report: Dict[str, Any] = {
        "engine_id": ENGINE_ID, "engine_label": ENGINE_LABEL,
        "scope": SCOPE, "scope_label": SCOPE_LABEL,
        "note": "模型口径不同（OpenDSS 三相不平衡 vs pandapower 正序平衡），"
                "只做量级与极值校核，不逐节点等价，也**不参与容量判定**。",
        "tolerances": {"v_pu_abs": TOL_V_PU, "loss_pct": TOL_LOSS_PCT},
        "points": [],
    }
    info = available()
    report["engine_version"] = info.get("version")
    if not info.get("available"):
        report["verdict"] = "unavailable"
        report["error"] = info.get("error")
        return report
    if not stage.is_dir():
        report["verdict"] = "unavailable"
        report["error"] = f"缺少冻结输入目录：{stage}"
        return report

    verdicts: List[str] = []
    for label, ts in ts_map.items():
        try:
            net, meta = build_net(stage, ts)
            pw = solve(net)
        except Exception as exc:
            report["points"].append({"point": label, "timestamp": ts,
                                     "verdict": "unavailable",
                                     "error": f"{type(exc).__name__}: {exc}"})
            verdicts.append("unavailable")
            continue
        op = _opendss_side(run_dir, ts)
        entry: Dict[str, Any] = {
            "point": label, "timestamp": ts, "model": meta,
            "pandapower": {k: pw.get(k) for k in
                           ("converged", "vmin_pu", "vmax_pu",
                            "max_loading_pct", "losses_kw", "load_kw",
                            "pv_kw", "source_kw")},
            "opendss": {k: op.get(k) for k in
                        ("available", "vmin_pu", "vmax_pu", "losses_kw",
                         "pv_kw", "buses")},
        }
        v_vmin = _verdict(op.get("vmin_pu"), pw.get("vmin_pu"),
                          tol_abs=TOL_V_PU)
        v_vmax = _verdict(op.get("vmax_pu"), pw.get("vmax_pu"),
                          tol_abs=TOL_V_PU)
        v_loss = _verdict(op.get("losses_kw"), pw.get("losses_kw"),
                          tol_pct=TOL_LOSS_PCT)
        entry["checks"] = {"vmin": v_vmin, "vmax": v_vmax, "losses": v_loss}
        for k, a, b in (("vmin_pu", op.get("vmin_pu"), pw.get("vmin_pu")),
                        ("vmax_pu", op.get("vmax_pu"), pw.get("vmax_pu")),
                        ("losses_kw", op.get("losses_kw"), pw.get("losses_kw"))):
            if a is not None and b is not None:
                entry.setdefault("diff", {})[k] = {
                    "opendss": round(a, 6), "pandapower": round(b, 6),
                    "abs": round(abs(a - b), 6)}
        pv = [v for v in (v_vmin, v_vmax, v_loss) if v != "unavailable"]
        if not pv:
            entry["verdict"] = "unavailable"
        elif all(v == "match" for v in pv):
            entry["verdict"] = "match"
        else:
            entry["verdict"] = "differ"
        verdicts.append(entry["verdict"])
        report["points"].append(entry)

    got = [v for v in verdicts if v != "unavailable"]
    if not got:
        report["verdict"] = "unavailable"
    elif all(v == "match" for v in got):
        report["verdict"] = "match"
    else:
        report["verdict"] = "differ"
    report["summary"] = {"points": len(report["points"]),
                         "match": sum(1 for v in got if v == "match"),
                         "differ": sum(1 for v in got if v == "differ")}
    return report


def write_crosscheck(run_dir: Path, **kw) -> Dict[str, Any]:
    """跑一次交叉校核并写入 results/engine_crosscheck.json。"""
    rep = crosscheck(Path(run_dir), **kw)
    out = Path(run_dir) / "results" / "engine_crosscheck.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    rep["path"] = str(out)
    return rep


def format_crosscheck(rep: Dict[str, Any]) -> str:
    """把结论排成便于阅读的中文文本（问答与报告共用）。"""
    lines = [f"引擎交叉校核：{rep.get('engine_label')} "
             f"{rep.get('engine_version') or ''}".rstrip(),
             f"口径：{rep.get('scope_label')} —— {rep.get('note')}"]
    vmap = {"match": "一致", "differ": "有差异", "unavailable": "不可用"}
    lines.append(f"总判定：{vmap.get(str(rep.get('verdict')), rep.get('verdict'))}")
    for p in rep.get("points") or []:
        lines.append("")
        lines.append(f"【{p.get('point')}】{p.get('timestamp')}"
                     f"  判定 {vmap.get(str(p.get('verdict')), p.get('verdict'))}")
        if p.get("error"):
            lines.append(f"  未完成：{p['error']}")
            continue
        for k, label in (("vmin_pu", "最低电压(pu)"), ("vmax_pu", "最高电压(pu)"),
                         ("losses_kw", "网损(kW)")):
            d = (p.get("diff") or {}).get(k)
            if d:
                lines.append(f"  {label}：OpenDSS {d['opendss']:.6f} ｜ "
                             f"pandapower {d['pandapower']:.6f} ｜ "
                             f"差 {d['abs']:.6f}"
                             f"（{p['checks'][{'vmin_pu': 'vmin', 'vmax_pu': 'vmax', 'losses_kw': 'losses'}[k]]}）")
    return "\n".join(lines)
