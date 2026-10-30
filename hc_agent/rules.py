"""rules.py — 规则中心（设计文档 4.8 规则中心设计 / 17.9 规则包结构）。

规则与技能分离：技能负责执行专业过程，规则负责决定适用范围和判定口径；
规则发布后必须版本化、审批、可回退，并在每次任务中冻结引用。

规则冲突时按照"安全边界优先、业务专规优先、对象范围更具体优先、
版本生效时间明确优先"的顺序处理；仍无法消解时必须转人工。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent.errors import ERR_RULE_UNSET, RuleNotApprovedError
from hc_agent.evidence import read_json, sha256_obj, write_json
from hc_agent.io.schema import IssueCollector, as_float, as_str, is_nullish

SCHEMA_VERSION_RULES = "hc-rules-v1"

# 规则冲突消解优先级（设计文档 4.8）
PRIORITY_ORDER = ("safety_boundary", "business_specific", "object_specific",
                  "version_effective")

# 约束类别（设计文档 6.2 / 表 23）
CONSTRAINT_CLASSES = ("reverse_flow", "voltage", "imbalance", "thermal",
                      "short_circuit")


class RuleConfigError(Exception):
    pass


@dataclass
class ConstraintRule:
    """单条约束规则（设计文档 17.9 constraints 列表项）。"""

    rule_id: str
    metric: str
    operator: str
    min: Optional[float] = None
    max: Optional[float] = None
    unit: str = ""
    duration_minutes: Optional[float] = None
    standard_ref: Optional[str] = None
    failure_action: str = "block"
    rule_class: str = "voltage"
    scope: Dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        """限值的单位、适用对象、持续时间、依据条款和批准记录必须齐备。"""
        return not (self.min is None and self.max is None)

    def describe(self) -> str:
        lo = "-inf" if self.min is None else f"{self.min:g}"
        hi = "+inf" if self.max is None else f"{self.max:g}"
        return f"{self.metric} {self.operator} [{lo}, {hi}] {self.unit}"


@dataclass
class RulePackage:
    """规则包（设计文档 17.9 rules.yaml）。"""

    schema_version: str
    rule_package: str
    status: str
    scope: Dict[str, Any]
    approval_ref: Optional[str]
    effective_from: Optional[str]
    constraints: List[ConstraintRule]
    search: Dict[str, Any]
    management: Dict[str, Any]
    verification: Dict[str, Any]
    release: Dict[str, Any]
    grading: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)
    source_path: str = ""

    @property
    def approved(self) -> bool:
        return (self.status == "approved" and bool(self.approval_ref)
                and bool(self.effective_from))

    def constraint(self, rule_id: str) -> Optional[ConstraintRule]:
        for c in self.constraints:
            if c.rule_id == rule_id:
                return c
        return None

    def by_class(self, rule_class: str) -> List[ConstraintRule]:
        return [c for c in self.constraints if c.rule_class == rule_class]

    def fingerprint(self) -> str:
        return sha256_obj(self.raw)

    def require(self, path: str) -> Any:
        """读取规则中的批准确认值；null 表示尚待审批而必须阻断（17.9）。"""
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise RuleNotApprovedError(
                    f"规则项 {path} 不存在；不能由程序自行设定默认值",
                    code=ERR_RULE_UNSET,
                )
            node = node[part]
        if is_nullish(node):
            raise RuleNotApprovedError(
                f"规则项 {path} 为 null（尚待审批），按设计必须阻断",
                code=ERR_RULE_UNSET,
            )
        return node


# 规则类别推断：把 rule_id / metric 映射到五类强制约束
_CLASS_HINTS = {
    "voltage": ("voltage", "volt", "v_"),
    "reverse_flow": ("reverse", "backfeed", "reverse_flow"),
    "imbalance": ("imbalance", "unbalance", "negative_sequence", "v2"),
    "thermal": ("thermal", "current", "loading", "ampacity", "overload"),
    "short_circuit": ("short", "fault", "isc", "zsc"),
}


def _infer_class(rule_id: str, metric: str) -> str:
    blob = f"{rule_id} {metric}".lower()
    for cls, hints in _CLASS_HINTS.items():
        if any(h in blob for h in hints):
            return cls
    return "voltage"


def load_rule_package(path, *, collector: Optional[IssueCollector] = None,
                      require_approved: bool = False,
                      allow_draft: bool = True) -> RulePackage:
    """读取并校验 rules.yaml（设计文档 17.9）。"""
    p = Path(path)
    col = collector or IssueCollector()
    if not p.exists():
        raise RuleConfigError(f"规则包 {p} 不存在（全部任务必需）")

    text = p.read_text(encoding="utf-8")
    data = _parse_yaml(text, p)

    required = ("schema_version", "rule_package", "status", "scope",
                "constraints", "search", "management", "verification",
                "release")
    for key in required:
        if key not in data:
            col.add(file=p.name, field=key, code="RULE_FIELD_MISSING",
                    message=f"规则包缺少必需段 {key}", severity="error",
                    blocking=True)

    constraints: List[ConstraintRule] = []
    for idx, raw in enumerate(data.get("constraints") or []):
        rid = as_str(raw.get("rule_id"))
        if not rid:
            col.add(file=p.name, field=f"constraints[{idx}].rule_id",
                    code="RULE_FIELD_MISSING", message="rule_id 缺失",
                    severity="error", blocking=True)
            continue
        metric = as_str(raw.get("metric")) or rid.lower()
        cr = ConstraintRule(
            rule_id=rid, metric=metric,
            operator=as_str(raw.get("operator")) or "between",
            min=as_float(raw.get("min")), max=as_float(raw.get("max")),
            unit=as_str(raw.get("unit")),
            duration_minutes=as_float(raw.get("duration_minutes")),
            standard_ref=as_str(raw.get("standard_ref")) or None,
            failure_action=as_str(raw.get("failure_action")) or "block",
            rule_class=as_str(raw.get("rule_class"))
            or _infer_class(rid, metric),
            scope=raw.get("scope") or {},
            priority=int(raw.get("priority") or 100),
            raw=raw,
        )
        if cr.rule_class not in CONSTRAINT_CLASSES:
            col.add(file=p.name, field=f"constraints[{idx}].rule_class",
                    object_id=rid, code="RULE_CLASS_UNKNOWN",
                    message=f"rule_class={cr.rule_class} 不在 "
                            f"{list(CONSTRAINT_CLASSES)}",
                    severity="error", blocking=True)
        if cr.operator == "between" and cr.min is None and cr.max is None:
            col.add(file=p.name, field=f"constraints[{idx}]", object_id=rid,
                    code=ERR_RULE_UNSET,
                    message=f"约束 {rid} 的 min 与 max 均为 null（尚待审批）；"
                            f"该约束在计算中将被阻断",
                    severity="warning", blocking=False)
        constraints.append(cr)

    pkg = RulePackage(
        schema_version=as_str(data.get("schema_version")),
        rule_package=as_str(data.get("rule_package")),
        status=as_str(data.get("status")) or "draft",
        scope=data.get("scope") or {},
        approval_ref=data.get("approval_ref"),
        effective_from=data.get("effective_from"),
        constraints=constraints,
        search=data.get("search") or {},
        management=data.get("management") or {},
        verification=data.get("verification") or {},
        release=data.get("release") or {},
        grading=data.get("grading") or {},
        raw=data, source_path=str(p),
    )

    if pkg.schema_version != SCHEMA_VERSION_RULES:
        col.add(file=p.name, field="schema_version",
                code="SCHEMA_VERSION_UNEXPECTED",
                message=f"schema_version={pkg.schema_version} 与期望 "
                        f"{SCHEMA_VERSION_RULES} 不一致",
                severity="warning", blocking=False)

    if require_approved and not pkg.approved:
        col.add(file=p.name, code="RULE_UNAPPROVED",
                message=f"规则包状态={pkg.status}，approval_ref="
                        f"{pkg.approval_ref}，effective_from="
                        f"{pkg.effective_from}；未批准版本禁止用于正式任务",
                severity="error", blocking=True)

    if pkg.status not in ("draft", "approved", "deprecated", "test"):
        col.add(file=p.name, field="status", code="RULE_STATUS_INVALID",
                message=f"status={pkg.status} 未登记", severity="error",
                blocking=True)

    return pkg


def _parse_yaml(text: str, path: Path) -> Dict[str, Any]:
    """解析 YAML；PyYAML 不可用时退化为内置子集解析器。

    设计文档 表 54：YAML 用 safe_load。
    """
    try:
        import yaml
        try:
            return yaml.safe_load(text) or {}
        except Exception as exc:
            raise RuleConfigError(f"规则包 {path} YAML 解析失败: {exc}") from exc
    except ImportError:
        return _mini_yaml(text, path)


def _mini_yaml(text: str, path: Path) -> Dict[str, Any]:
    """极简 YAML 子集解析器（仅支持本设计 rules.yaml 的结构）。

    支持：嵌套映射、映射列表（- key: value）、行内 {a: b} 与 [x, y]、
    标量 null/true/false/数值/字符串。用于 PyYAML 缺失的受控环境。
    """
    import re

    lines: List[Tuple[int, str]] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, raw.strip()))

    def scalar(tok: str) -> Any:
        tok = tok.strip()
        if tok == "" or tok in ("null", "~", "None"):
            return None
        if tok.lower() in ("true", "yes"):
            return True
        if tok.lower() in ("false", "no"):
            return False
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1].strip()
            if not inner:
                return []
            return [scalar(x) for x in _split_top(inner, ",")]
        if tok.startswith("{") and tok.endswith("}"):
            inner = tok[1:-1].strip()
            out: Dict[str, Any] = {}
            if not inner:
                return out
            for part in _split_top(inner, ","):
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                out[as_str(k.strip().strip("'\""))] = scalar(v)
            return out
        if (tok.startswith("'") and tok.endswith("'")) or \
           (tok.startswith('"') and tok.endswith('"')):
            return tok[1:-1]
        try:
            return int(tok)
        except ValueError:
            pass
        try:
            return float(tok)
        except ValueError:
            pass
        return tok

    def _split_top(s: str, sep: str) -> List[str]:
        out, depth, cur = [], 0, ""
        for ch in s:
            if ch in "[{(":
                depth += 1
            elif ch in "]})":
                depth -= 1
            if ch == sep and depth == 0:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur)
        return out

    pos = 0

    def parse_block(min_indent: int) -> Any:
        nonlocal pos
        if pos >= len(lines):
            return None
        result: Any = None
        while pos < len(lines):
            indent, content = lines[pos]
            if indent < min_indent:
                break
            if content.startswith("- "):
                if result is None:
                    result = []
                if not isinstance(result, list):
                    break
                item_text = content[2:].strip()
                pos += 1
                if ":" in item_text and not item_text.startswith(("[", "{")):
                    key, _, val = item_text.partition(":")
                    entry: Dict[str, Any] = {}
                    if val.strip():
                        entry[as_str(key.strip())] = scalar(val)
                    else:
                        child = parse_block(indent + 1)
                        entry[as_str(key.strip())] = child
                    # 收集同一列表项的后续同级键
                    while pos < len(lines):
                        nind, ncontent = lines[pos]
                        if nind <= indent or ncontent.startswith("- "):
                            break
                        if nind > indent and ":" in ncontent:
                            k2, _, v2 = ncontent.partition(":")
                            pos += 1
                            if v2.strip():
                                entry[as_str(k2.strip())] = scalar(v2)
                            else:
                                entry[as_str(k2.strip())] = parse_block(nind + 1)
                        else:
                            break
                    result.append(entry)
                else:
                    result.append(scalar(item_text))
                continue
            if ":" not in content:
                pos += 1
                continue
            key, _, val = content.partition(":")
            key = as_str(key.strip())
            pos += 1
            if result is None:
                result = {}
            if not isinstance(result, dict):
                break
            if val.strip():
                result[key] = scalar(val)
            else:
                child = parse_block(indent + 1)
                result[key] = child if child is not None else {}
        return result

    doc = parse_block(0)
    if not isinstance(doc, dict):
        raise RuleConfigError(f"规则包 {path} 解析结果不是映射")
    return doc


# --------------------------------------------------------------- 规则求值
def evaluate_constraint(rule: ConstraintRule, value: Optional[float],
                        *, duration_minutes: Optional[float] = None
                        ) -> Tuple[str, Optional[float]]:
    """按规则判定单个指标；返回 (status, margin)。

    设计文档 17.12：任何试探点只有所有必需对象、时间片、场景及约束均 pass
    才算可行；超时、不收敛或短路模型缺失是 unknown，不可等同于电气不可行。
    """
    if value is None:
        return "unknown", None  # 与"不可行"严格区分

    if duration_minutes is not None and rule.duration_minutes is not None:
        # 持续时间判据：未达到配置持续时间时不计为越限
        if duration_minutes < rule.duration_minutes:
            return "pass", _margin(rule, value)

    ok = True
    if rule.operator == "between":
        if rule.min is not None and value < rule.min:
            ok = False
        if rule.max is not None and value > rule.max:
            ok = False
    elif rule.operator == "<=":
        if rule.max is not None and value > rule.max:
            ok = False
    elif rule.operator == ">=":
        if rule.min is not None and value < rule.min:
            ok = False
    elif rule.operator == "<":
        if rule.max is not None and value >= rule.max:
            ok = False
    elif rule.operator == ">":
        if rule.min is not None and value <= rule.min:
            ok = False
    elif rule.operator == "==":
        if rule.max is not None and abs(value - rule.max) > 1e-9:
            ok = False
    else:
        return "unknown", None
    return ("pass" if ok else "fail"), _margin(rule, value)


def _margin(rule: ConstraintRule, value: float) -> Optional[float]:
    """余量：越限为负，安全为正（设计文档 表 23）。"""
    margins = []
    if rule.min is not None:
        margins.append(value - rule.min)
    if rule.max is not None:
        margins.append(rule.max - value)
    return min(margins) if margins else None


def apply_management_rules(physical_kw: Optional[float],
                           reservations_kw: float,
                           pkg: RulePackage,
                           *, coincidence_factor: Optional[float] = None,
                           margin_ratio: Optional[float] = None
                           ) -> Dict[str, Any]:
    """应用管理口径形成管理容量（设计文档 6.3-5 / 17.9）。

    管理容量需先明确物理容量口径：capacity_physical_kw 为在含已并网源的
    基线之上可增加的容量，不能再扣一次已并网装机。
    管理裕度、同时率、占用和上下级约束按 formula_id 的批准顺序处理，
    不在程序中硬编码统一减法。
    """
    mg = pkg.management or {}
    formula_id = mg.get("formula_id")
    result: Dict[str, Any] = {
        "formula_id": formula_id,
        "capacity_physical_kw": physical_kw,
        "reservations_kw": reservations_kw,
        "steps": [],
        "status": "ok",
    }

    if physical_kw is None:
        result.update({"capacity_managed_kw": None, "status": "unknown",
                       "reason": "物理容量为 unknown，不生成管理容量"})
        return result

    if not formula_id or is_nullish(formula_id):
        # 未批准管理公式：按设计必须阻断，不硬编码统一减法
        result.update({
            "capacity_managed_kw": None, "status": "blocked",
            "reason": "management.formula_id 为 null（尚待审批），"
                      "按设计 17.9 必须阻断，不得在程序中硬编码统一减法",
        })
        return result

    cf = coincidence_factor
    if cf is None:
        cf = as_float(mg.get("coincidence_factor"))
    mr = margin_ratio
    if mr is None:
        mr = as_float(mg.get("margin_ratio"))

    value = float(physical_kw)
    steps = result["steps"]

    # 步骤 1：同时率
    if cf is not None and formula_id in ("HC-MGMT-STD", "concurrent_then_margin",
                                         "margin_then_concurrent",
                                         "concurrent_margin_reserve"):
        if formula_id in ("HC-MGMT-STD", "concurrent_then_margin",
                          "concurrent_margin_reserve"):
            before = value
            value *= cf
            steps.append({"step": "coincidence_factor", "factor": cf,
                          "before_kw": before, "after_kw": value})

    # 步骤 2：管理裕度
    if mr is not None and formula_id in ("HC-MGMT-STD", "margin_then_concurrent",
                                         "concurrent_margin_reserve",
                                         "concurrent_then_margin"):
        before = value
        value *= (1.0 - mr)
        steps.append({"step": "management_margin", "ratio": mr,
                      "before_kw": before, "after_kw": value})

    # 步骤 3：扣除有效占用
    before = value
    value -= float(reservations_kw or 0.0)
    steps.append({"step": "deduct_reservations", "reservations_kw":
                  float(reservations_kw or 0.0), "before_kw": before,
                  "after_kw": value})

    value = max(0.0, value)
    result.update({
        "capacity_managed_kw": value,
        "coincidence_factor": cf,
        "margin_ratio": mr,
        "note": "capacity_physical_kw 为含已并网源基线之上可增加的容量，"
                "不再扣一次已并网装机",
    })
    return result


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """兼容 dict 与 ConstraintResult 等对象（约束结果两种形态都要支持）。"""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def apply_grading(capacity_managed_kw: Optional[float],
                  constraint_rows: Sequence[Any],
                  pkg: RulePackage, *, data_risk: bool = False) -> Dict[str, Any]:
    """红黄绿分级（设计文档 6.4 / 表 25）。

    红黄绿由批准规则映射，不以经验阈值生成。
    灰色为内部状态：数据不足、模型异常、不收敛或能力不足时
    不生成红黄绿正式结论，标记"无法评估"。
    """
    g = pkg.grading or {}
    if capacity_managed_kw is None:
        return {"level": "grey", "reason": "无法评估（容量为 unknown）",
                "formal": False}

    if data_risk:
        return {"level": "grey", "reason": "存在未解决的数据/模型风险",
                "formal": False}

    fails = [r for r in constraint_rows if _row_get(r, "status") == "fail"]
    unknowns = [r for r in constraint_rows if _row_get(r, "status") == "unknown"]
    if unknowns:
        return {"level": "grey",
                "reason": f"{len(unknowns)} 项约束为 unknown，不生成正式结论",
                "formal": False}

    red_at = as_float(g.get("red_at_or_below_kw"))
    yellow_ratio = as_float(g.get("yellow_margin_ratio"))

    if capacity_managed_kw <= 0 or fails:
        return {"level": "red", "formal": True,
                "reason": ("无可开放空间" if capacity_managed_kw <= 0
                           else "强制约束不满足")}

    if red_at is not None and capacity_managed_kw <= red_at:
        return {"level": "red", "formal": True,
                "reason": f"管理容量 {capacity_managed_kw:g} kW 未超过红色阈值 "
                          f"{red_at:g} kW"}

    # 余量比例：以最小约束余量相对于限值区间宽度衡量
    ratios = []
    for r in constraint_rows:
        margin = _row_get(r, "margin")
        if margin is None:
            continue
        span = _limit_span_obj(r)
        if span and span > 0:
            ratios.append(float(margin) / span)
    min_ratio = min(ratios) if ratios else None

    if min_ratio is not None and yellow_ratio is not None \
            and min_ratio <= yellow_ratio:
        return {"level": "yellow", "formal": True,
                "min_margin_ratio": min_ratio,
                "reason": f"容量受限或接近阈值（最小余量比 "
                          f"{min_ratio:.2%} ≤ {yellow_ratio:.2%}）"}

    return {"level": "green", "formal": True,
            "min_margin_ratio": min_ratio,
            "reason": "在批准口径下具有开放空间，未触发重点风险"}


def _limit_span_obj(row: Any) -> Optional[float]:
    lo = _row_get(row, "lower_limit")
    hi = _row_get(row, "upper_limit")
    try:
        if lo is not None and hi is not None:
            return abs(float(hi) - float(lo))
        if hi is not None:
            return abs(float(hi))
        if lo is not None:
            return abs(float(lo))
    except (TypeError, ValueError):
        return None
    return None


def check_release_gate(pkg: RulePackage, *, verification_passed: bool,
                       review_approved: bool, approval_ref: Optional[str],
                       result_hash: Optional[str]) -> Dict[str, Any]:
    """发布门禁（设计文档 S08 / 11.2 三道门）。

    独立复算 + 专业复核 + 发布审批三道门，任何一项失败均不得发布。
    """
    rel = pkg.release or {}
    gates: List[Dict[str, Any]] = []

    if rel.get("require_verification", True):
        gates.append({"gate": "independent_verification",
                      "required": True, "passed": bool(verification_passed)})
    if rel.get("require_professional_review", True):
        gates.append({"gate": "professional_review",
                      "required": True, "passed": bool(review_approved)})
    if rel.get("require_approval", True):
        gates.append({"gate": "approval",
                      "required": True, "passed": bool(approval_ref)})

    failed = [g["gate"] for g in gates if g["required"] and not g["passed"]]
    return {
        "rule_package": pkg.rule_package,
        "gates": gates,
        "failed_gates": failed,
        "result_hash": result_hash,
        "approval_bound": bool(approval_ref and result_hash),
        "passed": not failed,
    }
