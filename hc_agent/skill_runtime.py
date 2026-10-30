"""skill_runtime.py — 可执行技能契约。

## 为什么需要"可执行"的技能

本工程原有的 `skills/cards/*.md` 是**技能卡**：写清目的、输入、步骤、边界。
它对人好读，但机器**无法校验**"这一步到底能不能跑、需要什么权限"。

这里补一层**可加载的契约**（`skills/cards/*/skill.json`）：

```json
{
  "id": "HC.REMEDIATION.PROPOSE",
  "version": "1.0.0",
  "display_name": "整改建议候选",
  "actions": ["propose_remediation"],     // 对应 policy.ACTION_LEVELS
  "handler": "hc_agent.remediation:propose",  // 实际实现
  "permission": "candidate_generation",   // 需要的最低权限
  "requires": ["binding_constraint"],     // 必需输入
  "outputs": ["candidates"],              // 产出
  "auto_enabled": false                    // 是否允许自动触发
}
```

加载时**严格校验**：缺字段、ID 重复、动作重复、handler 不存在，一律报错。
不做"缺了就跳过"—— 跳过会让"技能明明没生效但界面显示正常"。

## 与技能卡的关系

| 层 | 文件 | 谁用 |
|---|---|---|
| 技能卡（人读） | `skills/cards/SK-*.md` | 评审、审计、培训 |
| 技能契约（机读） | `skills/contracts/*.json` | 运行时加载、权限校验、调用 |

两者用同一个 `skill_code`（SK-01…）对应上。
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from hc_agent.policy import (ACTION_LEVELS, LEVEL_LABEL, PermissionLevel,
                             Policy, PolicyViolation, RuleGuard)

#: 契约必填字段
REQUIRED_FIELDS = ("id", "version", "display_name", "actions", "handler",
                   "permission", "auto_enabled")


class SkillLoadError(RuntimeError):
    """契约文件不合法。刻意抛错而不是跳过。"""


@dataclass(frozen=True)
class SkillContract:
    id: str
    version: str
    display_name: str
    actions: Tuple[str, ...]
    handler: str
    permission: str
    requires: Tuple[str, ...]
    outputs: Tuple[str, ...]
    auto_enabled: bool
    root: Path
    description: str = ""
    skill_code: str = ""
    card: str = ""

    @property
    def permission_level(self) -> Optional[PermissionLevel]:
        m = {"read_only": 0, "offline_simulation": 1,
             "candidate_generation": 2, "approved_execution": 3}
        v = m.get(str(self.permission).strip().lower())
        return None if v is None else PermissionLevel(v)


class SkillRegistry:
    """扫描并加载技能契约。"""

    def __init__(self, root) -> None:
        self.root = Path(str(root))
        self.contracts: Dict[str, SkillContract] = {}
        self.action_index: Dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self.contracts.clear()
        self.action_index.clear()
        if not self.root.is_dir():
            return
        for path in sorted(self.root.glob("*/skill.json")):
            self._load_one(path)

    def _load_one(self, path: Path) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SkillLoadError(f"{path} 不是合法 JSON：{exc}") from exc
        missing = [k for k in REQUIRED_FIELDS if k not in raw]
        if missing:
            raise SkillLoadError(f"{path.name} 缺少字段：{'、'.join(missing)}")
        cid = str(raw["id"])
        if cid in self.contracts:
            raise SkillLoadError(f"重复技能 ID：{cid}")
        actions = tuple(str(x) for x in (raw.get("actions") or []))
        if not actions:
            raise SkillLoadError(f"{cid} 的 actions 为空")
        # 每个动作都必须在权限表里登记，否则运行时无法判权限
        for a in actions:
            if a not in ACTION_LEVELS:
                raise SkillLoadError(
                    f"{cid} 的动作 {a} 未在 policy.ACTION_LEVELS 中登记")
            if a in self.action_index:
                raise SkillLoadError(f"动作重复注册：{a}")
        perm = str(raw["permission"]).strip().lower()
        if perm not in ("read_only", "offline_simulation",
                        "candidate_generation", "approved_execution"):
            raise SkillLoadError(f"{cid} 的 permission 非法：{perm}")
        # handler 必须真的能解析到
        self._resolve_handler(str(raw["handler"]))
        c = SkillContract(
            id=cid, version=str(raw["version"]),
            display_name=str(raw["display_name"]),
            actions=actions, handler=str(raw["handler"]),
            permission=perm,
            requires=tuple(str(x) for x in (raw.get("requires") or [])),
            outputs=tuple(str(x) for x in (raw.get("outputs") or [])),
            auto_enabled=bool(raw["auto_enabled"]),
            root=path.parent,
            description=str(raw.get("description") or raw["display_name"]),
            skill_code=str(raw.get("skill_code") or ""),
            card=str(raw.get("card") or ""))
        self.contracts[cid] = c
        for a in actions:
            self.action_index[a] = cid

    @staticmethod
    def _resolve_handler(spec: str) -> Callable:
        """`模块:函数` → 可调用对象。"""
        if ":" not in spec:
            raise SkillLoadError(f"handler 必须写成 模块:函数，实际是 {spec}")
        mod, fn = spec.split(":", 1)
        try:
            m = importlib.import_module(mod)
        except Exception as exc:
            raise SkillLoadError(f"handler 模块导入失败 {mod}：{exc}") from exc
        f = getattr(m, fn, None)
        if not callable(f):
            raise SkillLoadError(f"handler 不存在或不可调用：{spec}")
        return f

    # ------------------------------------------------------------ 查询
    def get(self, contract_id: str) -> Optional[SkillContract]:
        return self.contracts.get(contract_id)

    def for_action(self, action: str) -> Optional[SkillContract]:
        cid = self.action_index.get(str(action or ""))
        return self.contracts.get(cid) if cid else None

    def catalog(self) -> List[Dict[str, Any]]:
        return [{
            "id": c.id, "version": c.version,
            "display_name": c.display_name,
            "skill_code": c.skill_code,
            "actions": list(c.actions),
            "permission": c.permission,
            "permission_label": LEVEL_LABEL.get(int(c.permission_level or 0), ""),
            "requires": list(c.requires),
            "outputs": list(c.outputs),
            "auto_enabled": c.auto_enabled,
            "description": c.description,
        } for c in self.contracts.values()]


class SkillRuntime:
    """按契约执行技能：先过权限，再调 handler，最后记审计。"""

    def __init__(self, registry: SkillRegistry,
                 policy: Optional[Policy] = None) -> None:
        self.registry = registry
        self.guard = RuleGuard(policy)

    def can(self, action: str) -> bool:
        return self.guard.policy.allowed(action)

    def execute(self, action: str, **params: Any) -> Dict[str, Any]:
        """执行一个动作。

        返回 `{"ok", "skill", "action", "result"}` 或 `{"ok": False, "error"}`。
        **越权会抛 PolicyViolation**（不是返回 ok=False）——
        越权是错误，不该被当成普通结果忽略。
        """
        c = self.registry.for_action(action)
        if c is None:
            return {"ok": False, "action": action,
                    "error": f"没有技能注册该动作：{action}"}
        # 契约声明的权限 不能低于 动作表要求的权限
        need = c.permission_level
        table_need = ACTION_LEVELS.get(action)
        if need is not None and table_need is not None and int(need) > int(table_need):
            # 契约要求更严，以契约为准（更安全的方向）
            self.guard.policy  # noqa: B018  （保持可读性）
            if int(self.guard.policy.level) < int(need):
                raise PolicyViolation(action, need, self.guard.policy.level,
                                      f"技能 {c.id} 声明的权限更高")
        self.guard.check(action)
        missing = [k for k in c.requires if k not in params]
        if missing:
            return {"ok": False, "action": action, "skill": c.id,
                    "error": f"缺少必需输入：{'、'.join(missing)}"}
        fn = self.registry._resolve_handler(c.handler)
        try:
            res = fn(**params)
        except Exception as exc:
            self.guard.log(action, "error")
            return {"ok": False, "action": action, "skill": c.id,
                    "error": f"{type(exc).__name__}: {exc}"}
        self.guard.log(action, "ok")
        return {"ok": True, "action": action, "skill": c.id,
                "version": c.version, "result": res}


def default_registry() -> SkillRegistry:
    """加载本工程 `skills/contracts` 下的契约。"""
    from hc_agent import paths
    # 打包后契约随 skills 一起分发
    for base in (paths.resource("skills", "contracts"),
                 paths.bundle_root() / "skills" / "contracts",
                 Path.cwd() / "skills" / "contracts"):
        if base.is_dir() and any(base.glob("*/skill.json")):
            return SkillRegistry(base)
    return SkillRegistry(Path.cwd() / "skills" / "contracts")
