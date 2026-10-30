"""llm.py — DeepSeek 接入层（把大模型接到真实数据上）。

## 核心安全设计：**大模型不产生数字**

本领域的数字一旦错一个，结论就可能从"可接 300 kW"变成"可接 500 kW"。
所以这里的做法是 **工具调用（function calling）**：

    用户问题 ──▶ DeepSeek ──(决定查什么)──▶ 本地工具 ──(读真实产物)──▶ 数据
                    ▲                                              │
                    └────────── 只允许复述工具返回值 ◀──────────────┘

模型负责**理解**与**措辞**，数字一律由工具返回、模型只能复述。
系统提示明确禁止它自己算、自己估、自己补。

## 降级

没有配置 Key、网络不通、模型报错 —— 一律**自动退回本地规则引擎**，
而不是让界面报错或给出半截答案。用户永远能得到一个可用的回答。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
TIMEOUT_S = 60

ENV_KEY = "DEEPSEEK_API_KEY"
ENV_BASE = "DEEPSEEK_BASE_URL"
ENV_MODEL = "DEEPSEEK_MODEL"


# ---------------------------------------------------------------- 配置
@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = DEFAULT_BASE
    model: str = DEFAULT_MODEL

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """优先环境变量，其次用户目录里保存过的配置。

        顺序有意：环境变量便于临时切换/CI，保存的配置让普通用户不必设环境变量。
        """
        saved = load_saved()
        return cls(
            api_key=(os.environ.get(ENV_KEY) or saved.get("api_key")
                     or "").strip(),
            base_url=((os.environ.get(ENV_BASE) or saved.get("base_url")
                       or DEFAULT_BASE)).rstrip("/"),
            model=(os.environ.get(ENV_MODEL) or saved.get("model")
                   or DEFAULT_MODEL),
        )

    def masked(self) -> str:
        k = self.api_key
        return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else ("已配置" if k else "未配置")

    def mask_str(self) -> str:
        """界面用：未配置时直接说"未配置"，而不是空白。"""
        return self.masked()


def config_candidates() -> List[Path]:
    """Key 配置的**所有**可能位置，按优先级排列。

    为什么不是单一路径：`data_root_safe()` 在数据目录不可写时会**降级**
    到别处（沙箱、只读安装目录、权限受限的用户目录都会触发）。
    如果只认降级后的路径，就会出现"打包版把 Key 写在 AppData，
    但读的时候跑去看降级目录，于是认为没配置"。
    实测就踩过：源码版好了、打包版仍显示本地规则引擎。

    顺序：主数据目录 → 降级目录 → 用户家目录兜底。
    """
    out: List[Path] = []
    try:
        from hc_agent import paths
        try:
            out.append(paths.data_root() / "llm.json")
        except Exception:
            pass
        try:
            safe = paths.data_root_safe() / "llm.json"
            if safe not in out:
                out.append(safe)
        except Exception:
            pass
        try:
            home = Path.home() / ".hc-agent" / "llm.json"
            if home not in out:
                out.append(home)
        except Exception:
            pass
    except Exception:
        pass
    return out


def config_path():
    """**写**配置时用的位置：优先主数据目录，不可写才降级。"""
    for c in config_candidates():
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            probe = c.parent / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return c
        except Exception:
            continue
    from hc_agent import paths
    return paths.data_root_safe() / "llm.json"


def load_saved() -> Dict[str, str]:
    """按优先级读配置；任一位置存在有效 Key 就用它。"""
    import json as _j
    for c in config_candidates():
        try:
            if c.is_file():
                data = _j.loads(c.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict) and str(data.get("api_key") or "").strip():
                    return data
        except Exception:
            continue
    return {}


def save_config(api_key: str, *, base_url: str = "",
                model: str = "") -> Dict[str, Any]:
    """保存配置。返回写入路径，便于界面提示用户。

    **Key 明文存本地**（用户目录），这是桌面单机应用的常规做法；
    文件权限依赖系统账户保护，不做额外加密。界面上会如实说明。
    """
    import json as _j
    from hc_agent import paths
    paths.ensure_writable(paths.data_root_safe())
    p = config_path()
    data = {"api_key": (api_key or "").strip(),
            "base_url": (base_url or DEFAULT_BASE).rstrip("/"),
            "model": (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL}
    payload = _j.dumps(data, ensure_ascii=False, indent=2)
    # 双写：把 Key 同步到其它候选位置，让源码版与打包版共用同一份配置。
    # 任一位置写失败都不影响主位置，只记下来供界面提示。
    written: List[str] = []
    for c in config_candidates():
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            c.write_text(payload, encoding="utf-8")
            written.append(str(c))
        except Exception:
            continue
    if not written:
        p.write_text(payload, encoding="utf-8")
        written.append(str(p))
    # 立即生效（不必重启）
    os.environ[ENV_KEY] = data["api_key"]
    os.environ[ENV_BASE] = data["base_url"]
    os.environ[ENV_MODEL] = data["model"]
    return {"ok": True, "path": str(p), "paths": written,
            "masked": _mask(data["api_key"])}


def _mask(k: str) -> str:
    k = k or ""
    return f"{k[:6]}…{k[-4:]}" if len(k) > 12 else ("已配置" if k else "未配置")


# ---------------------------------------------------------------- 工具定义
#: 暴露给模型的工具。**每个都只读**，且返回值就是事实本身。
TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_capacity",
            "description": "查询某次运行的开放容量（物理/管理）、红黄绿等级、主导限制因素。",
            "parameters": {
                "type": "object",
                "properties": {
                    "run": {"type": "string",
                            "description": "运行标识，如 FD002 / FD001 / APP001；留空表示最近一次"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_binding",
            "description": "查询为什么接不了更多：卡在哪个约束、哪个对象、实测值与限值。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_violations",
            "description": "列出所有不通过的约束及其统计。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_min_voltage",
            "description": "查询最低电压及其位置、限值、时刻。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_absorb",
            "description": "判断能否再接入指定容量；返回是否可行及余量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kw": {"type": "number", "description": "拟接入容量，单位 kW"},
                    "run": {"type": "string"},
                },
                "required": ["kw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_verification",
            "description": "查询独立复算是否通过、差异项数。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gate",
            "description": "查询发布门禁（独立复算/专业复核/审批）状态。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_runs",
            "description": "列出所有可查询的运行。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_opendss_case",
            "description": (
                "分析一个 OpenDSS 算例（.dss 文件或含 Master.dss 的目录）并生成计算报告。"
                "返回潮流结果（收敛、电压最值、过载、网损、反向潮流）与报告文件路径。"
                "注意：原生算例分析不产出开放容量。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "算例路径：.dss 文件或目录；留空表示用桌面上的默认算例",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_dss_case",
            "description": (
                "查看 OpenDSS 算例的**内部构成**（不跑潮流）：元件清单"
                "（线路/配变/负荷/电源及其参数）、分布式电源接在哪几处。"
                "用于回答「这个算例里有什么」「线路多长」「配变多大」"
                "「光伏接在哪」这类问题。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "case": {
                        "type": "string",
                        "description": "算例名或路径；留空表示桌面默认算例",
                    },
                    "what": {
                        "type": "string",
                        "enum": ["inventory", "pv"],
                        "description": "inventory=全部元件清单；pv=只看分布式电源",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "what_if_dss_case",
            "description": (
                "对 OpenDSS 算例做**增减假设**：把分布式电源改到指定容量后"
                "真的重跑一次潮流，返回改造前后的电压/越限/负载率/网损对比。"
                "用于回答「如果把光伏加到 300 kW 会怎样」。"
                "不会修改用户的原始算例。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "case": {
                        "type": "string",
                        "description": "算例名或路径；留空表示桌面默认算例",
                    },
                    "target_kw": {
                        "type": "number",
                        "description": "分布式电源总容量的目标值（kW）",
                    },
                },
                "required": ["target_kw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_limits",
            "description": "查询本次分析未覆盖的能力范围。",
            "parameters": {
                "type": "object",
                "properties": {"run": {"type": "string"}},
                "required": [],
            },
        },
    },
]

SYSTEM_PROMPT = """你是“配网承载力智能体Agent”，帮助调度/规划人员理解
配电网开放容量评估的结论。

**关于数字的硬性规则（必须遵守）**
1. 所有数值**只能来自工具返回值**。不得自己计算、推算、估算或补齐。
2. 工具没返回的数字，就说"这次产物里没有这项数据"，不要给近似值。
3. 工具返回 0 时如实说 0，不要解释成"约等于没有"。
4. 不确定就问工具，不要猜。

**回答风格**
- 中文，简洁，先说结论再给依据。
- 关键数字加粗，用 `对象名` 标注设备。
- 不要罗列工具调用的过程，直接给结论。

**边界**
- 你只解释已有的评估结果，不预测新算例、不做设备控制建议。
- 用户要求「算一下某个 OpenDSS 算例」时，调用 analyze_opendss_case；算完要如实说明这只是**原生算例分析、不产出开放容量**，并把报告路径告诉用户。
- 若问的算例不存在，如实说明并列出可查询的运行。
"""


# ---------------------------------------------------------------- 工具执行
class ToolRunner:
    """把工具名映射到本地事实查询。全部是只读操作。"""

    def __init__(self, facts) -> None:
        self.facts = facts

    def _run(self, name: str):
        r = self.facts.find(name) if name else self.facts.current
        return r

    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._dispatch(name, args or {})
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ---------------------------------------------------------- OpenDSS 算例
    def _resolve_dss_path(self, path: str) -> str:
        """解析算例路径。

        实际逻辑统一放在 `io.dss_report.resolve_case_path`，
        问答与界面共用一份 —— 这里只做转发，避免两处行为不一致。
        """
        try:
            from hc_agent.io.dss_report import resolve_case_path
            return resolve_case_path(path)
        except Exception:
            return ""

    def _search_bases(self):
        try:
            from hc_agent.io.dss_report import search_bases
            return search_bases()
        except Exception:
            return []

    def _analyze_dss(self, path: str) -> Dict[str, Any]:
        from hc_agent.io.dss_report import analyze_and_report
        r = analyze_and_report(path)
        if not r.get("ok") and r.get("hint"):
            return {"error": r.get("error") or "没找到算例",
                    "hint": r.get("hint"),
                    "searched": r.get("searched") or []}
        target = r.get("path") or ""
        if not r.get("ok"):
            return {"error": r.get("error") or "分析失败", "path": target}
        out = dict(r["summary"])
        out["report_path"] = r.get("report_path", "")
        if r.get("error"):
            out["report_error"] = r["error"]
        return out

    def _dispatch(self, name: str, a: Dict[str, Any]) -> Dict[str, Any]:
        run = str(a.get("run") or "")
        f = self._run(run)
        if name == "inspect_dss_case":
            try:
                from hc_agent.io import dss_facts
                from hc_agent.io.dss_report import resolve_case_path
                path = resolve_case_path(str(a.get("case") or ""))
                if not path:
                    return {"ok": False, "error": "没找到算例"}
                facts = dss_facts.case_facts(path)
                if not facts.get("ok"):
                    return {"ok": False, "error": facts.get("error")}
                out: Dict[str, Any] = {
                    "ok": True, "case": facts["name"], "path": path,
                    "counts": facts["counts"],
                    "lines": facts["lines"],
                    "transformers": facts["transformers"],
                    "loads": facts["loads"],
                    "generators": facts["generators"],
                    "load_total_kw": facts.get("load_kw_declared"),
                    "gen_total_kw": facts.get("gen_kw_declared"),
                    "text": dss_facts.format_inventory(facts),
                }
                if str(a.get("what") or "") == "pv":
                    out = {"ok": True, "case": facts["name"], "path": path,
                           "generators": facts["generators"],
                           "gen_total_kw": facts.get("gen_kw_declared"),
                           "load_total_kw": facts.get("load_kw_declared")}
                return out
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if name == "what_if_dss_case":
            try:
                from hc_agent.io import dss_facts
                from hc_agent.io.dss_report import resolve_case_path
                path = resolve_case_path(str(a.get("case") or ""))
                if not path:
                    return {"ok": False, "error": "没找到算例"}
                r = dss_facts.what_if_pv(path, float(a.get("target_kw")))
                r.pop("temp_dir", None)
                return r
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if name == "analyze_opendss_case":
            return self._analyze_dss(str(a.get("path") or ""))
        if name == "list_runs":
            return {"runs": [
                {"run": r.run_id,
                 "capacity_physical_kw": r.cap_physical,
                 "level": r.level,
                 "binding_constraint": r.binding}
                for r in self.facts.runs]}
        if f is None:
            return {"error": f"没有找到运行 {run!r}",
                    "available": [r.run_id for r in self.facts.runs]}
        if name == "get_capacity":
            if not f.ok:
                return {"error": f"{f.run_id} 没有容量结论"}
            return {"run": f.run_id,
                    "capacity_physical_kw": f.cap_physical,
                    "capacity_managed_kw": f.cap_managed,
                    "level": f.level,
                    "level_reason": f.summary.get("level_reason"),
                    "binding_constraint": f.binding,
                    "feasible_lower_kw": f.summary.get("feasible_lower_kw"),
                    "infeasible_upper_kw": f.summary.get("infeasible_upper_kw")}
        if name == "get_binding":
            bd = f.summary.get("binding_detail") or {}
            cs = f.summary.get("constraint_summary") or {}
            return {"run": f.run_id, "binding_constraint": f.binding,
                    "detail": bd,
                    "fail_count": cs.get("fail"),
                    "unknown_count": cs.get("unknown"),
                    "capacity_is_zero": (f.cap_physical == 0)}
        if name == "list_violations":
            cs = f.summary.get("constraint_summary") or {}
            bc = cs.get("binding_constraints") or []
            agg: Dict[str, Any] = {}
            for b in bc:
                cid = str(b.get("constraint_id"))
                g = agg.setdefault(cid, {"count": 0, "objects": set(),
                                         "worst_margin": 0.0})
                g["count"] += 1
                g["objects"].add(str(b.get("object_id")))
                g["worst_margin"] = min(float(g["worst_margin"]),
                                        float(b.get("margin") or 0))
            return {"run": f.run_id, "total": cs.get("total"),
                    "pass": cs.get("pass"), "fail": cs.get("fail"),
                    "unknown": cs.get("unknown"),
                    "by_constraint": [
                        {"constraint": k, "count": v["count"],
                         "objects": sorted(v["objects"])[:8],
                         "worst_margin": v["worst_margin"]}
                        for k, v in agg.items()]}
        if name == "get_min_voltage":
            rows = [x for x in f.constraint_rows
                    if str(x.get("constraint_id")) == "VOLTAGE_LIMIT"]
            if not rows:
                return {"error": f"{f.run_id} 没有电压约束明细"}
            worst = min(rows, key=lambda x: float(x.get("value") or 9e9))
            return {"run": f.run_id, "min_voltage_pu": float(worst.get("value")),
                    "object": worst.get("object_id"),
                    "lower_limit": worst.get("lower_limit"),
                    "upper_limit": worst.get("upper_limit"),
                    "scenario_id": worst.get("scenario_id"),
                    "timestamp": worst.get("timestamp")}
        if name == "check_absorb":
            kw = float(a.get("kw") or 0)
            if f.cap_physical is None:
                return {"error": f"{f.run_id} 没有容量结论"}
            return {"run": f.run_id, "requested_kw": kw,
                    "capacity_physical_kw": f.cap_physical,
                    "capacity_managed_kw": f.cap_managed,
                    "feasible": kw <= f.cap_physical,
                    "margin_kw": f.cap_physical - kw}
        if name == "get_verification":
            v = f.verification
            if not v:
                return {"error": f"{f.run_id} 没有复算产物"}
            return {"run": f.run_id, "pass": v.get("pass"),
                    "input_match": v.get("input_match"),
                    "mismatch_count": (v.get("comparison") or {}).get(
                        "mismatch_count")}
        if name == "get_gate":
            g = f.gate
            if not g:
                return {"error": f"{f.run_id} 没有门禁产物"}
            return {"run": f.run_id, "passed": g.get("passed"),
                    "gates": g.get("gates"),
                    "failed_gates": g.get("failed_gates")}
        if name == "get_limits":
            cal = f.summary.get("caliber") or {}
            return {"run": f.run_id,
                    "unsupported": cal.get("unsupported") or []}
        return {"error": f"未知工具 {name}"}


# ---------------------------------------------------------------- 调用
def _post(cfg: LLMConfig, path: str, payload: Dict[str, Any],
          *, retries: int = 2) -> Dict[str, Any]:
    """带重试的 POST。

    429（限流）与 5xx 是**瞬时**故障，重试通常就好了。实测遇到过：同一句问话
    第一次退回规则引擎、第二次正常 —— 不重试就会给用户一个"降级"的答案，
    而他并不知道本该走大模型。
    """
    import time as _t
    url = f"{cfg.base_url}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = exc
            # 401/402 是配置或余额问题，重试无意义
            if exc.code in (401, 403, 402):
                raise
            if exc.code == 429 or 500 <= exc.code < 600:
                if attempt < retries:
                    _t.sleep(0.8 * (attempt + 1))
                    continue
            raise
        except Exception as exc:
            last = exc
            if attempt < retries:
                _t.sleep(0.8 * (attempt + 1))
                continue
            raise
    if last is not None:
        raise last
    raise RuntimeError("请求未完成")


def available(cfg: Optional[LLMConfig] = None) -> bool:
    c = cfg or LLMConfig.from_env()
    return c.enabled


def ask(question: str, facts, *, cfg: Optional[LLMConfig] = None,
        max_rounds: int = 4, use_knowledge: bool = True) -> Dict[str, Any]:
    """用大模型回答问题，数字全部经工具取。

    返回结构与本工程其它回答一致：``{text, source, followups, engine}``。
    ``engine`` 标明这次是 deepseek 还是 fallback(rule)，便于用户知道答案来自哪。

    ``use_knowledge``：是否把知识库检索结果作为**补充上下文**喂给模型。
    知识用于解释"为什么这么判"，**数字仍然只能来自工具** —— 两件事严格分开，
    否则知识库里写死的旧数值会污染实时结论。
    """
    c = cfg or LLMConfig.from_env()
    if not c.enabled:
        return _fallback(question, facts, "未配置 DEEPSEEK_API_KEY")

    runner = ToolRunner(facts)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]
    # 知识库作为**方法说明**注入，并明确禁止用它替代实测值
    if use_knowledge:
        try:
            from hc_agent.knowledge import KnowledgeBase
            ctx = KnowledgeBase().as_context(question, top_k=3)
        except Exception:
            ctx = ""
        if ctx:
            messages.append({
                "role": "system",
                "content": ("以下是本工程的知识库节选，用于解释判定方法与口径。"
                            "**它不含本次算例的实测值**；任何具体数字仍必须"
                            "通过工具获取，不得引用知识库里的示例数字。\n\n"
                            + ctx),
            })
    messages.append({"role": "user", "content": question})
    used: List[str] = []
    reports: List[str] = []

    try:
        for _ in range(max_rounds):
            resp = _post(c, "/chat/completions", {
                "model": c.model,
                "messages": messages,
                "tools": TOOLS,
                "tool_choice": "auto",
                "temperature": 0.1,      # 事实型问答，压低随机性
            })
            msg = (resp.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            if not calls:
                text = (msg.get("content") or "").strip()
                if not text:
                    return _fallback(question, facts, "模型返回空内容")
                out = {"text": text,
                       "source": ("数据来源：" + "、".join(dict.fromkeys(used))
                                  if used else "数据来源：运行时产物"),
                       "followups": _followups(facts),
                       "engine": f"deepseek:{c.model}"}
                if reports:
                    out["report_path"] = reports[-1]
                    out["report_label"] = "打开计算报告"
                return out
            # 执行工具并把结果回灌
            messages.append({"role": "assistant",
                             "content": msg.get("content") or "",
                             "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                result = runner.call(name, args)
                used.append(name)
                # 工具可能产出计算报告（如 analyze_opendss_case）。
                # 必须记住路径并透传：否则界面拿不到"打开计算报告"按钮 ——
                # 规则引擎那条路会带，大模型这条漏了就会表现成
                # "同一句话换个引擎，一个有按钮一个没有"（实测踩过）。
                if isinstance(result, dict) and result.get("report_path"):
                    reports.append(str(result["report_path"]))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": json.dumps(result, ensure_ascii=False,
                                          default=str),
                })
        return _fallback(question, facts, "工具调用轮次过多")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return _fallback(question, facts,
                         f"DeepSeek 返回 HTTP {exc.code} {detail}")
    except Exception as exc:
        return _fallback(question, facts, f"{type(exc).__name__}: {exc}")


def _followups(facts) -> List[str]:
    return ["为什么是这个值？", "哪些约束没通过？", "复算通过了吗？",
            "还能接多少？"]


def _fallback(question: str, facts, reason: str) -> Dict[str, Any]:
    """大模型不可用时退回本地规则引擎 —— 用户永远能得到可用的回答。

    `note` 会显示在气泡里：必须让用户知道**这次答案不是大模型给的**，
    否则他会以为模型的表达能力就这么点。
    """
    from hc_agent.qa import Responder
    ans = Responder(facts).answer(question).to_dict()
    ans["engine"] = "rule"
    ans["note"] = (f"⚠️ DeepSeek 不可用，本回答由本地规则引擎生成。原因：{reason}")
    return ans


def self_test(cfg: Optional[LLMConfig] = None) -> Dict[str, Any]:
    """连通性自检：发一次最小的对话请求，只验证鉴权与网络。"""
    c = cfg or LLMConfig.from_env()
    if not c.enabled:
        return {"ok": False, "reason": "未配置 DEEPSEEK_API_KEY"}
    try:
        resp = _post(c, "/chat/completions", {
            "model": c.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        })
        return {"ok": True, "model": c.model,
                "usage": resp.get("usage")}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        hint = {401: "API Key 无效或已过期",
                402: "余额不足",
                429: "请求过于频繁"}.get(exc.code, "请求失败")
        return {"ok": False, "reason": f"HTTP {exc.code}：{hint}",
                "detail": detail}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
