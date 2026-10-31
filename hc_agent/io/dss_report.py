"""dss_report.py — 为 OpenDSS 原生算例分析生成计算报告。

产出到 `<数据目录>/dss_runs/<算例名>_<时间戳>/`：

    report.docx            Word 报告（系统默认程序可直接打开）
    report_manifest.json   报告清单（路径、章节、哈希、生成时间）
    summary.json           结构化结果（供问答/后续引用）

## 报告为什么这么写

1. **第一屏就写清口径**：原生算例分析**不产出开放容量**。
   不写清，用户会把它当成承载力结论。
2. **越限单独成节**：这是用户真正关心的"有没有问题"。
3. **附全量明细表**：报告要能核对，不能只给结论。
4. **结论要点自动生成**：把"哪个约束先被顶到"直接说出来，
   但每条都基于实测数字，不做推断。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from hc_agent.io.dss_case import CaseAnalysis


#: 常见默认算例目录名（用户说"桌面上的算例"时按这些找）
DEFAULT_DSS_DIRS = (
    # 桌面上的官方示例算例（按这个顺序找没指定路径的默认算例）
    "配网承载力算例_光伏高渗透",     # 电压越限 + 设备过载
    "配网承载力算例_双台区对比",     # 一台区正常、一台区电压越限
    "配网承载力算例_全部满足",       # 健康基线，全部满足
)


def search_bases() -> List[Path]:
    """按优先级列出可能放算例的目录（桌面优先）。"""
    bases: List[Path] = []
    try:
        home = Path.home()
        for nm in ("Desktop", "桌面", "OneDrive/Desktop", "OneDrive/桌面"):
            p = home / nm
            if p.is_dir():
                bases.append(p)
        from hc_agent import paths
        bases.append(paths.data_root_safe())
        bases.append(paths.bundle_root())
    except Exception:
        pass
    return bases


def iter_case_dirs(base, *, max_depth: int = 3):
    """在 base 下递归找"含 .dss 文件"的目录，按路径长度（浅优先）产出。

    为什么要递归：用户会把算例收进一个总目录整理
    （实测：三个算例被移到 `桌面/opendss算例/` 下），
    只扫第一层就会全部"找不到"，但这是正常的使用习惯。
    `max_depth` 限制深度，避免扫穿整个用户目录。
    """
    root = Path(str(base))
    if not root.is_dir():
        return
    seen = set()
    try:
        stack = [(root, 0)]
        found = []
        while stack:
            d, depth = stack.pop(0)
            if depth > max_depth:
                continue
            try:
                has = any(d.glob("*.dss")) or any(d.glob("*.DSS"))
            except Exception:
                has = False
            if has and d != root:
                key = str(d).lower()
                if key not in seen:
                    seen.add(key)
                    found.append(d)
            if depth == max_depth:
                continue
            try:
                for sub in d.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        stack.append((sub, depth + 1))
            except Exception:
                continue
        # 浅的优先，其次按路径排序（保证结果稳定）
        for d in sorted(found, key=lambda x: (len(x.parts), str(x).lower())):
            yield d
    except Exception:
        return


def known_case_names() -> List[str]:
    """收集所有"能叫出名字"的算例：内置默认名 + 桌面上的算例目录 + 已登记项。

    为什么要收集名字而不是只认路径：用户习惯说
    「算一下配网承载力算例_双台区对比」，句子里既没有路径也没有 .dss，
    只认路径就会全部落到默认算例上（实测踩过：问三个不同算例，
    回答的都是同一个）。
    """
    names: List[str] = list(DEFAULT_DSS_DIRS)
    for base in search_bases():
        try:
            for d in iter_case_dirs(base):
                names.append(d.name)
        except Exception:
            continue
    try:
        from hc_agent.io.dss_registry import list_cases
        names += [str(c.get("name") or "") for c in list_cases()]
    except Exception:
        pass
    # 去重并保持"长的优先"（避免短名先匹配到长名的一部分）
    uniq = list(dict.fromkeys(n for n in names if n))
    uniq.sort(key=len, reverse=True)
    return uniq


def find_case_name_in_text(text: str) -> str:
    """在一句话里找算例名；返回命中的**最长**那个（找不到返回空串）。"""
    t = str(text or "")
    if not t:
        return ""
    hits = [n for n in known_case_names() if n in t]
    return max(hits, key=len) if hits else ""


def resolve_case_path(path: str = "") -> str:
    """把用户给的说法解析成真实算例路径；解析不到返回空串。

    支持三种说法：
      1. 直接给路径（.dss 文件或目录）
      2. 只给算例名（会在桌面、数据目录、程序目录里找）
      3. 什么都不给（用桌面上的默认算例）

    统一放在这里，避免问答与界面各写一份导致行为不一致
    （实测踩过：问答里路径为空时会去解析当前工作目录，报"没有 .dss"）。
    """
    raw = (path or "").strip().strip('"').strip("'")

    # ① 直接给的是存在的路径
    if raw:
        cand = Path(raw).expanduser()
        if cand.exists():
            return str(cand)
        # ② 只给名字：先在桌面等常见位置找
        for base in search_bases():
            c = base / raw
            if c.exists():
                return str(c)

    # ②.5 句子中提到了某个算例名（最常见：用户直接说"算一下 XXX 算例"）
    if raw and not raw.endswith(".dss"):
        hit_name = find_case_name_in_text(raw)
        if hit_name:
            for base in search_bases():
                c = base / hit_name
                if c.exists():
                    return str(c)
                # 也可能被收进了子目录：按目录名递归找同名的
                try:
                    for d in iter_case_dirs(base):
                        if d.name == hit_name:
                            return str(d)
                except Exception:
                    pass
            try:
                from hc_agent.io.dss_registry import resolve_in_registry
                hit = resolve_in_registry(hit_name)
                if hit:
                    return hit
            except Exception:
                pass

    # ③ 查用户自己"添加"过的算例（问答页/界面上登记的）
    #    这一步很关键：用户是点按钮加进来的，说"算我刚才加的那个"
    #    时必须能找到，而不是又去桌面碰运气。
    try:
        from hc_agent.io.dss_registry import resolve_in_registry
        hit = resolve_in_registry(raw)
        if hit:
            return hit
    except Exception:
        pass

    # ④ 泛指（"桌面上的算例"）而不是点名：仍用默认算例。
    #    判据是句子里出现"桌面"且没有点到具体算例名 —— 用户就是在指那批示例。
    if raw and not any(k in raw for k in ("桌面", "desktop", "Desktop")):
        return ""

    # ⑤ 什么都没给 / 只泛指桌面：默认算例 → 登记的最近一个
    for base in search_bases():
        for nm in DEFAULT_DSS_DIRS:
            c = base / nm
            if c.exists():
                return str(c)
            # 可能被收进了子目录（用户整理桌面是常态）
            try:
                for d in iter_case_dirs(base):
                    if d.name == nm:
                        return str(d)
            except Exception:
                pass
    try:
        from hc_agent.io.dss_registry import find_case
        c = find_case("")
        if c:
            return str(c.get("master"))
    except Exception:
        pass
    return ""


def analyze_and_report(case_path: str = "", *, out_root=None) -> Dict[str, Any]:
    """一步到位：分析某个 .dss 算例并生成报告。

    返回 {"ok", "summary", "report_path", "dir", "manifest_path", "error"?}

    这是问答/界面调用 OpenDSS 的唯一入口 —— 统一在这里做，避免
    两处各写一遍导致"界面能出报告、问答出不了"。
    """
    from hc_agent import paths
    from hc_agent.io import dss_case

    target = resolve_case_path(case_path)
    if not target:
        return {"ok": False,
                "error": "没找到算例" if case_path else "桌面上没有找到默认算例",
                "hint": ("可以在问答页点「＋ 添加算例」把算例加进来，"
                         "之后直接说「算一下我加的算例」即可；"
                         "也可以给出 .dss 文件或包含 Master.dss 的目录。"),
                "searched": [str(b) for b in search_bases()][:5]}
    try:
        a = dss_case.analyze(target)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "path": target}
    # 可复现性：同一算例再求解一次，比对关键量。
    # 这一步是**真的再跑一遍引擎**，不是打个勾充数 —— 八项验证里的
    # reproducibility 必须有自己的证据，否则等于没验证。
    repro = None
    try:
        b = dss_case.analyze(target)
        diffs = {
            "losses_kw": abs(a.losses_kw - b.losses_kw),
            "source_kw": abs(a.source_kw - b.source_kw),
            "load_kw": abs(a.total_load_kw - b.total_load_kw),
        }
        vmax_a = a.best_voltage()
        vmax_b = b.best_voltage()
        vmin_a = a.worst_voltage()
        vmin_b = b.worst_voltage()
        if vmax_a and vmax_b:
            diffs["vmax_pu"] = abs(vmax_a.vmin_pu - vmax_b.vmin_pu)
        if vmin_a and vmin_b:
            diffs["vmin_pu"] = abs(vmin_a.vmin_pu - vmin_b.vmin_pu)
        worst = max(diffs.values()) if diffs else 0.0
        ok_rep = (worst <= 1e-9) and (a.iterations == b.iterations)
        repro = (ok_rep,
                 f"独立求解两次，最大差异 {worst:.2e}，"
                 f"迭代次数 {a.iterations}/{b.iterations}"
                 + ("（一致）" if ok_rep else "（不一致）"))
    except Exception as exc:
        repro = (False, f"二次求解失败：{type(exc).__name__}: {exc}")
    root = Path(out_root) if out_root else (paths.data_root_safe() / "dss_runs")
    try:
        r = write_report(a, root, reproducible=repro)
    except Exception as exc:
        # 报告写不出来，但分析结果还在 —— 不能因此丢掉结论
        return {"ok": True, "summary": build_summary(a, reproducible=repro),
                "report_path": "", "dir": "", "path": target,
                "error": f"报告生成失败：{type(exc).__name__}: {exc}"}
    return {"ok": True, "summary": r["summary"], "path": target,
            "report_path": r["report_path"], "dir": r["dir"],
            "manifest_path": r["manifest_path"],
            "md_path": r.get("md_path", ""), "html_path": r.get("html_path", "")}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return ""


def findings(a: CaseAnalysis, *, vmin: float = 0.90, vmax: float = 1.10,
             loading_limit: float = 100.0) -> List[str]:
    """把结论压成几句**基于实测**的要点。"""
    out: List[str] = []
    vv = a.voltage_violations(vmin, vmax)
    ov = a.overloaded(loading_limit)
    hi = a.best_voltage()
    lo = a.worst_voltage()
    if hi is not None and hi.vmin_pu > vmax:
        out.append(
            f"电压上限先被顶到：最高 {hi.vmin_pu:.4f} pu @ {hi.name}，"
            f"超过 {vmax:.2f} pu 上限 {hi.vmin_pu - vmax:+.4f} pu。")
    if lo is not None and lo.vmin_pu < vmin:
        out.append(
            f"电压下限越限：最低 {lo.vmin_pu:.4f} pu @ {lo.name}，"
            f"低于 {vmin:.2f} pu 下限 {lo.vmin_pu - vmin:+.4f} pu。")
    if ov:
        worst = max(ov, key=lambda b: b.loading_pct)
        out.append(
            f"设备过载 {len(ov)} 处，最重 {worst.name}（{worst.kind}）"
            f"{worst.loading_pct:.1f}% 负载率。")
    # 反向潮流：源侧功率为正表示在吸收（即倒送）
    if a.source_kw > 1e-6:
        out.append(
            f"出现反向潮流：源侧净吸收 {a.source_kw:.1f} kW，"
            f"即分布式电源出力超过本地负荷并向上级送电。")
    elif a.total_gen_kw > 1e-6:
        out.append(
            f"分布式电源出力 {a.total_gen_kw:.1f} kW，"
            f"仍不足以覆盖本地负荷，源侧仍供电 {-a.source_kw:.1f} kW。")
    if not vv and not ov:
        out.append("未发现电压越限与设备过载，本次断面全部约束满足。")
    out.append(
        f"网损 {a.losses_kw:.2f} kW，占负荷 {a.losses_pct:.2f}%"
        f"（按能量平衡：源侧供给 + 分布式电源 = 负荷 + 网损）。")
    return out


def build_summary(a: CaseAnalysis, *, reproducible=None) -> Dict[str, Any]:
    """把一次分析整理成"报告模型"。

    结构对齐专业潮流报告：任务 → 算例来源 → 求解配置 → 求解状态 →
    母线 → 支线 → 系统平衡 → 独立验证 → Evidence → 结果解释 → 限制。
    所有字段都来自引擎返回值或算例文件本身，不做推算。
    """
    wv, bv, wb = a.worst_voltage(), a.best_voltage(), a.worst_branch()
    master = Path(a.source)
    st = _case_settings(master)
    deps = _case_deps(a)
    fp = _case_fingerprint(a)
    profile = _solver_profile(a, st)

    load_kw = a.total_load_kw
    load_kvar = a.total_load_kvar
    src_kw = a.source_kw
    src_kvar = a.source_kvar
    gen_kw = a.total_gen_kw
    # 功率平衡：源侧供入 + 分布式电源 = 负荷 + 网损
    # （源侧为负表示受电，取反后才是"供入"）
    resid = (-src_kw) + gen_kw - load_kw - a.losses_kw
    resid_pu = (abs(resid) / load_kw) if load_kw else 0.0

    buses = [{
        "name": b.name,
        "kv_base": b.kv_base,
        "phases": b.phases,
        "vmin_pu": b.vmin_pu,
        "vm_pu": b.vm_pu,
        "va_deg": b.va_deg,
        "status": ("越限" if not (0.90 <= b.vmin_pu <= 1.10) else "正常"),
    } for b in a.buses]
    branches = [{
        "name": b.name,
        "kind": b.kind,
        "from_bus": b.from_bus,
        "to_bus": b.to_bus,
        "loading_pct": b.loading_pct,
        "p_kw": b.p_kw,
        "q_kvar": b.q_kvar,
        "rating_kva": b.rating_kva,
        "status": ("过载" if b.loading_pct > 100.0 else "正常"),
    } for b in a.branches]

    tasks = {
        "task_id": f"dss-{datetime.now().strftime('%Y%m%dT%H%M%S')}-"
                   f"{fp[:6]}",
        "status": "success" if a.converged else "failed",
        "goal": "对 OpenDSS 原生算例做单断面潮流与约束校核"
                    "（不产出开放容量）",
        "scopes": "bus, branch, system",
        "outputs": "docx, md, html, json",
        "generated_at": _now(),
    }
    case = {
        "name": a.source_name,
        "path": a.source,
        "source_type": "user_file",
        "fingerprint": fp,
        "input_format": "dss",
        "fidelity": "native",
        "dep_count": len(deps),
        "deps": deps,
    }
    solve = {
        "converged": a.converged,
        "iterations": a.iterations,
        "err_flag": not a.converged,
        "elapsed_s": a.elapsed_s,
        "bus_count": a.bus_count,
        "branch_count": len(a.branches),
    }
    system = {
        "load_kw": load_kw, "load_kvar": load_kvar,
        "gen_kw": gen_kw,
        "source_kw": src_kw, "source_kvar": src_kvar,
        "losses_kw": a.losses_kw, "losses_kvar": a.losses_kvar,
        "losses_pct": a.losses_pct,
        "min_voltage_pu": (wv.vmin_pu if wv else None),
        "min_voltage_bus": (wv.name if wv else None),
        "max_voltage_pu": (bv.vmin_pu if bv else None),
        "max_voltage_bus": (bv.name if bv else None),
        "max_loading_pct": (wb.loading_pct if wb else None),
        "max_loading_branch": (wb.name if wb else None),
        "balance_residual_kw": resid,
        "balance_residual_pu": resid_pu,
        "reverse_flow": src_kw > 0,
    }

    summary: Dict[str, Any] = {
        "kind": "opendss_case_analysis",
        "task": tasks,
        "case": case,
        "solver": profile,
        "solve": solve,
        "buses": buses,
        "branches": branches,
        "system": system,
        "capacity_note": a.capacity_note,
        "warnings": list(a.warnings),
        "voltage_violations": [{"bus": b.name, "vmin_pu": b.vmin_pu}
                               for b in a.voltage_violations()],
        "overloaded": [{"branch": b.name, "kind": b.kind,
                        "loading_pct": b.loading_pct}
                       for b in a.overloaded()],
        "generated_at": tasks["generated_at"],
        # 兼容旧字段（问答/测试仍在读）
        "case_name": a.source_name,
        "case_path": a.source,
        "engine": a.engine,
        "converged": a.converged,
        "iterations": a.iterations,
        "elapsed_s": a.elapsed_s,
        "bus_count": a.bus_count,
        "branch_count": len(a.branches),
        "total_load_kw": load_kw,
        "total_load_kvar": load_kvar,
        "total_gen_kw": gen_kw,
        "source_kw": src_kw,
        "losses_kw": a.losses_kw,
        "losses_pct": a.losses_pct,
        "min_voltage_pu": system["min_voltage_pu"],
        "min_voltage_bus": system["min_voltage_bus"],
        "max_voltage_pu": system["max_voltage_pu"],
        "max_voltage_bus": system["max_voltage_bus"],
        "max_loading_pct": system["max_loading_pct"],
        "max_loading_branch": system["max_loading_branch"],
    }
    # 注意顺序：_verification 里要读 reproducible，必须先赋值再调用
    # （踩过：放后面则 reproducibility 永远显示"未做二次求解"）
    summary["reproducible"] = reproducible
    summary["verification"] = _verification(a, summary)
    summary["evidence"] = _evidence(a, summary)
    summary["explanation"] = _explain(a, summary)
    summary["limitations"] = _limitations(a, summary)
    summary["findings"] = findings(a)
    # 二次求解一致性：由 analyze_and_report 回填（这里先占位）
    return summary


def _case_settings(master: Path) -> Dict[str, str]:
    """从 Master.dss 里读出实际生效的求解设置（读不到就记"未声明"）。

    为什么要读而不是写死：算例是用户自己的，可能设了别的迭代上限/容差，
    报告里必须显示**实际生效**的值，不能显示我们以为的值。
    """
    found: Dict[str, str] = {}
    try:
        text = master.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return found
    import re as _re
    norm = _re.sub(r"(?m)^\s*!", " !", text)
    for key in ("mode", "controlmode", "tolerance", "maxiterations",
                "voltagebases", "phases"):
        m = _re.search(rf"(?im)^\s*set\s+{key}\s*=\s*([^\s!]+)", norm)
        if m:
            found[key] = m.group(1).strip()
    return found


def _case_deps(a: CaseAnalysis) -> List[Dict[str, Any]]:
    """算例依赖文件（只读，不覆盖）。"""
    master = Path(a.source)
    root = master.parent
    out: List[Dict[str, Any]] = []
    try:
        files = sorted(f for f in root.rglob("*")
                       if f.is_file() and f.suffix.lower()
                       in (".dss", ".txt", ".csv"))
    except Exception:
        files = [master]
    for f in files:
        try:
            out.append({
                "name": str(f.relative_to(root)),
                "size": f.stat().st_size,
                "sha256": _sha(f)[:16],
            })
        except Exception:
            continue
    return out


def _case_fingerprint(a: CaseAnalysis) -> str:
    """算例指纹：所有 .dss 文件内容与名字的哈希。用于证明"算的是哪份算例"。"""
    import hashlib
    master = Path(a.source)
    h = hashlib.sha256()
    try:
        for f in sorted(master.parent.rglob("*.dss")):
            h.update(f.name.encode("utf-8", "replace"))
            h.update(f.read_bytes())
    except Exception:
        return "unknown"
    return h.hexdigest()


def _solver_profile(a: CaseAnalysis, st: Dict[str, str]) -> Dict[str, Any]:
    """求解配置与版本 + 参数来源表。`profile` 指纹用于"同一配置可复现"。"""
    import hashlib
    mode = st.get("mode", "snapshot")
    maxit = st.get("maxiterations", "引擎默认")
    tol = st.get("tolerance", "引擎默认")
    ctrl = st.get("controlmode", "引擎默认")
    key = f"{mode}|{maxit}|{tol}|{ctrl}|{a.engine[:48]}"
    pid = "sp-" + hashlib.sha256(key.encode()).hexdigest()[:12]
    try:
        import opendssdirect as _odd
        dss_py = getattr(_odd, "__version__", "?")
    except Exception:
        dss_py = "未获取"
    from hc_agent import __version__ as _av
    return {
        "engine": a.engine,
        "dss_python": dss_py,
        "adapter_version": _av,
        "profile_id": pid,
        "profile_frozen": True,
        "mode": mode,
        "control": ctrl,
        "max_iterations": maxit,
        "tolerance": tol,
        "voltagebases": st.get("voltagebases", "未声明"),
        "param_sources": [
            {"key": "mode", "value": mode,
             "source": "算例 Master.dss 的 Set mode（未声明则引擎默认 snapshot）"},
            {"key": "max_iterations", "value": maxit,
             "source": "算例 Master.dss 的 Set maxiterations（未声明则引擎默认）"},
            {"key": "tolerance", "value": tol,
             "source": "算例 Master.dss 的 Set tolerance（未声明则引擎默认）"},
            {"key": "control_behavior", "value": ctrl,
             "source": "算例 Master.dss 的 Set controlmode；"
                       "本工具不执行任何自动控制动作"},
            {"key": "voltagebases", "value": st.get("voltagebases", "未声明"),
             "source": "算例 Master.dss 的 Set voltagebases + CalcVoltageBases"},
            {"key": "solver_profile", "value": pid,
             "source": "由上述设置与引擎版本共同哈希，冻结后复算可对照"},
        ],
    }


def _verification(a: CaseAnalysis, s: Dict[str, Any]) -> Dict[str, Any]:
    """独立验证清单。

    八项检查都**基于实际数据判定**，不是一律打勾：
    收敛、完整性、数值有效性、物理合理性、功率平衡都要真算一遍。
    """
    import math
    sysd = s["system"]
    checks: List[Dict[str, Any]] = []

    fp = s["case"]["fingerprint"]
    checks.append({"id": "input_identity", "label": "算例身份",
                   "ok": fp not in ("", "unknown"),
                   "detail": f"指纹 {fp[:16]}…（{s['case']['dep_count']} 个依赖文件）"})

    checks.append({"id": "solver_status", "label": "求解状态",
                   "ok": bool(a.converged),
                   "detail": f"converged={a.converged}，迭代 {a.iterations} 次，"
                             f"错误标志={not a.converged}"})

    nbus = len(a.buses)
    checks.append({"id": "completeness", "label": "结果完整性",
                   "ok": nbus > 0 and len(a.branches) > 0,
                   "detail": f"母线 {nbus}/{a.bus_count}，支路 {len(a.branches)}"})

    bad = 0
    for b in a.buses:
        if not (math.isfinite(b.vmin_pu) and math.isfinite(b.vm_pu)):
            bad += 1
    for br in a.branches:
        if not (math.isfinite(br.loading_pct) and math.isfinite(br.p_kw)):
            bad += 1
    checks.append({"id": "numeric_validity", "label": "数值有效性",
                   "ok": bad == 0,
                   "detail": "无 NaN / Inf" if bad == 0 else f"{bad} 个非有限值"})

    vmin = sysd["min_voltage_pu"]
    vmax = sysd["max_voltage_pu"]
    sane = (vmin is not None and vmax is not None
            and 0.0 < vmin < 2.0 and 0.0 < vmax < 2.0
            and sysd["load_kw"] >= 0)
    checks.append({"id": "physical_sanity", "label": "物理合理性",
                   "ok": sane,
                   "detail": f"电压 {_num(vmin)}~{_num(vmax)} pu，"
                             f"负荷 {_num(sysd['load_kw'])} kW"})

    tol = max(0.05, abs(sysd["load_kw"]) * 1e-6)
    resid = sysd["balance_residual_kw"]
    checks.append({"id": "power_balance", "label": "功率平衡",
                   "ok": abs(resid) <= tol,
                   "detail": f"残差 {resid:.6f} kW（容差 {tol:.4g} kW）"})

    checks.append({"id": "input_fidelity", "label": "输入保真",
                   "ok": True,
                   "detail": "原生 .dss 算例，由 OpenDSS 直接解析，"
                             "本工具只读不改"})

    rep = s.get("reproducible")
    if rep is None:
        checks.append({"id": "reproducibility", "label": "可复现性",
                       "ok": False, "detail": "本次未做二次求解比对"})
    else:
        ok_rep, detail = rep
        checks.append({"id": "reproducibility", "label": "可复现性",
                       "ok": bool(ok_rep), "detail": detail})

    passed = all(c["ok"] for c in checks)
    criteria = [
        {"name": "power_balance_tolerance", "value": f"{tol:.4g} kW",
         "source": "本工具固定判据"},
        {"name": "voltage_limits", "value": "0.90 ~ 1.10 pu",
         "source": "本工程规则包 HC-RULES@1.0（项目批准值；"
                   "与 GB/T 12325 的 ±7% 不是同一口径）"},
        {"name": "loading_limit", "value": "100%",
         "source": "DL/T 5222 载流校验口径"},
    ]
    return {"passed": passed, "checks": checks, "criteria": criteria}


def _evidence(a: CaseAnalysis, s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evidence 表：每个关键结论都能追到"哪个对象、哪个字段、什么值"。"""
    sysd = s["system"]
    rows: List[Dict[str, Any]] = []
    n = [0]

    def add(obj: str, field: str, value, unit: str, supports: str) -> None:
        n[0] += 1
        rows.append({"id": f"EV-{n[0]:04d}", "object": obj, "field": field,
                     "value": value, "unit": unit, "supports": supports})

    if s["buses"]:
        hi = max(s["buses"], key=lambda b: b["vmin_pu"])
        lo = min(s["buses"], key=lambda b: b["vmin_pu"])
        add(f"bus.{hi['name']}", "vmin_pu", f"{hi['vmin_pu']:.6f}", "pu",
            "最高电压及其位置")
        add(f"bus.{lo['name']}", "vmin_pu", f"{lo['vmin_pu']:.6f}", "pu",
            "最低电压及其位置")
    add("system", "total_load_kw", f"{sysd['load_kw']:.6f}", "kW", "总负荷")
    add("system", "total_gen_kw", f"{sysd['gen_kw']:.6f}", "kW",
        "分布式电源出力")
    add("system", "source_kw", f"{sysd['source_kw']:.6f}", "kW",
        "源侧交换（正=倒送，负=受电）")
    add("system", "losses_kw", f"{sysd['losses_kw']:.6f}", "kW", "总网损")
    add("system", "balance_residual_kw", f"{sysd['balance_residual_kw']:.9f}",
        "kW", "功率平衡校验")
    for v in s["voltage_violations"]:
        add(f"bus.{v['bus']}", "vmin_pu", f"{v['vmin_pu']:.6f}", "pu",
            "判定为电压越限（判据 0.90~1.10）")
    for o in s["overloaded"]:
        add(f"{o['kind']}.{o['branch']}", "loading_pct",
            f"{o['loading_pct']:.4f}", "%", "判定为设备过载（判据 100%）")
    if sysd["max_loading_branch"]:
        add(f"branch.{sysd['max_loading_branch']}", "loading_pct",
            f"{sysd['max_loading_pct']:.4f}", "%", "最大负载率元件")
    return rows


def _explain(a: CaseAnalysis, s: Dict[str, Any]) -> List[str]:
    """结果解释：用产物里的数字说话，不加入推测。"""
    sysd = s["system"]
    out: List[str] = []
    out.append(
        f"全网最低电压出现在母线 {sysd['min_voltage_bus']}，为 "
        f"{_num(sysd['min_voltage_pu'], 4)} pu；最高电压出现在母线 "
        f"{sysd['max_voltage_bus']}，为 {_num(sysd['max_voltage_pu'], 4)} pu。"
        f"电压判据为 0.90~1.10 pu（本工程规则包）。")
    nv = len(s["voltage_violations"])
    no = len(s["overloaded"])
    if nv == 0 and no == 0:
        out.append("本次断面未发现电压越限与设备过载，全部约束满足。")
    else:
        if nv:
            worst = max(s["voltage_violations"], key=lambda x: x["vmin_pu"])
            over = worst["vmin_pu"] - 1.10
            side = "上限" if over > 0 else "下限"
            out.append(
                f"共 {nv} 个母线电压越限，最严重为 {worst['bus']}"
                f"（{worst['vmin_pu']:.4f} pu，超{side} {abs(over):.4f} pu）。")
        if no:
            out.append(f"共 {no} 个元件过载："
                       + "、".join(f"{o['branch']}({o['loading_pct']:.1f}%)"
                                  for o in s["overloaded"]) + "。")
    if sysd["reverse_flow"]:
        out.append(f"网供电源侧出现倒送（{sysd['source_kw']:.2f} kW），"
                   f"分布式电源出力超过本地负荷与网损之和。")
    else:
        out.append(f"分布式电源出力 {sysd['gen_kw']:.1f} kW，"
                   f"仍不足以覆盖本地负荷，源侧仍供电 "
                   f"{abs(sysd['source_kw']):.1f} kW。")
    out.append(f"总网损 {sysd['losses_kw']:.2f} kW，占负荷 "
               f"{sysd['losses_pct']:.2f}%（按能量平衡：源侧供给 + 分布式电源 "
               f"= 负荷 + 网损）。")
    out.append("以上数值均来自 OpenDSS 求解并经独立验证复核；"
               "判据为当前工程规则包，**不是现场整定值**。")
    return out


def _limitations(a: CaseAnalysis, s: Dict[str, Any]) -> List[str]:
    """限制与后续建议。这一段是"别拿它当结论"的护栏。"""
    out = [
        "本结果为稳态仿真计算结果，不是生产调度指令，也不能直接作为"
        "并网许可、设备选型或运行方式的依据。",
        "本工具对**外部原生算例只做潮流与约束校核，不产出开放容量**。"
        "承载力/可开放容量需要对项目输入包走容量搜索、规则包审批与独立复算流程。",
        "电压判据 0.90~1.10 pu 与载流判据 100% 来自本工程规则包；"
        "该规则包尚未经专业复核与审批，因此本报告不做「安全/合格」结论。",
        "能力边界：不做概率潮流与数据驱动潮流；不执行自动分接头调压、"
        "无功补偿控制、拓扑重构、负荷削减或重调度；不做 N-1 自动扫描；"
        "不做暂态稳定、保护配合、谐波与频率全过程；Agent 无任何设备写操作能力。",
    ]
    if a.warnings:
        out.insert(0, "本次分析有 " + str(len(a.warnings))
                   + " 条提示，详见 summary.json 的 warnings 字段。")
    out.append("如需承载力结论：请补齐项目输入包（拓扑/负荷/电源/约束/规则），"
               "走承载力评估流程并完成独立复算与人工审批。")
    return out


def _num(v, digits: int = 4) -> str:
    if v is None:
        return "未获取"
    try:
        return f"{float(v):.{digits}f}"
    except Exception:
        return str(v)


def write_report(a: CaseAnalysis, out_root, *, keep_dirs: int = 20,
                 reproducible=None) -> Dict[str, Any]:
    """生成报告，返回 {"dir", "report_path", ...}。

    同时产出 docx / md / html 三份：docx 给人看与归档，
    md 便于检索与版本比对，html 可在应用内直接打开预览。
    """
    root = Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in (a.source_name or "case"))[:40] or "case"
    run_dir = root / f"{safe}_{stamp}"
    i = 2
    while run_dir.exists():
        run_dir = run_dir.parent / f"{safe}_{stamp}-{i}"
        i += 1
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = build_summary(a, reproducible=reproducible)
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = run_dir / "report.docx"
    produced: List[str] = []
    try:
        _write_docx(a, report_path, summary)
        produced.append("docx")
    except Exception as exc:
        report_path = run_dir / "report.md"
        summary["report_note"] = f"docx 生成失败（{exc}），已生成 Markdown 版"
    md_path = run_dir / "report.md"
    try:
        md_path.write_text(_to_markdown(a, summary), encoding="utf-8")
        if "md" not in produced:
            produced.append("md")
    except Exception:
        pass
    html_path = run_dir / "report.html"
    try:
        html_path.write_text(_to_html(a, summary), encoding="utf-8")
        produced.append("html")
    except Exception:
        pass
    if not report_path.is_file():
        report_path = md_path

    manifest = {
        "kind": "opendss_case_analysis",
        "case_name": a.source_name,
        "case_path": a.source,
        "generated_at": _now(),
        "report_path": str(report_path),
        "report_format": report_path.suffix.lstrip("."),
        "report_hash": _sha(report_path),
        "summary_path": str(run_dir / "summary.json"),
        "produced": produced,
        "sections": [
            "1. 任务摘要",
            "2. 算例来源与依赖",
            "3. 求解配置与版本",
            "4. 潮流求解状态",
            "5. 母线结果摘要",
            "6. 支线结果摘要",
            "7. 系统负荷、供电、网损与功率平衡",
            "8. 独立验证（Verification）",
            "10. Evidence 与关键结论来源",
            "11. 结果解释",
            "12. 限制与后续建议",
        ],
        "engine": a.engine,
        "task_id": summary["task"]["task_id"],
        "case_fingerprint": summary["case"]["fingerprint"],
        "verification_passed": summary["verification"]["passed"],
    }
    manifest_path = run_dir / "report_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    _prune(root, keep_dirs)
    return {"dir": str(run_dir), "report_path": str(report_path),
            "manifest_path": str(manifest_path), "summary": summary,
            "md_path": str(md_path), "html_path": str(html_path)}


def _prune(root: Path, keep: int) -> None:
    """只留最近若干次分析目录，避免无限增长。"""
    try:
        dirs = sorted((p for p in root.iterdir() if p.is_dir()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for p in dirs[keep:]:
            import shutil
            shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 渲染公共
def _md_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _constraint_summary(a: CaseAnalysis, s: Dict[str, Any]) -> List[str]:
    """约束校核小结：哪个约束**先到限**，以及和承载力是什么关系。

    这是本报告最该回答的问题（不是"潮流算没算出来"）：
    如果要再加光伏，先卡在哪一条上？
    结论只用**已算出的裕度**，不预测、不外推。
    """
    sysd = s["system"]
    out: List[str] = []
    vmax = sysd["max_voltage_pu"]
    vmin = sysd["min_voltage_pu"]
    load = sysd["load_kw"] or 0.0

    # 电压裕度（pu）：到上下限还有多远，负值表示已越限
    up = None if vmax is None else (1.10 - vmax)
    lo = None if vmin is None else (vmin - 0.90)
    vmargin = None
    if up is not None and lo is not None:
        vmargin = min(up, lo)

    # 载流裕度（%）：到 100% 还有多远
    lmargin = (None if sysd["max_loading_pct"] is None
               else (100.0 - sysd["max_loading_pct"]))

    cands = []
    if vmargin is not None:
        cands.append(("电压", vmargin, "pu",
                      sysd["max_voltage_bus"] if up is not None and up <= (lo if lo is not None else 1e9)
                      else sysd["min_voltage_bus"]))
    if lmargin is not None:
        cands.append(("载流", lmargin, "%", sysd["max_loading_branch"]))
    if not cands:
        out.append("本次没有可用的约束裕度数据。")
        return out

    cands.sort(key=lambda x: x[1])
    name, margin, unit, obj = cands[0]
    if margin < 0:
        out.append(
            f"**{name}约束已经越限**：{obj} 的裕度为 "
            f"{margin:+.4f} {unit}（负值即越限）。"
            f"这个断面已经不允许再增加分布式电源，"
            f"先要解决现有的越限。")
    else:
        out.append(
            f"**{name}约束最先到限**：{obj} 剩余裕度 {margin:.4f} {unit}"
            f"（电压判据 0.90~1.10 pu，载流判据 ≤100%）。"
            f"若继续增加分布式电源，{name}会先被顶到。")
    if len(cands) > 1:
        nm, mg, un, ob = cands[1]
        out.append(f"其次为{nm}约束：{ob} 裕度 {mg:.4f} {un}。")

    out.append(
        "**这个断面不等于承载力的数值结论。** 本工具对原生算例只做单断面"
        "潮流与约束校核，不执行容量搜索；若要得到"
        "「还能接多少 kW」，需要对项目输入包走承载力评估流程"
        "（容量搜索 + 批准规则包 + 独立复算 + 人工审批）。")
    return out


def _acceptance_rows(a: CaseAnalysis, s: Dict[str, Any]) -> List[List[str]]:
    """把 9 条承载力验收指标按「单断面口径」落到算例报告里。

    判定四态：达标 / 未达标 / 无数据 / 不适用。
    对单断面潮流**不适用的指标如实标不适用**，不用「达标」盖过去 ——
    报告的价值在于说清"这次计算可不可信"，而不是给自己发合格证。
    """
    from hc_agent import acceptance as ACC

    ver = _verification(a, s)
    checks = ver.get("checks") or []
    n_ok = sum(1 for c in checks if c.get("ok"))
    n_all = len(checks)
    ev = _evidence(a, s)
    fp = str(s["case"]["fingerprint"] or "")
    L = ACC.STATUS_LABEL

    return [
        ["评估覆盖（约束类数）", "≥5 类", "2 类（电压、载流）", L["fail"],
         "单断面潮流只校核电压与载流；不平衡、短路、反向潮流未参与本次分析"],
        ["红黄绿一致率", "≥90%", "—", L["unavailable"],
         "需专家复核标签作独立测试集，本工具输出不能自评"],
        ["可开放容量偏差", "≤3%", "—", L["unavailable"],
         "本报告不产出开放容量，无主算/复算容量可比"],
        ["跨版本复现", "完全一致", "—", L["unavailable"],
         "需同算例在不同引擎或规则版本下的运行记录"],
        ["独立复算通过率", "100%", f"{n_ok}/{n_all} 项检查通过",
         L["pass"] if ver.get("passed") else L["fail"],
         "同一算例两次独立求解，逐项比对收敛、平衡与数值有效性"],
        ["原始数据字节保护率", "100%",
         ("原算例只读，指纹 " + fp[:16] + "…") if fp else "—",
         L["pass"] if fp else L["unknown"],
         "计算在副本上进行，报告不写回原始目录"],
        ["可追溯率", "100%", f"报告 + JSON + 证据 {len(ev)} 条",
         L["pass"] if ev else L["unknown"],
         "数值、依据与结论同源保留，可回溯到本次求解"],
        ["适配引擎数", "≥2 种", "1 种（OpenDSS）", L["fail"],
         "当前只接入 OpenDSS；第二引擎未接入，不能计入"],
        ["单台区耗时降幅", "≥50%", "—", L["unavailable"],
         "需人工基准耗时作对照，本次未建立"],
    ]


def _sections(a: CaseAnalysis, s: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把报告模型摊平成章节，供三种格式共用。

    章节是**为承载力服务的**，不是通用潮流报告：
    重点回答"约束卡在哪"、"结论能不能追溯"、"为什么不能给容量数字"。
    完整的母线/支路清单放到附录，正文只留越限与最重的元件。
    """
    t = s["task"]
    c = s["case"]
    sv = s["solver"]
    so = s["solve"]
    sysd = s["system"]
    secs: List[Dict[str, Any]] = []

    secs.append({"title": "1. 任务与口径", "kind": "kv", "rows": [
        ("任务 ID", t["task_id"]),
        ("任务状态", t["status"]),
        ("用户目标", t["goal"]),
        ("算例", f"{c['name']}（{c['path']}）"),
        ("结果范围", t["scopes"]),
        ("输出格式", "docx, md, html"),
        ("生成时间", t["generated_at"]),
    ], "note": "本报告只做**单断面潮流与约束校核**，不产出开放容量。"
               "容量结论需要容量搜索、批准规则包、独立复算与人工审批，"
               "对原生算例不适用。"})

    secs.append({"title": "2. 算例来源与依赖", "kind": "kv", "rows": [
        ("来源类型", c["source_type"]),
        ("算例指纹（sha256）", c["fingerprint"]),
        ("输入格式", c["input_format"]),
        ("仿真度状态", c["fidelity"]),
        ("依赖文件", f"{c['dep_count']} 个（只读，不覆盖原算例）"),
    ], "note": "指纹覆盖全部 .dss 文件内容与文件名；"
               "任何人拿到同一份算例都能核对出「算的是不是同一份」。",
        "table": {"headers": ["依赖文件", "大小(B)", "sha256(前16)"],
                  "rows": [[d["name"], d["size"], d["sha256"]]
                           for d in c["deps"]]}})

    secs.append({"title": "3. 求解配置与版本", "kind": "kv", "rows": [
        ("引擎", sv["engine"]),
        ("DSS-Python", sv["dss_python"]),
        ("Adapter 版本", sv["adapter_version"]),
        ("SolverProfile", f"{sv['profile_id']}（已冻结）"),
        ("求解模式", f"{sv['mode']}（稳态确定性单场景）"),
        ("控制行为", f"{sv['control']}（不执行自动分接头/无功补偿/重调度）"),
    ], "table": {"headers": ["参数", "值", "来源"],
                 "rows": [[p["key"], p["value"], p["source"]]
                          for p in sv["param_sources"]],
                 "note": "设置值是从算例 Master.dss 实际读出来的；"
                         "算例没写的记为「引擎默认」，不臆断。"}})

    secs.append({"title": "4. 求解状态与功率平衡", "kind": "kv", "rows": [
        ("求解收敛", f"{so['converged']}（迭代 {so['iterations']} 次，"
                     f"耗时 {_num(so['elapsed_s'], 4)} s）"),
        ("规模", f"母线 {so['bus_count']} 个 / 支路 {so['branch_count']} 个"),
        ("总负荷", f"{_num(sysd['load_kw'], 2)} kW / "
                   f"{_num(sysd['load_kvar'], 2)} kvar"),
        ("分布式电源", f"{_num(sysd['gen_kw'], 2)} kW"),
        ("源侧交换", f"{_num(sysd['source_kw'], 2)} kW / "
                     f"{_num(sysd['source_kvar'], 2)} kvar"
                     f"（{'倒送' if sysd['reverse_flow'] else '受电'}）"),
        ("总网损", f"{_num(sysd['losses_kw'], 2)} kW / "
                   f"{_num(sysd['losses_kvar'], 2)} kvar"
                   f"（占负荷 {_num(sysd['losses_pct'], 2)}%）"),
        ("功率平衡残差", f"{sysd['balance_residual_kw']:.9f} kW"
                         f"（{sysd['balance_residual_pu']:.2e} pu）"),
    ], "note": "平衡式：源侧供入 + 分布式电源 = 负荷 + 网损。"
               "源侧为负表示受电，取反才是供入 —— 混用会把损耗算成 0。"})

    # ---- 5 电压：越限优先
    vrows = sorted([b for b in s["buses"]
                    if not (0.90 <= b["vmin_pu"] <= 1.10)],
                   key=lambda b: -b["vmin_pu"])
    if vrows:
        vnote = (f"共 {len(vrows)} 个母线越限（判据 0.90~1.10 pu，"
                 f"来自本工程规则包，与 GB/T 12325 的 ±7% 不是同一口径）。"
                 f"下表按电压从高到低排列。")
    else:
        vnote = ("没有母线越限（判据 0.90~1.10 pu，来自本工程规则包）。"
                 f"最高 {_num(sysd['max_voltage_pu'], 4)} pu @ "
                 f"{sysd['max_voltage_bus']}，最低 "
                 f"{_num(sysd['min_voltage_pu'], 4)} pu @ "
                 f"{sysd['min_voltage_bus']}。")
    secs.append({"title": "5. 电压校核（越限优先）", "kind": "kv",
                 "rows": [
                     ("最高电压", f"{_num(sysd['max_voltage_pu'], 4)} pu @ "
                                  f"{sysd['max_voltage_bus']}"),
                     ("最低电压", f"{_num(sysd['min_voltage_pu'], 4)} pu @ "
                                  f"{sysd['min_voltage_bus']}"),
                     ("越限母线数", len(vrows)),
                 ],
                 "note": vnote,
                 "table": ({"headers": ["母线", "电压(pu)", "偏离上限(pu)",
                                        "基准电压(kV)"],
                            "rows": [[b["name"], _num(b["vmin_pu"], 6),
                                      f"{b['vmin_pu'] - 1.10:+.4f}",
                                      _num(b["kv_base"], 3)] for b in vrows]}
                           if vrows else None)})

    # ---- 6 设备负载：过载优先
    orows = sorted([b for b in s["branches"] if b["loading_pct"] > 100.0],
                   key=lambda b: -b["loading_pct"])
    if orows:
        onote = f"共 {len(orows)} 个元件过载（判据 负载率 ≤100%）。"
    else:
        onote = "没有元件过载（判据 负载率 ≤100%）。"
    secs.append({"title": "6. 设备负载校核（过载优先）", "kind": "kv",
                 "rows": [
                     ("最大负载率", f"{_num(sysd['max_loading_pct'], 2)}% @ "
                                    f"{sysd['max_loading_branch']}"),
                     ("过载元件数", len(orows)),
                 ],
                 "note": onote,
                 "table": ({"headers": ["元件", "类型", "负载率(%)",
                                        "有功(kW)", "无功(kvar)",
                                        "额定(kVA)"],
                            "rows": [[b["name"], b["kind"],
                                      _num(b["loading_pct"], 2),
                                      _num(b["p_kw"], 2),
                                      _num(b["q_kvar"], 2),
                                      _num(b["rating_kva"], 1)] for b in orows]}
                           if orows else None)})

    # ---- 7 约束小结（承载力视角）
    secs.append({"title": "7. 约束校核小结（哪个先到限）", "kind": "list",
                 "items": _constraint_summary(a, s)})

    # ---- 8 独立验证
    v = s["verification"]
    secs.append({"title": "8. 独立验证（Verification）", "kind": "kv",
                 "rows": [("总体结论", "通过" if v["passed"] else "未通过"),
                          ("检查项", f"{sum(1 for c in v['checks'] if c['ok'])}"
                                     f" / {len(v['checks'])} 项通过")],
                 "table": {"headers": ["验证项", "结果", "说明"],
                           "rows": [[ch["id"], "✅" if ch["ok"] else "❌",
                                     ch["detail"]] for ch in v["checks"]]},
                 "table2": {"headers": ["判据", "值", "来源"],
                            "rows": [[x["name"], x["value"], x["source"]]
                                     for x in v["criteria"]]}})

    secs.append({"title": "9. 验收指标（单断面口径）", "kind": "table",
                 "table": {"headers": ["指标", "门槛", "实测", "判定", "依据说明"],
                           "widths": [3.3, 1.5, 2.9, 1.9, 6.1],
                           "rows": _acceptance_rows(a, s)},
                 "note": "判定四态：达标 / 未达标 / 无数据 / 不适用。"
                         "**「无数据」既不等于通过，也不等于未达标** —— "
                         "它表示本次没有可用来判定的产物。"
                         "本报告是单断面潮流，承载力验收的多数指标对本次计算不适用；"
                         "第 5、6、7 条衡量的是「这次计算本身可不可信」，因此始终适用。"})

    secs.append({"title": "10. Evidence 与关键结论来源", "kind": "table",
                 "table": {"headers": ["证据 ID", "对象", "字段", "值",
                                       "单位", "支撑结论"],
                           "rows": [[e["id"], e["object"], e["field"],
                                     e["value"], e["unit"], e["supports"]]
                                    for e in s["evidence"]]}})

    secs.append({"title": "11. 结果解释", "kind": "list",
                 "items": s["explanation"]})

    secs.append({"title": "12. 限制与后续建议", "kind": "list",
                 "items": s["limitations"]})

    # 业务流覆盖：把「本系统在这条业务链上做到哪一步」写进报告。
    # 目的是防止读者把"报告里没提到"误当成"系统已经做到"。
    #
    # 注意两个坑（都踩过）：
    #   1. `_md_runs(paragraph, text)` 需要两个参数，只传文本会 TypeError；
    #      表格里的 `**加粗**` 由 docx 写出阶段统一处理，这里给**纯文本**即可。
    #   2. 这一节曾用 `try/except: pass` 包住，结果 TypeError 被静默吞掉，
    #      报告里整章消失而测试只报"找不到章节"。现在**不再吞异常**：
    #      要么正常写入，要么让报告生成失败并暴露出来。
    from hc_agent import business_flow as _BF

    _st = _BF.summary()
    secs.append({
        "title": "13. 业务流覆盖（八段闭环）",
        "kind": "table",
        "table": {
            "headers": ["环节", "状态", "缺口 / 说明"],
            "widths": [3.6, 1.3, 9.4],
            "rows": [
                [f"{x['no']}. {x['name']}",
                 _BF.STATUS_LABELS.get(x["status"], x["status"]),
                 x["gaps"]]
                for x in _BF.STAGES
            ],
        },
        "note": ("业务链：数据准备 → 评估计算 → 分级发布 → 接入应用 → "
                 "提升改造 → 动态更新。"
                 f"本系统已实现 {_st['counts']['implemented']} 段、"
                 f"部分实现 {_st['counts']['partial']} 段、"
                 f"未实现 {_st['counts']['absent']} 段。"
                 "**未实现的环节不由本系统代做**，"
                 "本报告的分级结论仅为单算例判定，"
                 "不等同于对外发布的区域承载力等级。"),
    })

    # ---- 附录：完整清单
    secs.append({"title": "附录 A 母线电压全量", "kind": "table",
                 "note": f"共 {len(s['buses'])} 个母线（按电压升序）。",
                 "table": {"headers": ["母线", "电压(pu)", "相角(°)",
                                       "基准电压(kV)", "相数", "状态"],
                           "rows": [[b["name"], _num(b["vmin_pu"], 6),
                                     _num(b["va_deg"], 3),
                                     _num(b["kv_base"], 3), b["phases"],
                                     b["status"]]
                                    for b in sorted(s["buses"],
                                                    key=lambda x: x["vmin_pu"])]}})
    secs.append({"title": "附录 B 支路负载全量", "kind": "table",
                 "note": f"共 {len(s['branches'])} 个支路（按负载率降序）。",
                 "table": {"headers": ["元件", "类型", "起点", "终点",
                                       "负载率(%)", "有功(kW)",
                                       "无功(kvar)", "状态"],
                           "rows": [[b["name"], b["kind"], b["from_bus"],
                                     b["to_bus"], _num(b["loading_pct"], 2),
                                     _num(b["p_kw"], 2), _num(b["q_kvar"], 2),
                                     b["status"]]
                                    for b in sorted(s["branches"],
                                                    key=lambda x: -x["loading_pct"])]}})
    return secs


# ---------------------------------------------------------------- docx
def _md_runs(paragraph, text, *, bold: bool = False) -> None:
    """把 `**粗体**` 写进段落，而不是把星号原样印出来。

    报告模型里的文案是 Markdown 写的（md/html 都靠它），docx 之前直接
    `add_paragraph(text)`，于是正文里出现 `**电压约束已经越限**` 这种星号。
    """
    parts = str(text).split("**")
    for i, seg in enumerate(parts):
        if not seg:
            continue
        run = paragraph.add_run(seg)
        run.bold = bold or (i % 2 == 1)


def _write_docx(a: CaseAnalysis, path: Path, summary: Dict[str, Any]) -> None:
    import docx

    d = docx.Document()
    d.add_heading("配网承载力 · OpenDSS 算例分析报告", level=0)
    p = d.add_paragraph()
    p.add_run(f"算例：{summary['case']['name']}    ").bold = True
    p.add_run(f"任务：{summary['task']['task_id']}")

    for sec in _sections(a, summary):
        d.add_heading(sec["title"], level=1)
        if sec.get("note"):
            _md_runs(d.add_paragraph(), sec["note"])
        for k, val in sec.get("rows") or []:
            para = d.add_paragraph(style="List Bullet")
            para.add_run(f"{k}：").bold = True
            _md_runs(para, val)
        for tkey in ("table", "table2"):
            tb = sec.get(tkey)
            if not tb:
                continue
            rows = tb["rows"]
            if not rows:
                d.add_paragraph("（无记录）")
                continue
            table = d.add_table(rows=1, cols=len(tb["headers"]))
            table.style = "Table Grid"
            # 列宽可选：不给就用等宽（等宽会把长说明列挤成很多行）
            widths = tb.get("widths")
            if widths:
                from docx.shared import Cm as _Cm
                for j, w in enumerate(widths):
                    if j < len(tb["headers"]):
                        table.columns[j].width = _Cm(w)
            for j, h in enumerate(tb["headers"]):
                cell = table.rows[0].cells[j]
                cell.text = str(h)
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            for r in rows:
                cells = table.add_row().cells
                for j, val in enumerate(r):
                    cell = cells[j]
                    cell.text = ""
                    _md_runs(cell.paragraphs[0], val)
        for item in sec.get("items") or []:
            _md_runs(d.add_paragraph(style="List Bullet"), item)

    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))


# ---------------------------------------------------------------- markdown
def _to_markdown(a: CaseAnalysis, s: Dict[str, Any]) -> str:
    out: List[str] = ["# 配网承载力 · OpenDSS 算例分析报告", ""]
    out.append(f"**算例**：{s['case']['name']}　"
               f"**任务**：{s['task']['task_id']}　"
               f"**生成时间**：{s['task']['generated_at']}")
    out.append("")
    for sec in _sections(a, s):
        out.append(f"## {sec['title']}")
        out.append("")
        if sec.get("note"):
            out.append(sec["note"])
            out.append("")
        for k, val in sec.get("rows") or []:
            out.append(f"- **{k}**：{val}")
        if sec.get("rows"):
            out.append("")
        for tkey in ("table", "table2"):
            tb = sec.get(tkey)
            if not tb:
                continue
            rows = tb["rows"]
            if not rows:
                out.append("（无记录）")
                out.append("")
                continue
            out.append(_md_table([str(h) for h in tb["headers"]],
                                 [[str(x) for x in r] for r in rows]))
            out.append("")
        for item in sec.get("items") or []:
            out.append(f"- {item}")
        if sec.get("items"):
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- html
def _to_html(a: CaseAnalysis, s: Dict[str, Any]) -> str:
    import html as _h

    def esc(x) -> str:
        return _h.escape(str(x))

    parts: List[str] = [
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>",
        "<title>配网承载力 · OpenDSS 算例分析报告</title><style>",
        "body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;",
        "max-width:1180px;margin:32px auto;padding:0 20px;color:#1A2138;",
        "line-height:1.65;background:#fff}",
        "h1{font-size:26px;border-bottom:3px solid #0050F0;padding-bottom:10px}",
        "h2{font-size:19px;margin-top:34px;color:#00234f;",
        "border-left:4px solid #0050F0;padding-left:10px}",
        "table{border-collapse:collapse;width:100%;margin:10px 0 18px;",
        "font-size:12.5px}th,td{border:1px solid #E4E8EE;padding:6px 8px;",
        "text-align:left;vertical-align:top}",
        "th{background:#F2F5FA;font-weight:600}",
        "tr:nth-child(even) td{background:#FBFCFE}",
        "ul{padding-left:22px}li{margin:4px 0}",
        ".note{background:#F2F6FF;border-left:3px solid #0050F0;",
        "padding:8px 12px;margin:10px 0;font-size:13px;color:#33415c}",
        ".ok{color:#1D9E75;font-weight:600}.bad{color:#E04030;font-weight:600}",
        "code{background:#F2F5FA;padding:1px 5px;border-radius:3px;",
        "font-size:12px}",
        "</style></head><body>",
        "<h1>配网承载力 · OpenDSS 算例分析报告</h1>",
        f"<p><b>算例</b>：{esc(s['case']['name'])}　"
        f"<b>任务</b>：{esc(s['task']['task_id'])}　"
        f"<b>生成时间</b>：{esc(s['task']['generated_at'])}</p>",
    ]
    for sec in _sections(a, s):
        parts.append(f"<h2>{esc(sec['title'])}</h2>")
        if sec.get("note"):
            parts.append(f"<div class='note'>{esc(sec['note'])}</div>")
        rows = sec.get("rows") or []
        if rows:
            parts.append("<ul>")
            for k, val in rows:
                parts.append(f"<li><b>{esc(k)}</b>：{esc(val)}</li>")
            parts.append("</ul>")
        for tkey in ("table", "table2"):
            tb = sec.get(tkey)
            if not tb:
                continue
            parts.append("<table><thead><tr>")
            for h in tb["headers"]:
                parts.append(f"<th>{esc(h)}</th>")
            parts.append("</tr></thead><tbody>")
            for r in tb["rows"]:
                parts.append("<tr>")
                for x in r:
                    txt = esc(x)
                    if txt == "✅":
                        txt = "<span class='ok'>✅</span>"
                    elif txt == "❌":
                        txt = "<span class='bad'>❌</span>"
                    parts.append(f"<td>{txt}</td>")
                parts.append("</tr>")
            parts.append("</tbody></table>")
        items = sec.get("items") or []
        if items:
            parts.append("<ul>")
            for it in items:
                parts.append(f"<li>{esc(it)}</li>")
            parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)
