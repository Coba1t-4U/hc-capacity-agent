"""business_flow.py — 配网承载力业务流（闭环）的结构化描述。

**为什么要有这个模块**：业务上这条链是
「数据准备 → 评估计算 → 分级发布 → 接入应用 → 提升改造 → 动态更新」，
但本系统只实现了其中几段。把每段的**实现状态与缺口**写成代码，
好处是能回答"走到哪一步了、缺什么"，而不是靠人记。

口径（重要）：
  * ``implemented`` 有代码且被测试覆盖；
  * ``partial``     有代码但只覆盖了业务上的一部分环节；
  * ``absent``      本系统不提供该环节。
**状态是如实标注的，不是目标状态。**

业务流原文见 ``knowledge/business_flow/配网承载力业务流.md``。
"""

from __future__ import annotations

from typing import Any, Dict, List

#: 状态取值与中文标签
STATUS_LABELS = {
    "implemented": "已实现",
    "partial": "部分实现",
    "absent": "未实现",
}

#: 八个环节，按业务先后排列。
STAGES: List[Dict[str, Any]] = [
    {
        "key": "data_prepare",
        "no": 1,
        "name": "基础数据收集与治理",
        "goal": "把网架、运行、电源、规划四类数据凑齐并清洗到可算",
        "inputs": ["变电站/10kV线路/配变/台区拓扑与设备参数", "负荷曲线、电压电流",
                   "已并网与已受理电源容量", "规划负荷与改造计划"],
        "sources": ["营销系统", "PMS", "调度/D5000", "用采系统", "GIS"],
        "outputs": ["输入包（network.xlsx / profiles.csv / rules.yaml / task.json）"],
        "status": "partial",
        "modules": ["hc_agent/io/ingest.py", "hc_agent/io/validation.py",
                    "hc_agent/io/quality.py", "hc_agent/io/converters.py"],
        "gaps": ("本系统**不直连**营销/PMS/D5000/用采/GIS，数据要靠导出后整理成输入包；"
                 "已有输入包校验与质量治理，但不做多源抽取与主数据对齐。"),
    },
    {
        "key": "constraints",
        "no": 2,
        "name": "评估边界与约束条件设定",
        "goal": "把设备、电压、短路、反送、电能质量等约束写成可执行的规则包",
        "inputs": ["设备限值", "标准与项目批准口径"],
        "sources": ["规则包 rules.yaml（带审批引用）"],
        "outputs": ["可执行约束（含 standard_ref 与 failure_action）"],
        "status": "partial",
        "modules": ["hc_agent/rules.py", "hc_agent/policy.py"],
        "gaps": ("已实现 5 类：电压、热稳、反送、不平衡、短路；"
                 "**未实现**谐波、电压波动（闪变）、继电保护适应性。"),
    },
    {
        "key": "compute",
        "no": 3,
        "name": "承载力计算分析",
        "goal": "在典型场景下求满足全部约束的最大可接入容量，并扣除已受理",
        "inputs": ["输入包", "规则包", "场景与时间断面"],
        "sources": ["OpenDSS 潮流（主算）", "pandapower（交叉校核）"],
        "outputs": ["物理可开放容量", "管理口径容量", "绑定约束与裕度", "搜索轨迹"],
        "status": "partial",
        "modules": ["hc_agent/pipeline.py", "hc_agent/capacity_search.py",
                    "hc_agent/model/scenarios.py", "hc_agent/engines/registry.py"],
        "gaps": ("潮流 + 二分搜索 + 取各约束最小值已实现；"
                 "**未实现**「台区 → 10kV 线路 → 主变 → 区域电网」逐级计算，"
                 "当前按单个算例/馈线评估，跨层级叠加由使用者自行组织。"),
    },
    {
        "key": "grading",
        "no": 4,
        "name": "结果分级（红黄绿）",
        "goal": "按剩余可开放容量给区域定级，支撑准入判断",
        "inputs": ["可开放容量", "分级阈值"],
        "sources": ["规则包 grading 段"],
        "outputs": ["level = green / yellow / red"],
        "status": "implemented",
        "modules": ["hc_agent/errors.py", "hc_agent/rules.py"],
        "gaps": ("按阈值输出三级；注意**口径**：本系统的分级是「单算例判定」，"
                 "不等于省公司发布的区域承载力等级。"),
    },
    {
        "key": "publish",
        "no": 5,
        "name": "结果发布与公示",
        "goal": "向社会公开分级结果并报送主管部门",
        "inputs": ["分级结果", "发布口径"],
        "sources": ["网上国网等对外平台", "能源主管部门报送"],
        "outputs": ["对外公示数据", "报送材料"],
        "status": "absent",
        "modules": [],
        "gaps": "本系统只产出**内部报告**，不生成对外公示口径，也不对接发布平台。",
    },
    {
        "key": "access",
        "no": 6,
        "name": "业务应用（接入管理）",
        "goal": "用承载力结果支撑接入申请受理、方案答复与选址引导",
        "inputs": ["申请容量与位置", "可开放容量", "分级结果"],
        "sources": ["营销业务系统"],
        "outputs": ["可行/不可行结论", "绑定约束与裕度解释"],
        "status": "partial",
        "modules": ["hc_agent/qa.py", "hc_agent/remediation.py",
                    "hc_agent/io/dss_report.py"],
        "gaps": ("能回答「某容量接不接得下、为什么接不下」并给建议；"
                 "**未实现**工单流转、答复意见书出具、与营销系统对接。"),
    },
    {
        "key": "improve",
        "no": 7,
        "name": "提升措施与改造",
        "goal": "给出增容、储能、调压、控制等提升手段，改造后重评",
        "inputs": ["绑定约束", "整改知识"],
        "sources": ["知识库", "工程经验"],
        "outputs": ["整改建议（措施/代价/依据）"],
        "status": "partial",
        "modules": ["hc_agent/remediation.py"],
        "gaps": ("有措施建议库（含代价与适用边界）；"
                 "**未实现**改造方案的定量校核（改完能提多少）与再评估闭环。"),
    },
    {
        "key": "update",
        "no": 8,
        "name": "动态更新与闭环",
        "goal": "新并网、负荷变化、改造投运后重新评估，形成持续闭环",
        "inputs": ["更新后的数据"],
        "sources": ["数据回流"],
        "outputs": ["新的评估结果与证据链"],
        "status": "partial",
        "modules": ["hc_agent/pipeline.py", "hc_agent/audit_chain.py"],
        "gaps": ("可以随时重跑并生成新的哈希链证据；"
                 "**未实现**变更检测、增量触发与版本对比（哪次变化导致等级改变）。"),
    },
]


def summary() -> Dict[str, Any]:
    """按状态汇总八段。"""
    counts = {"implemented": 0, "partial": 0, "absent": 0}
    for s in STAGES:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {
        "total": len(STAGES),
        "counts": counts,
        "labels": {k: STATUS_LABELS.get(k, k) for k in counts},
        "absent": [s["name"] for s in STAGES if s["status"] == "absent"],
        "gaps": [{"name": s["name"], "gap": s["gaps"]}
                 for s in STAGES if s["status"] != "implemented"],
    }


def stage(key_or_no: Any) -> Dict[str, Any] | None:
    """按 key 或序号取单段。"""
    for s in STAGES:
        if str(s["key"]) == str(key_or_no) or str(s["no"]) == str(key_or_no):
            return s
    return None


def format_flow() -> str:
    """八段业务流的可读文本（Markdown）。"""
    lines = [
        "**配网承载力业务流（八段闭环）**",
        "",
        "数据准备 → 评估计算 → 分级发布 → 接入应用 → 提升改造 → 动态更新",
        "",
    ]
    for s in STAGES:
        label = STATUS_LABELS.get(s["status"], s["status"])
        mark = {"implemented": "✅", "partial": "◐", "absent": "✗"}[s["status"]]
        lines.append(f"{mark} **{s['no']}. {s['name']}**　{label}")
        if s["status"] != "implemented":
            lines.append(f"　　缺口：{s['gaps']}")
    st = summary()
    lines += [
        "",
        f"合计 {st['total']} 段：已实现 {st['counts']['implemented']}、"
        f"部分实现 {st['counts']['partial']}、未实现 {st['counts']['absent']}。",
        "",
        "**口径提醒**：分级是「单算例判定」，不等于省公司发布的区域承载力等级；"
        "未实现的环节不会被本系统代做。",
    ]
    return "\n".join(lines)


def format_stage(key_or_no: Any) -> str:
    """单段详情。"""
    s = stage(key_or_no)
    if s is None:
        return "没有这一段。可选：" + "、".join(
            f"{x['no']}.{x['name']}" for x in STAGES)
    label = STATUS_LABELS.get(s["status"], s["status"])
    lines = [
        f"**{s['no']}. {s['name']}**　{label}",
        "",
        f"目标：{s['goal']}",
        "输入：" + "、".join(s["inputs"]),
        "数据源：" + "、".join(s["sources"]),
        "输出：" + "、".join(s["outputs"]),
    ]
    if s["modules"]:
        lines.append("对应模块：" + "、".join(s["modules"]))
    else:
        lines.append("对应模块：无")
    lines += ["", f"缺口：{s['gaps']}"]
    return "\n".join(lines)
