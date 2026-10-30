"""audit_chain_summary.py — 给技能运行时用的审计链入口（薄封装）。

`skill.json` 里的 handler 必须写成 `模块:函数`，而被调用的函数要能接收
契约声明的 `requires` 字段。这里把 `AuditChain` 的用法包成单参数函数，
避免技能层直接依赖类结构。
"""

from __future__ import annotations

from typing import Any, Dict


def verify(run_dir: str = "") -> Dict[str, Any]:
    """校验某次运行的审计链，返回摘要。只读。"""
    from hc_agent.audit_chain import chain_for_run
    from pathlib import Path

    if not run_dir:
        return {"ok": False, "reason": "没有给运行目录", "count": 0}
    root = Path(str(run_dir))
    if not root.is_dir():
        return {"ok": False, "reason": f"运行目录不存在：{root}", "count": 0}
    chain = chain_for_run(root)
    if not chain.path.is_file():
        return {"ok": False, "count": 0, "broken_at": None,
                "reason": "这次运行还没有审计链（可用 seal 补建）",
                "head_hash": "", "path": str(chain.path)}
    return chain.summary()


def seal(run_dir: str = "") -> Dict[str, Any]:
    """给老运行补建审计链。"""
    from hc_agent.audit_chain import seal_run

    if not run_dir:
        return {"ok": False, "reason": "没有给运行目录", "count": 0}
    return seal_run(run_dir)
