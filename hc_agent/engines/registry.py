"""registry.py — 计算引擎注册表。

**这是"适配引擎数"的唯一事实来源。** 验收矩阵不再从 manifest 猜一个
`engine_id`，而是问这里：有几个引擎真的能导入、真的能算，各自的口径是什么。

注册表的每条记录都必须写清 `scope`（适用范围）与 `role`（角色）：

  * OpenDSS：三相不平衡潮流，**主算**，容量判定与约束结论只认它；
  * pandapower：正序平衡潮流，**交叉校核**，只用来核对量级，不参与判定。

把两个引擎都算进去，前提是它们**真的能算**（`available()` 会实际导入并建网），
不是把包装名写进清单就算数。
"""

from __future__ import annotations

from typing import Any, Dict, List

ENGINE_OPENDSS = "opendss"
ENGINE_PANDAPOWER = "pandapower"


def _opendss_entry() -> Dict[str, Any]:
    from hc_agent import runtime_env

    lib = runtime_env.ensure_engine_on_path()
    entry: Dict[str, Any] = {
        "engine_id": ENGINE_OPENDSS,
        "engine_label": "OpenDSS",
        "scope": "three_phase_unbalanced",
        "scope_label": "三相不平衡潮流",
        "role": "primary",
        "role_label": "主算（容量与约束判定的唯一来源）",
        "pylib_path": str(lib) if lib else None,
        "available": False,
    }
    if lib is None:
        entry["error"] = "OpenDSSDirect.py 未找到"
        return entry
    try:
        import opendssdirect as dss
        entry["available"] = True
        entry["version"] = getattr(dss, "__version__", "unknown")
        try:
            entry["engine_version"] = dss.Basic.Version()
        except Exception:
            pass
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
    return entry


def _pandapower_entry() -> Dict[str, Any]:
    from hc_agent.engines import pandapower_adapter as PA

    info = PA.available()
    info.setdefault("role", "crosscheck")
    info["role_label"] = "交叉校核（正序平衡，不参与容量判定）"
    return info


def adapters() -> List[Dict[str, Any]]:
    """返回全部已注册引擎（含不可用的，便于如实展示缺口）。"""
    return [_opendss_entry(), _pandapower_entry()]


def available_engines() -> List[Dict[str, Any]]:
    return [e for e in adapters() if e.get("available")]


def count_available() -> int:
    return len(available_engines())


def summary() -> Dict[str, Any]:
    """给验收矩阵与界面用的一句话摘要。"""
    eng = available_engines()
    names = "、".join(f"{e['engine_label']}（{e['scope_label']}）" for e in eng)
    return {
        "count": len(eng),
        "engine_ids": [e["engine_id"] for e in eng],
        "names": names or "—",
        "primary": next((e["engine_label"] for e in eng
                         if e.get("role") == "primary"), None),
        "crosscheck": next((e["engine_label"] for e in eng
                            if e.get("role") == "crosscheck"), None),
        "entries": eng,
    }
