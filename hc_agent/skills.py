"""skills.py — 技能注册中心（设计文档 4.4 技能注册与生命周期 / 4.6 技能应该怎么写）。

技能不是提示词，而是受约束的专业工具契约。每项技能应明确输入数据、适用对象、
计算方法、约束条件、输出字段和人工交接点；涉及潮流、电压、短路和设备能力的
数值必须由登记的专业计算引擎产生。

技能元数据字段（设计文档 表 14）：
  skill_id/version、business_scope、input/output schema、tool_allowlist、
  pre/post conditions、risk_level、test_suite、owner/approver、status
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from hc_agent import paths
from hc_agent.errors import HCAgentError
from hc_agent.io.schema import as_str

SKILL_STATUSES = ("draft", "test", "approved", "deprecated")
RISK_LEVELS = ("low", "medium", "high")
SCHEMA_VERSION_SKILLS = "hc-skills-v1"


class SkillRegistryError(HCAgentError):
    code = "SKILL_REGISTRY_INVALID"


@dataclass
class Skill:
    """一项技能（Skill Card + 机器可读注册项）。"""

    skill_id: str
    skill_code: str
    version: str
    name: str
    purpose: str
    status: str
    risk_level: str
    requires_engine: bool
    module: str = ""
    entrypoints: List[str] = field(default_factory=list)
    business_scope: List[str] = field(default_factory=list)
    object_levels: List[str] = field(default_factory=list)
    engine: Dict[str, Any] = field(default_factory=dict)
    inputs: List[Dict[str, Any]] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    procedure: List[str] = field(default_factory=list)
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    failure_policy: Dict[str, Any] = field(default_factory=dict)
    evidence_policy: List[str] = field(default_factory=list)
    safety_boundary: List[str] = field(default_factory=list)
    test_suite: Dict[str, Any] = field(default_factory=dict)
    owner: str = ""
    approver: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return self.status == "approved"

    @property
    def uid(self) -> str:
        return f"{self.skill_id}@{self.version}"

    def tool_allowlist(self) -> List[str]:
        """技能可调用的计算/检索/数据服务（设计文档 表 14）。"""
        tools = []
        if self.requires_engine:
            tools.append("opendss_adapter")
        if self.module:
            tools.append(self.module)
        for ep in self.entrypoints:
            tools.append(f"{self.module}.{ep}" if self.module else ep)
        return tools

    def card(self) -> str:
        """Skill Card 文本（设计文档 4.6 固定顺序）。"""
        L = [
            f"# {self.skill_id}@{self.version}  {self.name}",
            "",
            f"**目的**：{self.purpose}",
            "",
            f"**适用范围**：业务场景 {'/'.join(self.business_scope)}；"
            f"对象层级 {'/'.join(self.object_levels)}；"
            f"状态 {self.status}；风险等级 {self.risk_level}",
            "",
            f"**是否必须专业引擎**：{'是' if self.requires_engine else '否'}"
            + (f"（{self.engine.get('id')} / {self.engine.get('interface')}）"
               if self.engine else ""),
            "",
            "**输入**：",
        ]
        for i in self.inputs:
            L.append(f"- `{i.get('field')}`"
                     f"{'（' + str(i.get('unit')) + '）' if i.get('unit') else ''}"
                     f"{'（必填）' if i.get('required') else '（可选）'}"
                     f"{'  ' + str(i.get('note')) if i.get('note') else ''}")
        L += ["", "**前置条件**："]
        L += [f"- {p}" for p in self.preconditions]
        L += ["", "**执行步骤**："]
        L += [f"{s}" for s in self.procedure]
        L += ["", "**输出**："]
        for o in self.outputs:
            L.append(f"- `{o.get('field')}`"
                     f"{'（' + str(o.get('unit')) + '）' if o.get('unit') else ''}"
                     f"{'  ' + str(o.get('note')) if o.get('note') else ''}")
        L += ["", "**异常处理**："]
        for k, v in self.failure_policy.items():
            if v:
                L.append(f"- {k}: {', '.join(map(str, v)) if isinstance(v, list) else v}")
        L += ["", "**证据要求**："]
        L += [f"- {e}" for e in self.evidence_policy]
        L += ["", "**安全边界**："]
        L += [f"- {s}" for s in self.safety_boundary]
        L += ["", "**测试样例**："]
        for k, v in self.test_suite.items():
            if v:
                L.append(f"- {k}: {', '.join(map(str, v)) if isinstance(v, list) else v}")
        L += ["", f"**责任人/审批人**：{self.owner} / {self.approver}"]
        return "\n".join(L)


class SkillRegistry:
    """技能注册中心。"""

    def __init__(self, skills: Sequence[Skill], *, registry_version: str = "",
                 source_path: str = "") -> None:
        self.skills: List[Skill] = list(skills)
        self.registry_version = registry_version
        self.source_path = source_path
        self._by_id: Dict[str, Skill] = {s.skill_id: s for s in self.skills}
        self._by_code: Dict[str, Skill] = {s.skill_code: s for s in self.skills}

    def get(self, key: str) -> Optional[Skill]:
        return self._by_id.get(key) or self._by_code.get(key)

    def require(self, key: str, *, must_be_approved: bool = True) -> Skill:
        s = self.get(key)
        if s is None:
            raise SkillRegistryError(f"技能 {key} 未登记")
        if must_be_approved and not s.approved:
            raise SkillRegistryError(
                f"技能 {s.uid} 状态为 {s.status}；仅 approved 技能可被正式任务选用")
        return s

    def approved_skills(self) -> List[Skill]:
        return [s for s in self.skills if s.approved]

    def engine_skills(self) -> List[Skill]:
        return [s for s in self.skills if s.requires_engine]

    def for_scene(self, business_scene: str) -> List[Skill]:
        return [s for s in self.skills
                if business_scene in (s.business_scope or [])]

    def version_manifest(self) -> Dict[str, str]:
        """技能版本清单（写入 manifest.skill_version，设计文档 4.9）。"""
        return {s.skill_id: s.version for s in self.skills}

    def summary(self) -> Dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "skill_count": len(self.skills),
            "by_status": _count(self.skills, lambda s: s.status),
            "by_risk": _count(self.skills, lambda s: s.risk_level),
            "engine_required": len(self.engine_skills()),
            "skills": [{"skill_id": s.skill_id, "skill_code": s.skill_code,
                        "version": s.version, "name": s.name,
                        "status": s.status, "risk_level": s.risk_level,
                        "requires_engine": s.requires_engine,
                        "module": s.module}
                       for s in self.skills],
        }


def _count(items, keyfn) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for i in items:
        k = keyfn(i)
        out[k] = out.get(k, 0) + 1
    return out


def validate_registry(data: Dict[str, Any]) -> List[str]:
    """校验技能注册表（设计文档 表 14 字段要求）。"""
    issues: List[str] = []
    if as_str(data.get("schema_version")) != SCHEMA_VERSION_SKILLS:
        issues.append(f"schema_version 应为 {SCHEMA_VERSION_SKILLS}，"
                      f"实际 {data.get('schema_version')}")
    skills = data.get("skills") or []
    if not skills:
        issues.append("skills 为空")
    seen: set = set()
    required = ("skill_id", "skill_code", "version", "name", "purpose",
                "status", "risk_level", "requires_engine", "business_scope",
                "inputs", "preconditions", "procedure", "outputs",
                "failure_policy", "evidence_policy", "safety_boundary",
                "test_suite", "owner", "approver")
    for idx, s in enumerate(skills):
        sid = as_str(s.get("skill_id"))
        if not sid:
            issues.append(f"skills[{idx}] 缺 skill_id")
            continue
        if sid in seen:
            issues.append(f"skill_id {sid} 重复")
        seen.add(sid)
        for key in required:
            if key not in s or s.get(key) in (None, "", []):
                issues.append(f"{sid}: 缺字段 {key}")
        st = as_str(s.get("status"))
        if st and st not in SKILL_STATUSES:
            issues.append(f"{sid}: status={st} 不在 {list(SKILL_STATUSES)}")
        rl = as_str(s.get("risk_level"))
        if rl and rl not in RISK_LEVELS:
            issues.append(f"{sid}: risk_level={rl} 不在 {list(RISK_LEVELS)}")
        # 步骤中不得出现无法验收的措辞（设计文档 4.6）
        for step in (s.get("procedure") or []):
            for bad in ("适当", "合理", "必要时", "酌情"):
                if bad in str(step):
                    issues.append(f"{sid}: 步骤含不可验收措辞'{bad}'：{step}")
        if s.get("requires_engine") and not s.get("engine"):
            issues.append(f"{sid}: requires_engine=true 但未声明 engine")
    return issues


def load_skill_registry(path, *, strict: bool = True) -> SkillRegistry:
    """加载技能注册表。"""
    p = Path(path)
    if not p.exists():
        raise SkillRegistryError(f"技能注册表 {p} 不存在")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SkillRegistryError(f"技能注册表解析失败: {exc}") from exc

    issues = validate_registry(data)
    if issues and strict:
        raise SkillRegistryError("技能注册表校验失败：\n  - " + "\n  - ".join(issues))

    skills: List[Skill] = []
    for raw in data.get("skills") or []:
        skills.append(Skill(
            skill_id=as_str(raw.get("skill_id")),
            skill_code=as_str(raw.get("skill_code")),
            version=as_str(raw.get("version")),
            name=as_str(raw.get("name")),
            purpose=as_str(raw.get("purpose")),
            status=as_str(raw.get("status")),
            risk_level=as_str(raw.get("risk_level")),
            requires_engine=bool(raw.get("requires_engine")),
            module=as_str(raw.get("module")),
            entrypoints=[as_str(x) for x in (raw.get("entrypoints") or [])],
            business_scope=[as_str(x) for x in (raw.get("business_scope") or [])],
            object_levels=[as_str(x) for x in (raw.get("object_levels") or [])],
            engine=raw.get("engine") or {},
            inputs=list(raw.get("inputs") or []),
            preconditions=list(raw.get("preconditions") or []),
            procedure=list(raw.get("procedure") or []),
            outputs=list(raw.get("outputs") or []),
            failure_policy=raw.get("failure_policy") or {},
            evidence_policy=list(raw.get("evidence_policy") or []),
            safety_boundary=list(raw.get("safety_boundary") or []),
            test_suite=raw.get("test_suite") or {},
            owner=as_str(raw.get("owner")),
            approver=as_str(raw.get("approver")),
            raw=raw,
        ))
    return SkillRegistry(skills,
                         registry_version=as_str(data.get("registry_version")),
                         source_path=str(p))


def default_registry_path() -> Path:
    """默认技能注册表路径（工程根 skills/skill_registry.json）。"""
    return paths.resource("skills", "skill_registry.json")


def export_skill_cards(registry: SkillRegistry, out_dir) -> List[str]:
    """导出全部 Skill Card 为 Markdown（设计文档 4.6 双层表达）。"""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for s in registry.skills:
        p = d / f"{s.skill_code}_{s.skill_id.replace('.', '_')}.md"
        p.write_text(s.card(), encoding="utf-8")
        written.append(str(p))
    return written
