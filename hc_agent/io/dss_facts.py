"""dss_facts.py — 把一个 OpenDSS 算例"看懂"：元件清单、参数、拓扑、假设分析。

问答要能回答"这个算例里有什么、光伏接在哪、线路多长、加 300 kW 会怎样"，
就必须先把 .dss 文件解析成结构化事实。

两种数据来源分工明确：
  * **静态元件**（线路/配变/负荷/电源参数）→ 直接解析 .dss 文本
  * **计算结果**（电压/负载率/网损）→ 交给引擎算（`dss_case.analyze`）
不混用：静态解析不猜电气结果，引擎结果也不反推元件参数。
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 解析时把 "Redirect xxx.dss" 展开，保持元件顺序
_REDIRECT = re.compile(r"(?im)^\s*redirect\s+(\S+)")


def _expand(text: str, root: Path, depth: int = 0) -> str:
    """把 Redirect 展开成完整文本（算例常拆成多个 .dss，不展开会漏元件）。"""
    if depth > 6:
        return text
    out: List[str] = []

    def repl(m):
        name = m.group(1).strip().strip("'\"")
        f = root / name
        try:
            sub = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return m.group(0)
        return _expand(sub, root, depth + 1)

    for line in text.splitlines():
        m = _REDIRECT.match(line)
        if m:
            out.append(repl(m))
        else:
            out.append(line)
    return "\n".join(out)


def _split_cmds(text: str) -> List[str]:
    """按行切命令，并把以 ~ 开头的续行接到上一条。"""
    cmds: List[str] = []
    for raw in text.splitlines():
        line = raw.split("!", 1)[0].strip()
        if not line:
            continue
        if line.startswith("~"):
            if cmds:
                cmds[-1] += " " + line.lstrip("~ ").strip()
            continue
        cmds.append(line)
    return cmds


def _kv(s: str) -> Dict[str, str]:
    """把 "bus1=x bus2=y length=1 units=km" 解析成字典（小写键）。"""
    d: Dict[str, str] = {}
    for tok in re.finditer(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|\S+)", s):
        d[tok.group(1).lower()] = tok.group(2).strip("'\"")
    return d


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).split()[0])
    except Exception:
        return None


def case_facts(path) -> Dict[str, Any]:
    """解析算例，返回结构化清单 + 关键统计。

    返回的 `ok=False` 表示没找到 Master.dss 或解析失败，`error` 说明原因。
    """
    from hc_agent.io import dss_case

    out: Dict[str, Any] = {"ok": False, "error": "", "name": "", "dir": "",
                           "source_kv": None, "buses": [], "lines": [],
                           "transformers": [], "loads": [], "generators": [],
                           "linecodes": [], "counts": {}, "notes": []}
    try:
        master = dss_case.resolve_master(str(path))
    except Exception as exc:
        out["error"] = str(exc)
        return out
    master = Path(master)
    root = master.parent
    try:
        text = _expand(master.read_text(encoding="utf-8", errors="replace"),
                       root)
    except Exception as exc:
        out["error"] = f"读不了 {master}：{exc}"
        return out

    cmds = _split_cmds(text)
    for cmd in cmds:
        low = cmd.lower()
        body = cmd.split(None, 1)[1] if " " in cmd else ""
        if low.startswith("new linecode"):
            kv = _kv(body)
            name = body.split()[0].split(".", 1)[-1] if body else "?"
            out["linecodes"].append({
                "name": name, "r1": _num(kv.get("r1")), "x1": _num(kv.get("x1")),
                "normamps": _num(kv.get("normamps"))})
        elif low.startswith("new line."):
            kv = _kv(body)
            out["lines"].append({
                "name": body.split()[0].split(".", 1)[-1],
                "from": kv.get("bus1"), "to": kv.get("bus2"),
                "length_km": _num(kv.get("length")),
                "units": kv.get("units", "km"),
                "linecode": kv.get("linecode"), "phases": kv.get("phases"),
            })
        elif low.startswith("new transformer."):
            kv = _kv(body)
            out["transformers"].append({
                "name": body.split()[0].split(".", 1)[-1],
                "kva": _num(kv.get("kva")), "kv": _num(kv.get("kv")),
                "bus": kv.get("bus"), "conn": kv.get("conn"),
                "windings": _num(kv.get("windings"))})
        elif low.startswith("new load."):
            kv = _kv(body)
            out["loads"].append({
                "name": body.split()[0].split(".", 1)[-1],
                "bus": kv.get("bus1"), "kw": _num(kv.get("kw")),
                "kvar": _num(kv.get("kvar")), "pf": _num(kv.get("pf"))})
        elif low.startswith("new generator.") or low.startswith("new pvsystem."):
            kv = _kv(body)
            out["generators"].append({
                "name": body.split()[0].split(".", 1)[-1],
                "bus": kv.get("bus1") or kv.get("bus"),
                "kw": _num(kv.get("kw")), "kva": _num(kv.get("kva")),
                "pf": _num(kv.get("pf"))})
        elif low.startswith("new circuit."):
            kv = _kv(body)
            out["source_kv"] = _num(kv.get("basekv"))
            out["name"] = (body.split()[0].split(".", 1)[-1]
                           if body else root.name)
    if not out["name"]:
        out["name"] = root.name
    out["dir"] = str(root)
    out["master"] = str(master)
    out["counts"] = {"lines": len(out["lines"]),
                     "transformers": len(out["transformers"]),
                     "loads": len(out["loads"]),
                     "generators": len(out["generators"]),
                     "linecodes": len(out["linecodes"])}
    # 总负荷/总光伏（按元件参数，不是引擎结果）
    out["load_kw_declared"] = sum(x["kw"] or 0 for x in out["loads"])
    out["gen_kw_declared"] = sum(x["kw"] or 0 for x in out["generators"])
    out["ok"] = bool(out["lines"] or out["transformers"] or out["loads"])
    if not out["ok"]:
        out["error"] = "算例里没解析出任何元件（可能是空文件或格式不对）"
    return out


def format_inventory(f: Dict[str, Any]) -> str:
    """把清单排成可读文本（问答直接用）。"""
    if not f.get("ok"):
        return f"没能解析这个算例：{f.get('error') or '未知原因'}"
    c = f["counts"]
    lines = [f"**算例**：{f['name']}",
             f"电压等级：{f['source_kv'] or '未声明'} kV　"
             f"元件：线路 {c['lines']}、配变 {c['transformers']}、"
             f"负荷 {c['loads']}、电源 {c['generators']}", ""]
    if f["transformers"]:
        lines.append("**配变**")
        for t in f["transformers"]:
            lines.append(f"· {t['name']}：{t['kva']} kVA，"
                         f"{t['kv']} kV（{t['conn'] or '—'}）")
    if f["generators"]:
        lines.append("")
        lines.append("**分布式电源**")
        for g in f["generators"]:
            lines.append(f"· {g['name']}：{g['kw']} kW @ {g['bus']}"
                         f"（pf={g['pf'] if g['pf'] is not None else '1.0'}）")
    if f["loads"]:
        lines.append("")
        lines.append("**负荷**")
        for x in f["loads"]:
            lines.append(f"· {x['name']}：{x['kw']} kW @ {x['bus']}"
                         + (f"，pf={x['pf']}" if x["pf"] else ""))
    if f["lines"]:
        lines.append("")
        lines.append("**线路**")
        for x in f["lines"]:
            ln = (f"{x['length_km']} {x['units']}"
                  if x["length_km"] is not None else "长度未声明")
            lines.append(f"· {x['name']}：{x['from']} → {x['to']}，{ln}，"
                         f"型号 {x['linecode'] or '—'}")
    lines.append("")
    lines.append(f"按元件参数声明：负荷合计 {f['load_kw_declared']:.1f} kW，"
                 f"电源合计 {f['gen_kw_declared']:.1f} kW"
                 f"（实际出力以引擎求解为准）")
    return "\n".join(lines)


def what_if_pv(path, target_kw: float) -> Dict[str, Any]:
    """假设分析：把算例里**全部**光伏改到 target_kw 再算一次。

    实现方式：复制算例到临时目录 → 按原总容量比例缩放每个 Generator 的 kw
    → 重算 → 给出前后对比。**不改用户的原算例**。

    这是"能自己回答"的关键能力：用户问"加到 300 kW 会怎样"，
    不是靠推算，而是真的重跑一遍引擎。
    """
    from hc_agent.io import dss_case

    f = case_facts(path)
    if not f.get("ok"):
        return {"ok": False, "error": f.get("error") or "算例解析失败"}
    if not f["generators"]:
        return {"ok": False, "error": "这个算例里没有分布式电源元件，无法做光伏增减假设"}
    src_dir = Path(f["dir"])
    tmp = Path(tempfile.mkdtemp(prefix="hc-whatif-"))
    try:
        dst = tmp / src_dir.name
        shutil.copytree(src_dir, dst)
        base_total = f["gen_kw_declared"] or 0.0
        gens = f["generators"]
        # 按比例缩放；总量为 0 时平均分配
        if base_total > 0:
            share = [((g["kw"] or 0.0) / base_total) for g in gens]
        else:
            share = [1.0 / len(gens)] * len(gens)
        # 改写每个 Master 里出现过的 generator 行
        master = Path(dst / Path(f["master"]).name)
        for fpath in [p for p in dst.rglob("*.dss")]:
            txt = fpath.read_text(encoding="utf-8", errors="replace")
            new = txt
            for g, sh in zip(gens, share):
                kw_new = round(target_kw * sh, 6)
                pat = re.compile(
                    rf"(?im)^(\s*New\s+(?:Generator|PVSystem)\.{re.escape(g['name'])}\b[^\n]*?)"
                    rf"\bkw\s*=\s*[\d.]+", re.I)
                def _rep(m, kw_new=kw_new):
                    return f"{m.group(1)} kw={kw_new}"
                new = pat.sub(_rep, new)
            if new != txt:
                fpath.write_text(new, encoding="utf-8")
        before = dss_case.analyze(src_dir)
        after = dss_case.analyze(dst)
        bh, bl = before.best_voltage(), before.worst_voltage()
        ah, al = after.best_voltage(), after.worst_voltage()
        return {
            "ok": True,
            "case": f["name"],
            "gen_before_kw": base_total,
            "gen_after_kw": target_kw,
            "converged": after.converged,
            "vmax_before": (bh.vmin_pu if bh else None),
            "vmax_after": (ah.vmin_pu if ah else None),
            "vmin_before": (bl.vmin_pu if bl else None),
            "vmin_after": (al.vmin_pu if al else None),
            "violations_before": len(before.voltage_violations()),
            "violations_after": len(after.voltage_violations()),
            "overload_before": len(before.overloaded()),
            "overload_after": len(after.overloaded()),
            "maxload_before": before.worst_branch().loading_pct if before.worst_branch() else None,
            "maxload_after": after.worst_branch().loading_pct if after.worst_branch() else None,
            "losses_before_kw": before.losses_kw,
            "losses_after_kw": after.losses_kw,
            "reverse_before": before.source_kw > 0,
            "reverse_after": after.source_kw > 0,
            "temp_dir": str(tmp),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if not (tmp / src_dir.name).exists():
            shutil.rmtree(tmp, ignore_errors=True)
