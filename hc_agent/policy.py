"""policy.py — 权限分级与动作白名单。

## 为什么要这个

承载力 Agent 会调用引擎、跑搜索、生成报告。一旦出错，后果比"答错一句话"严重。
所以必须能回答：**这个操作在这个权限级别下允许吗？**

借的是成熟监管类系统的做法（分级 + 白名单 + 人工审批闸门）：

```
L0 read_only            只读：看结果、算摘要、读报告——不跑任何计算
L1 offline_simulation   离线仿真：跑潮流、跑搜索、做假设分析（不改变外部系统）
L2 candidate_generation 候选生成：出整改建议、出调压/储能方案候选
L3 approved_execution   批准执行：签发结论、提交审批
```

**关键约束**：本 Agent **永远没有 L4**（直接操作现场设备）。
这不是没做，而是刻意不做 —— 承载力评估的结论必须经人签字才能执行。

## 与规则包的关系

- 规则包（`rules.yaml`）管**数值**：限值、同时率、裕度。
- 权限策略（本模块）管**能不能做这件事**：跑/不跑、签发/不签发。

两者独立：换了规则包不代表能做更多动作，反之亦然。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, FrozenSet, Optional


class PermissionLevel(IntEnum):
    """权限级别。数字越大权限越高。"""

    READ_ONLY = 0
    OFFLINE_SIMULATION = 1
    CANDIDATE_GENERATION = 2
    APPROVED_EXECUTION = 3


LEVEL_LABEL: Dict[int, str] = {
    0: "只读",
    1: "离线仿真",
    2: "候选生成",
    3: "批准执行",
}


class PolicyViolation(RuntimeError):
    """动作超出当前权限级别时抛出。

    刻意用异常而不是返回 False：越权是**错误**，不是一种正常结果，
    静默返回 False 会被调用方忽略（"看起来成功了"）。
    """

    def __init__(self, action: str, need: PermissionLevel,
                 have: PermissionLevel, why: str = "") -> None:
        self.action = action
        self.need = need
        self.have = have
        self.why = why
        need_i = int(need) if not isinstance(need, int) else need
        have_i = int(have)
        need_txt = ("永久禁用" if need_i >= 99
                    else LEVEL_LABEL.get(need_i, str(need_i)))
        msg = (f"动作 {action} 需要「{need_txt}」权限，"
               f"当前是「{LEVEL_LABEL.get(have_i, str(have_i))}」")
        if why:
            msg += f"（{why}）"
        super().__init__(msg)


#: 每个动作需要的最低权限。**没列出的动作一律拒绝**（默认拒绝，不是默认允许）。
ACTION_LEVELS: Dict[str, PermissionLevel] = {
    # ---- L0 只读：看已有产物
    "read_run": PermissionLevel.READ_ONLY,
    "list_runs": PermissionLevel.READ_ONLY,
    "summary": PermissionLevel.READ_ONLY,
    "explain": PermissionLevel.READ_ONLY,
    "verify_audit_chain": PermissionLevel.READ_ONLY,
    "verify_caliber_binding": PermissionLevel.READ_ONLY,
    "evaluate_acceptance": PermissionLevel.READ_ONLY,
    "read_report": PermissionLevel.READ_ONLY,
    # ---- L1 离线仿真：会真的调引擎，但不产出对外结论
    "analyze_opendss_case": PermissionLevel.OFFLINE_SIMULATION,
    "inspect_dss_case": PermissionLevel.OFFLINE_SIMULATION,
    "what_if_dss_case": PermissionLevel.OFFLINE_SIMULATION,
    "run_capacity_search": PermissionLevel.OFFLINE_SIMULATION,
    "run_independent_verification": PermissionLevel.OFFLINE_SIMULATION,
    # ---- L2 候选生成：出建议，但只是"候选"，不是结论
    "propose_remediation": PermissionLevel.CANDIDATE_GENERATION,
    "propose_remediation_from_run": PermissionLevel.CANDIDATE_GENERATION,
    "propose_storage_sizing": PermissionLevel.CANDIDATE_GENERATION,
    "propose_voltage_measure": PermissionLevel.CANDIDATE_GENERATION,
    "draft_report": PermissionLevel.CANDIDATE_GENERATION,
    # ---- L3 批准执行：签发对外结论
    "approve_capacity": PermissionLevel.APPROVED_EXECUTION,
    "publish_capacity": PermissionLevel.APPROVED_EXECUTION,
    "sign_report": PermissionLevel.APPROVED_EXECUTION,
}

#: 明确**永不开放**的动作：就算把权限调到 3，也不能做。
#: 留这张表是为了让"我们不做这个"变成可检查的代码，而不是口头承诺。
FORBIDDEN_ACTIONS: FrozenSet[str] = frozenset({
    "write_device",            # 写现场设备
    "send_control_command",    # 下发控制命令
    "operate_switch",          # 操作开关
    "adjust_tap_online",       # 在线调分接头
    "close_dg_permit",         # 直接签发并网许可（必须人工）
    "bypass_human_approval",   # 绕过人工审批
})


@dataclass(frozen=True)
class Policy:
    """一次任务的权限策略。"""

    policy_id: str = "hc-default"
    version: str = "1.0"
    level: PermissionLevel = PermissionLevel.OFFLINE_SIMULATION
    #: 额外放开的动作（默认空；给特定任务临时提权用）
    extra_allowed: FrozenSet[str] = field(default_factory=frozenset)
    #: 是否要求人工审批才能签发（恒为 True，保留字段便于显式声明）
    require_human_approval: bool = True
    note: str = ""

    # ---- 查询
    def allowed(self, action: str) -> bool:
        a = str(action or "")
        if a in FORBIDDEN_ACTIONS:
            return False
        if a in self.extra_allowed:
            return True
        need = ACTION_LEVELS.get(a)
        if need is None:
            return False          # 未登记的动作默认拒绝
        return int(self.level) >= int(need)

    def need_level(self, action: str) -> Optional[PermissionLevel]:
        return ACTION_LEVELS.get(str(action or ""))

    def describe(self) -> Dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "level": int(self.level),
            "level_label": LEVEL_LABEL.get(int(self.level), str(self.level)),
            "require_human_approval": self.require_human_approval,
            "allowed_actions": sorted(
                a for a in ACTION_LEVELS if self.allowed(a)),
            "forbidden_actions": sorted(FORBIDDEN_ACTIONS),
            "note": self.note,
        }


def default_policy() -> Policy:
    """默认策略：L1 离线仿真。

    为什么不是 L3：默认给到"能算"就够了。签发结论属于正式流程，
    应该显式提权，不该是默认行为。
    """
    return Policy(policy_id="hc-default",
                  level=PermissionLevel.OFFLINE_SIMULATION,
                  note="默认只允许离线仿真；签发结论需显式提权到批准执行")


def policy_from_meta(meta: Optional[Dict[str, Any]]) -> Policy:
    """从规则包/任务元数据里读权限级别。

    认 `permission_level`，可以是整数（0~3）或名字（read_only 等）。
    读不到就用默认。**读错不静默降级为最高权限** —— 一律回落到默认 L1。
    """
    if not isinstance(meta, dict):
        return default_policy()
    raw = meta.get("permission_level")
    if raw is None:
        return default_policy()
    name_map = {"read_only": 0, "offline_simulation": 1,
                "candidate_generation": 2, "approved_execution": 3}
    try:
        if isinstance(raw, str) and raw.strip().lower() in name_map:
            lvl = name_map[raw.strip().lower()]
        else:
            lvl = int(raw)
        if not (0 <= lvl <= 3):
            raise ValueError("越界")
        return Policy(level=PermissionLevel(lvl),
                      note=f"来自任务元数据 permission_level={raw}")
    except Exception:
        return default_policy()


class RuleGuard:
    """动作调用前的守门人。所有会改状态的动作都应先过它。"""

    def __init__(self, policy: Optional[Policy] = None) -> None:
        self.policy = policy or default_policy()
        self._calls: list = []

    def check(self, action: str, **context: Any) -> None:
        """不通过就抛 PolicyViolation。"""
        if self.policy.allowed(action):
            return
        a = str(action or "")
        if a in FORBIDDEN_ACTIONS:
            raise PolicyViolation(
                a, 99, self.policy.level,
                "该动作在本 Agent 中永久禁用（无设备写操作能力）")
        need = self.policy.need_level(a)
        if need is None:
            raise PolicyViolation(
                a, 99, self.policy.level,
                "动作未在允许清单中，按默认拒绝处理")
        raise PolicyViolation(a, need, self.policy.level)

    def log(self, action: str, result: str = "ok") -> None:
        """记录一次动作（供审计链使用）。"""
        self._calls.append({"action": str(action), "result": str(result),
                            "level": int(self.policy.level)})

    def history(self) -> list:
        return list(self._calls)
