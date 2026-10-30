"""acceptance_check.py — 给技能运行时用的验收矩阵入口（薄封装）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def evaluate(run_dir: str = "") -> Dict[str, Any]:
    """按九条指标评估一次运行。只读。"""
    from hc_agent import acceptance as A

    if not run_dir:
        return {"ok": False, "reason": "没有给运行目录", "items": []}
    root = Path(str(run_dir))
    if not root.is_dir():
        return {"ok": False, "reason": f"运行目录不存在：{root}", "items": []}
    res = A.single_run(root)
    return {"ok": True, "run_id": res.get("run_id"),
            "items": res.get("items"), "summary": res.get("summary"),
            "gate": res.get("gate")}
