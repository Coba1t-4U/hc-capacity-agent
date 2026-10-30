"""caliber.py — 求解口径声明（移植自天枢一号报告的核心一节）。

**为什么需要它**：本工程原来的报告只有散文式"风险与缺口"，缺**逐项口径声明**。
而实际复盘中最常被问到、也最容易出错的恰恰是"这次结果到底是什么口径"：

  * 负荷 P/Q 是名义值还是实际供出值？
  * 母线电压低于某阈值时负荷模型会不会变？
  * 储能算进负荷还是发电？
  * 总负荷与净负荷是不是一回事？
  * 哪些能力**没有**覆盖？

设计文档 17.13 也要求"报告注明求解范围、场景覆盖、未支持分析、模型假设、
精度和有效期"。本模块把这些**从代码事实推导出来**，而不是让人手写——
手写的口径声明必然与代码漂移。

关键纪律（照搬天枢一号）：**只有真的按输入相别建模时，才敢说"按相别建模"；
否则必须承认是平衡等值。** 三态顺序不可颠倒：
原生分相 → 按输入相别建模 → 平衡三相等值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 三相建模三态（顺序不可颠倒）
PHASE_NATIVE = "native"            # 原生分相模型
PHASE_MODELED = "modeled"          # 按输入相别建模
PHASE_BALANCED = "balanced"        # 平衡三相等值


@dataclass
class CaliberItem:
    key: str
    label: str
    value: str
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "value": self.value,
                "note": self.note}


@dataclass
class Caliber:
    """一份口径声明。"""

    items: List[CaliberItem] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)
    phase_state: str = PHASE_BALANCED
    assumptions: List[str] = field(default_factory=list)

    def add(self, key: str, label: str, value: str, note: str = "") -> None:
        self.items.append(CaliberItem(key, label, value, note))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "unsupported": list(self.unsupported),
            "phase_state": self.phase_state,
            "phase_statement": phase_statement(self.phase_state),
            "assumptions": list(self.assumptions),
        }


def phase_statement(state: str) -> str:
    """三相建模口径的**唯一措辞来源**（避免报告里多处各写一遍而漂移）。"""
    if state == PHASE_NATIVE:
        return ("原生分相模型（母线与支路按相建模，分相结果有效）")
    if state == PHASE_MODELED:
        return ("相别信息已按输入建模；结果仍不能直接视为原始求解器的等价复现")
    return ("输入不含相别信息，已按**平衡三相**等值建模；"
            "分相不平衡结论不适用")


def build_caliber(*, task: Optional[Dict[str, Any]] = None,
                  manifest: Optional[Dict[str, Any]] = None,
                  summary: Optional[Dict[str, Any]] = None,
                  rules: Optional[Dict[str, Any]] = None,
                  conversion: Optional[Dict[str, Any]] = None,
                  scenario_basis: str = "") -> Caliber:
    """从运行产物**推导**口径声明（不让人手写，避免与代码漂移）。"""
    task = task or {}
    manifest = manifest or {}
    summary = summary or {}
    c = Caliber()

    # ---- 1. 求解模式与场景覆盖
    atypes = [str(a) for a in (task.get("analysis_types") or [])]
    c.add("analysis_types", "分析类型",
          "、".join(atypes) if atypes else "（未声明）",
          "来自任务单的显式请求")
    tw = task.get("time_window") or {}
    c.add("time_window", "时间窗",
          f"{tw.get('start','?')} 至 {tw.get('end','?')}（半开区间）")
    c.add("scenario_coverage", "场景覆盖",
          f"{summary.get('scenario_count', '?')} 个场景 / "
          f"{summary.get('slice_count', '?')} 个时间片",
          scenario_basis)

    # ---- 2. 三相建模口径（三态）
    if conversion:
        if conversion.get("phase_modeled") and conversion.get("unbalanced_input"):
            c.phase_state = PHASE_MODELED
        else:
            c.phase_state = PHASE_BALANCED
        c.add("phase_model", "三相建模", phase_statement(c.phase_state),
              f"假定三相元件 {conversion.get('assumed_three_phase', 0)} 个 / "
              f"显式相别 {conversion.get('elements_with_phase', 0)} 个")
    else:
        c.phase_state = PHASE_NATIVE
        c.add("phase_model", "三相建模", phase_statement(PHASE_NATIVE))

    # ---- 3. 负荷模型（含 Vminpu 隐性转恒阻抗的说明）
    c.add("load_model", "负荷模型",
          "名义恒功率（P/Q 恒定，OpenDSS model=1）",
          "本工程显式写入 vminpu；三相负荷默认 0.95，母线电压低于该值时"
          "负荷自动转为恒阻抗，故**实际供出负荷可能低于名义值**。"
          "单相负荷不适用该机制，已显式置 vminpu=0")
    c.assumptions.append(
        "负荷按名义恒功率建模；低电压时 OpenDSS 的 Vminpu 机制会使其转为"
        "恒阻抗，实际供出可能低于名义值")

    # ---- 4. 总负荷 / 净负荷口径
    c.add("load_caliber", "总负荷口径",
          "各负荷元件端子 1 有功之和（负荷自身消耗）",
          "**不等于**源侧供电；含光伏/发电时另列净负荷")
    c.add("net_load", "净负荷口径",
          "源侧供电 − 网损", "与总负荷不是同一个量，不可混用")

    # ---- 5. 发电与储能口径
    c.add("generation_caliber", "发电注入口径",
          "各发电元件端子 1 有功之和（含 PVSystem）",
          "PVSystem 不是 Generator 的子集，须单独遍历（两者都计入）")
    c.add("storage_caliber", "储能口径",
          "各储能元件端子 1 有功之和，保留引擎原始符号"
          "（正=充电、负=放电）",
          "**不计入**总负荷与发电注入，但**必须计入**功率平衡式")

    # ---- 6. 约束与阈值来源
    rp = (manifest.get("rule_version") or summary.get("rule_package") or "?")
    c.add("rule_source", "阈值来源",
          f"规则包 {rp}",
          "所有限值来自经批准的规则包，Agent 不内置未经确认的常数")
    c.add("unknown_handling", "unknown 口径",
          "能力未覆盖（缺故障模型/零序参数等）记为 unknown",
          "unknown **既不计为通过、也不计为不可行**（设计文档 17.12）")

    # ---- 7. 数值精度与单位
    eng = (manifest.get("engine_fingerprint") or {})
    c.add("engine", "计算引擎",
          f"{eng.get('engine_id','opendss')} / "
          f"{eng.get('interface','OpenDSSDirect.py')} "
          f"{eng.get('interface_version','?')}",
          "全系统唯一电气计算引擎；所有电气数值由此产生")
    c.add("units", "单位口径",
          "功率 kW/kvar、电压 kV 与 pu、电流 A、长度 km",
          "数值列不带单位后缀；单位由字段名明确")
    c.add("sign_convention", "功率符号约定",
          "负荷 P/Q 消费为正；光伏 P 发出为正、Q 向网注入为正",
          "适配器负责转换为引擎符号，禁止混用")

    # ---- 8. 有效期与范围
    c.add("validity", "有效期",
          f"快照 {manifest.get('snapshot_id') or task.get('snapshot_id') or '?'}"
          f"；拓扑版本 {manifest.get('topology_version') or '?'}",
          "超出模型有效期须重新冻结快照后重算")

    # ---- 9. 转换保真度（若由外部格式转换而来）
    if conversion:
        c.add("fidelity", "输入保真度",
              conversion.get("fidelity_status", "unknown"),
              "；".join(conversion.get("warnings") or []) or
              "输入为原生格式，未经格式转换")
        for n in (conversion.get("notes") or []):
            c.assumptions.append(n)

    # ---- 10. 未覆盖能力（**必须显式列出**，不能默认读者知道）
    c.unsupported = [
        "概率潮流与数据驱动潮流",
        "自动分接头调压与无功补偿控制（本版只识别不动作）",
        "拓扑重构、负荷削减、重调度",
        "N-1 自动扫描",
        "暂态稳定、保护配合、谐波、频率全过程",
        "现场控制闭环（Agent 无任何设备写操作能力）",
    ]
    if "shortcircuit" not in atypes:
        c.unsupported.append("短路容量校核（本次任务未请求 shortcircuit）")
    return c


def render_caliber_markdown(caliber: Caliber) -> List[str]:
    """渲染为 Markdown（供报告使用）。"""
    L: List[str] = []
    L.append("### 本次求解口径声明")
    L.append("")
    L.append("| 项 | 本次口径 | 说明 |")
    L.append("|---|---|---|")
    for it in caliber.items:
        L.append(f"| {it.label} | {it.value} | {it.note} |")
    L.append("")
    if caliber.assumptions:
        L.append("**模型假设**：")
        L.append("")
        for a in caliber.assumptions:
            L.append(f"- {a}")
        L.append("")
    L.append("**本次未覆盖**：")
    L.append("")
    for u in caliber.unsupported:
        L.append(f"- {u}")
    L.append("")
    L.append(f"> {phase_statement(caliber.phase_state)}")
    return L
