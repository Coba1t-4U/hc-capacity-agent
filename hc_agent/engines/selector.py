"""selector.py — 计算引擎选择（界面下拉与问答行为的分发点）。

三种模式，语义必须清楚，不能让人以为换了引擎还得到同样的东西：

    opendss      三相不平衡潮流，主算。容量搜索、约束判定、红黄绿分级只认它。（默认）
    both         OpenDSS 主算 + pandapower 交叉校核，答案后面附一段校核结果。
    pandapower   只跑正序平衡潮流，**不产出容量**。用来快速核对量级。

选择结果持久化到数据目录的 ``engine_mode.json``，下次启动沿用；
写盘失败只影响"记住上次选择"，不影响本次使用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MODE_OPENDSS = "opendss"
MODE_BOTH = "both"
MODE_PANDAPOWER = "pandapower"
DEFAULT_MODE = MODE_OPENDSS

MODES: Tuple[Dict[str, str], ...] = (
    {"id": MODE_OPENDSS, "label": "OpenDSS",
     "hint": "三相不平衡潮流 · 主算（容量与约束只认它）"},
    {"id": MODE_BOTH, "label": "双引擎",
     "hint": "OpenDSS 主算 + pandapower 交叉校核"},
    {"id": MODE_PANDAPOWER, "label": "pandapower",
     "hint": "仅正序平衡潮流 · 不产出容量"},
)
_VALID = {m["id"] for m in MODES}


def labels() -> List[str]:
    return [m["label"] for m in MODES]


def label_of(mode: str) -> str:
    return next((m["label"] for m in MODES if m["id"] == mode), MODES[0]["label"])


def hint_of(mode: str) -> str:
    return next((m["hint"] for m in MODES if m["id"] == mode), MODES[0]["hint"])


def _store() -> Optional[Path]:
    try:
        from hc_agent import paths
        root = paths.data_root()
        root.mkdir(parents=True, exist_ok=True)
        return root / "engine_mode.json"
    except Exception:
        return None


def get_mode() -> str:
    p = _store()
    if p is None or not p.is_file():
        return DEFAULT_MODE
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        mode = str(raw.get("mode") or "")
        return mode if mode in _VALID else DEFAULT_MODE
    except Exception:
        return DEFAULT_MODE


def set_mode(mode: str) -> str:
    mode = mode if mode in _VALID else DEFAULT_MODE
    p = _store()
    if p is not None:
        try:
            p.write_text(json.dumps({"mode": mode}, ensure_ascii=False,
                                    indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    return mode


# ---------------------------------------------------------------- 行为分发
def _run_dir_from_source(source: str) -> Optional[Path]:
    """从答案的 source（可能是运行目录或其中某个文件）回溯到运行目录。"""
    if not source:
        return None
    try:
        p = Path(str(source))
    except Exception:
        return None
    for cand in (p, p.parent, p.parent.parent, p.parent.parent.parent):
        try:
            if (cand / "results" / "run_summary.json").is_file():
                return cand
            if (cand / "_stage").is_dir() and (cand / "results").is_dir():
                return cand
        except Exception:
            continue
    return None


def _latest_run_dir() -> Optional[Path]:
    try:
        from hc_agent import paths
        root = paths.runs_root()
        best = None
        for f in root.rglob("results/run_summary.json"):
            m = f.stat().st_mtime
            if best is None or m > best[0]:
                best = (m, f.parent.parent)
        return best[1] if best else None
    except Exception:
        return None


def apply_mode(ans: Dict[str, Any]) -> Dict[str, Any]:
    """按当前选择改造一条问答结果（就地返回新 dict，不改入参）。"""
    mode = get_mode()
    out = dict(ans)
    if mode == MODE_OPENDSS:
        return out

    from hc_agent.engines import pandapower_adapter as PA

    run = _run_dir_from_source(str(out.get("source") or "")) or _latest_run_dir()
    text = str(out.get("text") or "")

    if mode == MODE_PANDAPOWER:
        head = ("【pandapower 模式】本模式只跑**正序平衡**潮流，用于核对量级；"
                "**不产出开放容量**，也不能替代 OpenDSS 的相别与不平衡结论。\n"
                "要容量结论请把引擎切回「OpenDSS」或「双引擎」。\n")
        if run is None:
            out["text"] = head + "\n还没有可校核的运行（需要先跑一次评估）。"
            out["engine"] = "rule"
            return out
        rep = PA.crosscheck(run)
        out["text"] = head + "\n" + PA.format_crosscheck(rep)
        out["source"] = str(run)
        out["engine"] = "rule"
        return out

    # MODE_BOTH：答案不动，只在后面补一段交叉校核
    if run is None:
        out["text"] = text + ("\n\n——\n交叉校核：本次没有产生可取数的运行，"
                              "已跳过（引擎选择「双引擎」）。")
        return out
    try:
        rep = PA.crosscheck(run)
        out["text"] = text + "\n\n——\n" + PA.format_crosscheck(rep)
    except Exception as exc:
        out["text"] = text + (f"\n\n——\n交叉校核失败：{type(exc).__name__}: "
                              f"{exc}")
    return out
