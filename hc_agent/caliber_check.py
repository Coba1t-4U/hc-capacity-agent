"""caliber_check.py — 给技能运行时用的口径绑定入口（薄封装）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def verify(run_dir: str = "") -> Dict[str, Any]:
    """校验某次运行的口径绑定。只读。"""
    from hc_agent import caliber as cal

    if not run_dir:
        return {"ok": False, "available": False,
                "reason": "没有给运行目录", "diffs": [], "digest": ""}
    root = Path(str(run_dir))
    if not root.is_dir():
        return {"ok": False, "available": False,
                "reason": f"运行目录不存在：{root}", "diffs": [], "digest": ""}
    return cal.check(root)
