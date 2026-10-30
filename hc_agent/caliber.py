"""caliber.py — 口径绑定凭证（Caliber Binding）。

## 为什么需要它

一次承载力结论由四样东西决定：

```
B = { F, v_calc, Θ, γ }
      │    │       │    └─ γ 参数：同时率、裕度、搜索精度、步长
      │    │       └────── Θ 口径：规则包版本、判据定义
      │    └────────────── v_calc 计算版本：引擎 + 适配层 + Python
      └─────────────────── F 数据指纹：输入包的 sha256
```

**任何一样变了，原来的结论就不该继续被引用。**

典型事故：规则包从"电压 ±7%"改成"±10%"，用户还拿着旧报告对外发布 ——
数字没错，但已经不代表现在的口径了。文档要求"数据版本变更 → 重算指纹
→ 原凭证自动失效 → 拒绝继续使用"，本模块就是实现这个。

## 设计要点

1. **凭证是可计算出来的，不是存下来的记录**：拿着 `run_dir` 现算一遍，
   与运行时的记录比对。这样"改了什么"能定位到**具体哪一项**。
2. **失效不抛异常，而是返回结构化差异**：因为"失效"是要展示给用户的
   正常状态（告诉他是数据变了还是规则变了），不是程序错误。
3. **凭证要进证据链**：`digest()` 给出短哈希，可写进报告与审计链。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 参与凭证计算的四个分量
COMPONENTS = ("data_fingerprint", "calc_version", "caliber", "params")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


@dataclass
class Binding:
    """一份口径绑定凭证。"""

    data_fingerprint: str = ""
    calc_version: Dict[str, Any] = field(default_factory=dict)
    caliber: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    #: 可选的说明，便于人看
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_fingerprint": self.data_fingerprint,
            "calc_version": dict(self.calc_version),
            "caliber": dict(self.caliber),
            "params": dict(self.params),
            "note": self.note,
        }

    def digest(self) -> str:
        """四元组合成的短哈希（可写进报告/审计链）。"""
        return _sha(_canon(self.to_dict()))[:32]

    # ---- 比对
    def diff(self, other: "Binding") -> List[Dict[str, Any]]:
        """逐分量比对，返回差异列表（空表示完全一致）。"""
        out: List[Dict[str, Any]] = []
        for name in COMPONENTS:
            a = getattr(self, name)
            b = getattr(other, name)
            if _canon(a) != _canon(b):
                out.append({
                    "component": name,
                    "label": _LABELS.get(name, name),
                    "before": a,
                    "after": b,
                })
        return out

    def valid_against(self, other: "Binding") -> bool:
        """本凭证是否仍然适用于 `other` 描述的环境。"""
        return not self.diff(other)


_LABELS = {
    "data_fingerprint": "输入数据指纹",
    "calc_version": "计算版本（引擎/适配层/Python）",
    "caliber": "口径（规则包与判据）",
    "params": "参数（同时率/裕度/搜索设置）",
}


# ---------------------------------------------------------------- 从产物重建
def _read_json(p: Path) -> Dict[str, Any]:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def from_run(run_dir) -> Optional[Binding]:
    """从一次运行的产物里**重建**凭证。

    数据量都来自产物本身（manifest / run_summary / rules.yaml），
    没有产物返回 None。
    """
    root = Path(str(run_dir))
    man = _read_json(root / "manifest.json")
    summ = _read_json(root / "results" / "run_summary.json")
    out = _read_json(root / "run_outcome.json")
    if not (man or summ):
        return None

    data_fp = str(man.get("input_digest")
                  or summ.get("input_digest") or "")

    eng = (man.get("engine_fingerprint")
           or summ.get("engine_fingerprint") or {})
    calc = {
        "engine_id": eng.get("engine_id", ""),
        "engine_version": eng.get("version") or eng.get("engine_version", ""),
        "interface": eng.get("interface", ""),
        "python_version": eng.get("python_version", ""),
        "agent_version": man.get("agent_version", ""),
    }

    caliber = {
        "rule_package": (summ.get("rule_package")
                         or man.get("rule_version") or ""),
        "topology_version": man.get("topology_version", ""),
        "skill_version": man.get("skill_version", {}),
        "snapshot_id": (summ.get("snapshot_id")
                        or man.get("snapshot_id") or ""),
    }

    # 参数：优先从规则包实际内容算，拿不到就用 run_outcome 的 management
    mg = (out.get("management") or summ.get("management") or {})
    params: Dict[str, Any] = {}
    for k in ("coincidence", "margin", "formula_id"):
        if mg.get(k) is not None:
            params[k] = mg[k]
    rules = root / "config" / "rules.yaml"
    if rules.is_file():
        try:
            params["rules_yaml_sha"] = _sha(rules.read_text(encoding="utf-8"))
        except Exception:
            pass
    search = (summ.get("search_method") or "")
    if search:
        params["search_method"] = search
    if summ.get("slice_count") is not None:
        params["slice_count"] = summ["slice_count"]

    return Binding(data_fingerprint=data_fp, calc_version=calc,
                   caliber=caliber, params=params)


def current_binding() -> Binding:
    """当前环境（规则包 + 引擎）的凭证，用于与历史产物比对。"""
    from hc_agent import __version__ as agent_ver
    from hc_agent.runtime_env import engine_fingerprint

    eng = {}
    try:
        eng = engine_fingerprint() or {}
    except Exception:
        eng = {}
    calc = {
        "engine_id": eng.get("engine_id", ""),
        "engine_version": eng.get("version") or eng.get("engine_version", ""),
        "interface": eng.get("interface", ""),
        "python_version": eng.get("python_version", ""),
        "agent_version": agent_ver,
    }
    # 当前规则包：取最近一次 APPROVE 运行的 config/rules.yaml
    caliber: Dict[str, Any] = {}
    params: Dict[str, Any] = {}
    try:
        from hc_agent.desktop import runs as runs_mod
        rd = runs_mod.resolve_run_dir("FD002")
        if rd is not None:
            man = _read_json(Path(rd) / "manifest.json")
            summ = _read_json(Path(rd) / "results" / "run_summary.json")
            out = _read_json(Path(rd) / "run_outcome.json")
            caliber = {
                "rule_package": (summ.get("rule_package")
                                 or man.get("rule_version") or ""),
                "topology_version": man.get("topology_version", ""),
                "skill_version": man.get("skill_version", {}),
                "snapshot_id": (summ.get("snapshot_id")
                                or man.get("snapshot_id") or ""),
            }
            mg = (out.get("management") or summ.get("management") or {})
            for k in ("coincidence", "margin", "formula_id"):
                if mg.get(k) is not None:
                    params[k] = mg[k]
            rules = Path(rd) / "config" / "rules.yaml"
            if rules.is_file():
                params["rules_yaml_sha"] = _sha(
                    rules.read_text(encoding="utf-8"))
    except Exception:
        pass
    return Binding(calc_version=calc, caliber=caliber, params=params,
                   note="当前环境")


# ---------------------------------------------------------------- 对外结论
def check(run_dir) -> Dict[str, Any]:
    """校验一次运行的口径绑定是否仍然成立。

    返回：
      `ok`        —— 是否仍可用
      `reason`    —— 一句话结论
      `diffs`     —— 逐分量差异（定位到底哪一项变了）
      `digest`    —— 该凭证的短哈希
    """
    b = from_run(run_dir)
    if b is None:
        return {"ok": False, "available": False,
                "reason": "这次运行没有可用的产物，无法重建口径绑定",
                "diffs": [], "digest": ""}
    cur = current_binding()
    # 数据指纹只在能取到当前值时才有意义；当前环境没有输入包指纹，
    # 所以只比对"计算版本/口径/参数"三项 —— 数据是否变由指纹本身保证。
    diffs = []
    for name in ("calc_version", "caliber", "params"):
        a = getattr(b, name)
        c = getattr(cur, name)
        # 当前值为空的键不参与比对（取不到 ≠ 变了）
        keys = [k for k in set(a) | set(c) if c.get(k) not in (None, "", {})]
        sub_a = {k: a.get(k) for k in keys}
        sub_c = {k: c.get(k) for k in keys}
        if _canon(sub_a) != _canon(sub_c):
            diffs.append({"component": name,
                          "label": _LABELS.get(name, name),
                          "before": sub_a, "after": sub_c})
    if not diffs:
        return {"ok": True, "available": True,
                "reason": "口径绑定仍然成立（计算版本、口径、参数均未变化）",
                "diffs": [], "digest": b.digest()}
    names = "、".join(d["label"] for d in diffs)
    return {"ok": False, "available": True,
            "reason": f"口径绑定已失效：{names}发生了变化，"
                      f"原结论不应继续引用",
            "diffs": diffs, "digest": b.digest()}


def format_check(res: Dict[str, Any]) -> str:
    """排成可读文本（问答直接用）。"""
    lines = []
    if not res.get("available"):
        return res.get("reason") or "无法校验口径绑定"
    dg = res.get("digest") or ""
    if res.get("ok"):
        lines.append("✅ **口径绑定有效**")
        lines.append("")
        lines.append("计算版本、口径（规则包）、参数（同时率/裕度）均与当前一致，"
                     "这次运行的结论可以继续引用。")
    else:
        lines.append("❌ **口径绑定已失效**")
        lines.append("")
        lines.append(res.get("reason") or "")
        for d in res.get("diffs") or []:
            lines.append("")
            lines.append(f"· **{d['label']}**")
            before, after = d.get("before") or {}, d.get("after") or {}
            for k in sorted(set(before) | set(after)):
                b = before.get(k)
                a = after.get(k)
                if _canon(b) == _canon(a):
                    continue
                lines.append(f"  - `{k}`：{b} → {a}")
    if dg:
        lines.append("")
        lines.append(f"凭证指纹：`{dg}`")
    return "\n".join(lines)
