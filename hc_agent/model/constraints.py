"""constraints.py — 约束判定（SK-06 电压 / SK-07 载流与反向潮流 /
SK-08 三相不平衡 / SK-09 短路容量校核）。

设计文档 6.2 约束校验模型 / 表 23 结果字段 / 17.11 指标口径。

铁律：判据只描述数据结构和计算责任。具体限值、反送边界、短路模型、
三相不平衡定义和场景系数必须来自经审批的规则包或专业计算引擎，
Agent 不得内置未经确认的常数。
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from hc_agent.errors import (
    ERR_RULE_UNSET,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
)
from hc_agent.io.schema import as_float, as_str
from hc_agent.model.powerflow import ScenarioRun
from hc_agent.rules import ConstraintRule, RulePackage, evaluate_constraint

IMBALANCE_PHASES = ("A", "B", "C")

# OpenDSS 内部等值母线：sourcebus 是 Vsource 的内部无穷大母线，其电压/相角由
# 电源定义直接给出，不是被评估的网络节点。设计文档 6.1 的评估对象是"台区、
# 馈线和变电站"的实际节点，故一律排除该等值母线，避免把电源内电势当作节点
# 越限或三相不平衡（本机实测：纳入后会产出 sourcebus 的伪越限与不稳定计数）。
EXCLUDED_BUSES = ("sourcebus",)


def is_excluded_bus(bus: str) -> bool:
    b = (bus or "").strip().lower()
    return b in EXCLUDED_BUSES


@dataclass
class ConstraintResult:
    """单条约束结果（设计文档 表 56 constraint_results.csv）。"""

    scenario_id: str
    timestamp: str
    object_id: str
    constraint_id: str
    metric: str
    value: Optional[float]
    unit: str
    lower_limit: Optional[float]
    upper_limit: Optional[float]
    margin: Optional[float]
    status: str
    evidence_ref: str = ""
    binding: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id, "timestamp": self.timestamp,
            "object_id": self.object_id, "constraint_id": self.constraint_id,
            "metric": self.metric, "value": self.value, "unit": self.unit,
            "lower_limit": self.lower_limit, "upper_limit": self.upper_limit,
            "margin": self.margin, "status": self.status,
            "evidence_ref": self.evidence_ref,
            "detail": self.detail or "",
        }


def is_worse(value: Optional[float], current: Optional[float],
             current_key: str, candidate_key: str,
             *, rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> bool:
    """确定性的最不利选择（设计文档 2.3 可复现性要求）。

    设计文档 2.3 规定："冻结数据快照、规则、技能、OpenDSS 版本和运行参数后，
    状态与离散判定应一致"。直接用 `value > current` 比较会在数值近似相等时
    由浮点末位决定胜负，从而让"最不利时段"随迭代顺序变化——主计算与独立复算
    因此可能选出不同的小时，产生虚假的复算差异（本机实测：全网平衡时不平衡度
    逐位相同，选出的时段随遍历顺序在 18:00/23:00 之间跳变）。

    本函数在容差内视为相等，此时按时间戳字典序取较小者，使选择与顺序无关。
    """
    if current is None:
        return True
    if value is None:
        return False
    if math.isclose(value, current, rel_tol=rel_tol, abs_tol=abs_tol):
        # 数值等价 -> 用稳定的次关键字（时间戳/对象名）打破平局
        return candidate_key < current_key
    return value > current


def is_worse_min(value: Optional[float], current: Optional[float],
                 current_key: str, candidate_key: str,
                 *, rel_tol: float = 1e-9, abs_tol: float = 1e-12) -> bool:
    """确定性的"最小即最不利"选择（用于电压下限等场景）。"""
    if current is None:
        return True
    if value is None:
        return False
    if math.isclose(value, current, rel_tol=rel_tol, abs_tol=abs_tol):
        return candidate_key < current_key
    return value < current


class ConstraintEvaluator:
    """约束评估器（设计文档 4.9：技能返回后，规则中心负责约束判定）。"""

    def __init__(self, rules: RulePackage) -> None:
        self.rules = rules
        self._rules_by_metric: Dict[str, List[ConstraintRule]] = {}
        for c in rules.constraints:
            self._rules_by_metric.setdefault(c.metric, []).append(c)

    # ------------------------------------------------------------- 入口
    def evaluate(self, run: ScenarioRun, *,
                 buses: Optional[Sequence[Dict[str, Any]]] = None,
                 fault_results: Optional[Dict[str, Any]] = None
                 ) -> List[ConstraintResult]:
        """对一次场景集执行结果判定全部强制约束。"""
        results: List[ConstraintResult] = []
        results.extend(self.evaluate_voltage(run))
        results.extend(self.evaluate_thermal(run))
        results.extend(self.evaluate_reverse_flow(run))
        results.extend(self.evaluate_imbalance(run))
        results.extend(self.evaluate_short_circuit(run, fault_results))
        return results

    @staticmethod
    def all_pass(results: Sequence[ConstraintResult]) -> bool:
        """所有必需对象、时间片、场景及约束均 pass 才算**完全通过**（17.12）。

        注意区分两个不同谓词：
          - all_pass：全部 pass，无 fail 也无 unknown。用于"能否发布正式结论"。
          - feasible：无 fail。用于容量搜索中的**可行性判定**。
        设计文档 17.12 明确："超时、不收敛或短路模型缺失是 unknown，
        不可等同于电气不可行"。因此搜索可行性必须用 feasible，
        否则任何 unknown（例如未开展短路研究时的 SHORT_CIRCUIT_LIMIT）
        都会让所有试探点变成不可行，容量恒为 0。
        """
        return all(r.status == STATUS_PASS for r in results)

    @staticmethod
    def feasible(results: Sequence[ConstraintResult]) -> bool:
        """可行 = 无强制约束越限（unknown 不阻断可行性，但会在结论中显式暴露）。

        与 all_pass 的分工见 all_pass 的说明。未知项必须继续出现在
        constraint_summary.unknown 与分级判定中，使 level 保持灰色、
        正式结论不可发布。
        """
        return not any(r.status == STATUS_FAIL for r in results)

    @staticmethod
    def has_unknown(results: Sequence[ConstraintResult]) -> bool:
        return any(r.status == STATUS_UNKNOWN for r in results)

    @staticmethod
    def count_by_status(results: Sequence[ConstraintResult]
                        ) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in results:
            out[r.status] = out.get(r.status, 0) + 1
        return out

    @staticmethod
    def binding(results: Sequence[ConstraintResult]) -> List[ConstraintResult]:
        """主导限制因素：越限中最严重的（余量最小）。"""
        bad = [r for r in results if r.status == STATUS_FAIL]
        if not bad:
            return []
        bad.sort(key=lambda r: (r.margin if r.margin is not None else -1e18))
        return bad

    # --------------------------------------------------- SK-06 电压越限
    def evaluate_voltage(self, run: ScenarioRun) -> List[ConstraintResult]:
        """节点电压越限校核（设计文档 表 23 电压越限）。

        按节点、相别、时间片检查电压幅值、持续时间和变化量。
        """
        rules = self._rules_by_metric.get("voltage_pu", [])
        if not rules:
            rules = [c for c in self.rules.constraints
                     if c.rule_class == "voltage"]
        if not rules:
            return []

        # 每 (scenario,node,phase) 收集时间序列，用于持续时间判据
        series: Dict[Tuple[str, str, str], List[Tuple[str, float]]] = {}
        for row in run.slices:
            if row.get("kind") != "bus":
                continue
            if is_excluded_bus(as_str(row.get("bus"))):
                continue
            v = row.get("v_pu")
            if v is None or v != v:
                continue
            key = (as_str(row.get("scenario_id")), as_str(row.get("bus")),
                   as_str(row.get("phase")))
            series.setdefault(key, []).append((as_str(row.get("timestamp")), v))

        out: List[ConstraintResult] = []
        for rule in rules:
            for (scid, bus, phase), vals in series.items():
                if not vals:
                    continue
                # 确定性最不利取值：先按数值，再按时间戳打破平局，
                # 避免相同数值时结果随迭代顺序变化（设计文档 2.3）
                ordered = sorted(vals, key=lambda x: (x[1], x[0]))
                worst_ts, worst_v = ordered[0]
                ts_hi, v_hi = ordered[-1]
                # 持续时间：越限延续的采样数 × 时间步
                duration = self._violation_duration(vals, rule)
                status, margin = evaluate_constraint(
                    rule, worst_v if _is_low_binding(vals, rule) else v_hi,
                    duration_minutes=None)
                if status == STATUS_FAIL and duration is not None:
                    status2, margin2 = evaluate_constraint(
                        rule, worst_v if _is_low_binding(vals, rule) else v_hi,
                        duration_minutes=duration)
                    status, margin = status2, margin2
                use_ts = worst_ts if _is_low_binding(vals, rule) else ts_hi
                use_v = worst_v if _is_low_binding(vals, rule) else v_hi
                out.append(ConstraintResult(
                    scenario_id=scid, timestamp=use_ts,
                    object_id=f"{bus}.{phase}", constraint_id=rule.rule_id,
                    metric=rule.metric, value=use_v, unit=rule.unit or "pu",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=margin, status=status,
                    detail={"v_min": worst_v, "v_max": v_hi,
                            "v_min_time": worst_ts, "v_max_time": ts_hi,
                            "duration_minutes": duration}))
        return out

    @staticmethod
    def _violation_duration(vals: List[Tuple[str, float]],
                            rule: ConstraintRule) -> Optional[float]:
        """估计越限持续时间（分钟），按相邻时间戳差值累加。"""
        from hc_agent.io.schema import parse_iso
        viol = [ts for ts, v in vals
                if (rule.min is not None and v < rule.min)
                or (rule.max is not None and v > rule.max)]
        if len(viol) < 2:
            return 0.0 if not viol else None
        times = sorted(t for t in (parse_iso(x) for x in viol) if t is not None)
        if len(times) < 2:
            return None
        steps = [(times[i + 1] - times[i]).total_seconds() / 60.0
                 for i in range(len(times) - 1)]
        if not steps:
            return None
        return float(max(steps)) * len(times)

    # ------------------------------------------- SK-07 载流与反向潮流
    def evaluate_thermal(self, run: ScenarioRun) -> List[ConstraintResult]:
        """设备热稳定与载流校核（设计文档 表 23 载流量）。

        线路、开关、母线等电流与持续/短时允许值比较。
        线路和变压器载流率优先按电流与设备允许值计算（17.11）。
        """
        rules = self._rules_by_metric.get("loading_pct", [])
        if not rules:
            rules = [c for c in self.rules.constraints
                     if c.rule_class == "thermal"]
        if not rules:
            return []

        # 取每 (scenario, element, phase) 的最大电流与额定值
        peak: Dict[Tuple[str, str, str], Tuple[str, float, Optional[float]]] = {}
        for row in run.slices:
            if row.get("kind") != "element":
                continue
            eid = as_str(row.get("equipment_id"))
            cls = as_str(row.get("element_class")).lower()
            if cls not in ("line", "transformer", "reactor", "capacitor"):
                continue
            i_a = row.get("i_a")
            if i_a is None or i_a != i_a:
                continue
            rating = row.get("rating_a")
            key = (as_str(row.get("scenario_id")), eid,
                   as_str(row.get("phase")) or "ALL")
            cur = peak.get(key)
            if is_worse(float(i_a), cur[1] if cur else None,
                        cur[0] if cur else "", as_str(row.get("timestamp"))):
                peak[key] = (as_str(row.get("timestamp")), float(i_a),
                             rating if rating is not None else None)

        out: List[ConstraintResult] = []
        for rule in rules:
            for (scid, eid, phase), (ts, i_a, rating) in peak.items():
                if not rating or rating <= 0:
                    # 缺少设备允许值时不能当成 pass（17.12）
                    out.append(ConstraintResult(
                        scenario_id=scid, timestamp=ts,
                        object_id=f"{eid}.{phase}", constraint_id=rule.rule_id,
                        metric=rule.metric, value=None,
                        unit=rule.unit or "pct",
                        lower_limit=rule.min, upper_limit=rule.max,
                        margin=None, status=STATUS_UNKNOWN,
                        detail={"reason": "缺少设备允许载流值 rating_a",
                                "i_a": i_a}))
                    continue
                loading = 100.0 * i_a / rating
                status, margin = evaluate_constraint(rule, loading)
                # 余量统一换算为电流安培口径（不同量纲不得混比余量）
                margin_a = None
                if rule.max is not None:
                    margin_a = (rule.max - loading) / 100.0 * rating
                out.append(ConstraintResult(
                    scenario_id=scid, timestamp=ts,
                    object_id=f"{eid}.{phase}", constraint_id=rule.rule_id,
                    metric=rule.metric, value=loading,
                    unit=rule.unit or "pct",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=margin, status=status,
                    detail={"i_a": i_a, "rating_a": rating,
                            "margin_a": margin_a}))
        return out

    def evaluate_reverse_flow(self, run: ScenarioRun) -> List[ConstraintResult]:
        """反向重过载校核（设计文档 表 23 反向重过载）。

        比较设备/断面反向潮流与经规则批准的允许边界。

        方向约定（本机实测标定，见 check_reverse.py 的物理判据实验）：
          dss.CktElement.Powers() 按"端子×导体"展开，每量占 2 个浮点 (P,Q)，
          索引 = 2*((terminal-1)*ncond + conductor)。对 line/transformer：
            端子 1 P > 0  => 功率由 bus1 **流入元件**，即正向供电；
            端子 1 P < 0  => 功率由元件**倒送回 bus1**，即反向潮流。

          物理判据（无歧义）：MV1 经线路供电给 MV2 的负荷时，端子 1 P = +500.9 kW
          （正向），此时反向潮流必须为 0；当 MV2 处光伏远大于负荷时，
          端子 1 P = −2480.1 kW（倒送），此时反向潮流必须 > 0。
          因此：reverse = max(0, −p_terminal1)。

          注入型元件（PVSystem/Generator）是反向潮流的成因而非承载通道，
          不参与判定，否则会把注入功率误判为数千 kW 的反送越限。
        """
        rules = self._rules_by_metric.get("reverse_flow_kw", [])
        if not rules:
            rules = [c for c in self.rules.constraints
                     if c.rule_class == "reverse_flow"]
        if not rules:
            return []

        # 只统计承担潮流的元件端子 1 三相合计
        peak: Dict[Tuple[str, str], Tuple[str, float, float]] = {}
        for row in run.slices:
            if row.get("kind") != "element":
                continue
            if as_str(row.get("phase")) != "ALL":
                continue
            cls = as_str(row.get("element_class")).lower()
            if cls not in ("line", "transformer", "reactor"):
                continue
            p = row.get("p_kw")
            if p is None or p != p:
                continue
            net = float(p)
            # 端子 1 P < 0 => 功率倒送回 bus1，反向潮流 = |P|
            reverse = max(0.0, -net)
            key = (as_str(row.get("scenario_id")), as_str(row.get("equipment_id")))
            cur = peak.get(key)
            if is_worse(reverse, cur[1] if cur else None,
                        cur[0] if cur else "", as_str(row.get("timestamp"))):
                peak[key] = (as_str(row.get("timestamp")), reverse, net)

        out: List[ConstraintResult] = []
        for rule in rules:
            for (scid, eid), (ts, reverse, net) in peak.items():
                status, margin = evaluate_constraint(rule, reverse)
                out.append(ConstraintResult(
                    scenario_id=scid, timestamp=ts, object_id=eid,
                    constraint_id=rule.rule_id, metric=rule.metric,
                    value=reverse, unit=rule.unit or "kW",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=margin, status=status,
                    detail={"max_reverse_flow_kw": reverse,
                            "terminal1_p_kw": net,
                            "sign_convention":
                                "reverse = max(0, -p_terminal1); "
                                "端子1 P<0 表示由元件倒送回 bus1"}))
        return out

    # ------------------------------------------- SK-08 三相不平衡校核
    def evaluate_imbalance(self, run: ScenarioRun) -> List[ConstraintResult]:
        """三相不平衡校核（设计文档 表 23 / 17.11）。

        100×|V₂|/|V₁|，V₁/V₂ 由三相复电压求序分量；仅有幅值不能完整求该指标。
        """
        rules = self._rules_by_metric.get("imbalance_pct", [])
        if not rules:
            rules = [c for c in self.rules.constraints
                     if c.rule_class == "imbalance"]
        if not rules:
            return []

        # 按 (scenario, bus, timestamp) 聚合三相复电压
        by_phase: Dict[Tuple[str, str, str], Dict[str, complex]] = {}
        for row in run.slices:
            if row.get("kind") != "bus":
                continue
            if is_excluded_bus(as_str(row.get("bus"))):
                continue
            phase = as_str(row.get("phase")).upper()
            if phase not in IMBALANCE_PHASES:
                continue
            v = row.get("v_v")
            ang = row.get("angle_deg")
            if v is None or ang is None or v != v:
                continue
            key = (as_str(row.get("scenario_id")), as_str(row.get("bus")),
                   as_str(row.get("timestamp")))
            by_phase.setdefault(key, {})[phase] = cmath.rect(
                float(v), math.radians(float(ang)))

        out: List[ConstraintResult] = []
        for rule in rules:
            worst: Dict[Tuple[str, str], Tuple[str, float, Dict[str, float]]] = {}
            for (scid, bus, ts), phases in by_phase.items():
                if len(phases) < 3:
                    continue
                imb = negative_sequence_imbalance(
                    phases["A"], phases["B"], phases["C"])
                if imb is None:
                    continue
                key = (scid, bus)
                cur = worst.get(key)
                if is_worse(imb, cur[1] if cur else None,
                            cur[0] if cur else "", ts):
                    worst[key] = (ts, imb, {
                        "va": abs(phases["A"]), "vb": abs(phases["B"]),
                        "vc": abs(phases["C"])})
            for (scid, bus), (ts, imb, detail) in worst.items():
                status, margin = evaluate_constraint(rule, imb)
                out.append(ConstraintResult(
                    scenario_id=scid, timestamp=ts, object_id=bus,
                    constraint_id=rule.rule_id, metric=rule.metric,
                    value=imb, unit=rule.unit or "pct",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=margin, status=status,
                    detail={"metric_definition":
                            "100*|V2|/|V1| from three-phase complex voltages",
                            **detail}))
        return out
    # --------------------------------------- SK-09 短路容量与保护触发
    def evaluate_short_circuit(self, run: ScenarioRun,
                               fault_results: Optional[Dict[str, Any]] = None
                               ) -> List[ConstraintResult]:
        """短路容量校核（设计文档 表 23 短路容量 / 17.11）。

        新增电源后短路电流/容量与设备开断及系统边界比较。
        短路研究仅在短路与零序参数完整时使用 FaultStudy；
        逆变器故障响应未经模型验证时，相关能力标为未覆盖（unknown）。
        """
        rules = self._rules_by_metric.get("fault_current_a", [])
        if not rules:
            rules = [c for c in self.rules.constraints
                     if c.rule_class == "short_circuit"]
        if not rules:
            return []

        out: List[ConstraintResult] = []
        for rule in rules:
            if not fault_results or not fault_results.get("available"):
                # 缺少故障模型：能力 unknown，不能当 pass 也不能当不可行
                out.append(ConstraintResult(
                    scenario_id="SC", timestamp="", object_id="SYSTEM",
                    constraint_id=rule.rule_id, metric=rule.metric,
                    value=None, unit=rule.unit or "A",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=None, status=STATUS_UNKNOWN,
                    detail={"reason": fault_results.get("reason")
                            if fault_results else "未执行短路研究"}))
                continue
            currents = fault_results.get("fault_element_currents") or []
            if not currents:
                out.append(ConstraintResult(
                    scenario_id="SC", timestamp="",
                    object_id=fault_results.get("bus", "SYSTEM"),
                    constraint_id=rule.rule_id, metric=rule.metric,
                    value=None, unit=rule.unit or "A",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=None, status=STATUS_UNKNOWN,
                    detail={"reason": "故障元件未返回具体故障电流；"
                                      "FaultStudy 等值量不等同于实际故障电流"}))
                continue
            for c in currents:
                i_f = c.get("i_fault_a")
                status, margin = evaluate_constraint(rule, i_f)
                out.append(ConstraintResult(
                    scenario_id="SC", timestamp="",
                    object_id=f"{fault_results.get('bus','')}.{c.get('phase','')}",
                    constraint_id=rule.rule_id, metric=rule.metric,
                    value=i_f, unit=rule.unit or "A",
                    lower_limit=rule.min, upper_limit=rule.max,
                    margin=margin, status=status,
                    detail={"fault_type": fault_results.get("fault_type")}))
        return out


def _is_low_binding(vals: List[Tuple[str, float]],
                    rule: ConstraintRule) -> bool:
    """判断下限还是上限为绑定约束（决定报告哪个最不利值）。"""
    lo_viol = (rule.min is not None
               and any(v < rule.min for _, v in vals))
    hi_viol = (rule.max is not None
               and any(v > rule.max for _, v in vals))
    if lo_viol and not hi_viol:
        return True
    if hi_viol and not lo_viol:
        return False
    if rule.min is None:
        return False
    if rule.max is None:
        return True
    # 两者都越限：取相对越限更严重的
    d_lo = min(rule.min - v for _, v in vals)
    d_hi = max(v - rule.max for _, v in vals)
    return d_lo >= d_hi


def negative_sequence_imbalance(va: complex, vb: complex, vc: complex,
                                ) -> Optional[float]:
    """三相电压不平衡度 = 100×|V₂|/|V₁|（设计文档 17.11）。

    a = 1∠120°，V₁ = (Va + a·Vb + a²·Vc)/3，V₂ = (Va + a²·Vb + a·Vc)/3。
    """
    a = cmath.rect(1.0, 2.0 * math.pi / 3.0)
    a2 = a * a
    v1 = (va + a * vb + a2 * vc) / 3.0
    v2 = (va + a2 * vb + a * vc) / 3.0
    if abs(v1) < 1e-12:
        return None
    return 100.0 * abs(v2) / abs(v1)


def summarize_constraints(results: Sequence[ConstraintResult]
                          ) -> Dict[str, Any]:
    """约束汇总（设计文档 7.3 输出结论结构）。"""
    counts = ConstraintEvaluator.count_by_status(results)
    binding = ConstraintEvaluator.binding(results)
    return {
        "total": len(results),
        "pass": counts.get(STATUS_PASS, 0),
        "fail": counts.get(STATUS_FAIL, 0),
        "unknown": counts.get(STATUS_UNKNOWN, 0),
        "all_pass": ConstraintEvaluator.all_pass(results),
        "binding_constraints": [
            {"constraint_id": b.constraint_id, "object_id": b.object_id,
             "metric": b.metric, "value": b.value, "unit": b.unit,
             "limit_lower": b.lower_limit, "limit_upper": b.upper_limit,
             "margin": b.margin, "scenario_id": b.scenario_id,
             "timestamp": b.timestamp}
            for b in binding[:20]
        ],
    }
