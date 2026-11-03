"""hc_agent — 配网承载力智能体Agent。

设计依据：《配网承载力与主配微协同优化 Agent 设计文档》
  - 第 3 章 总体架构（单监督 Agent + 专业技能 + 规则策略 + 计算引擎）
  - 第 4 章 Agent 核心设计与技能注册（SK-01 .. SK-12）
  - 第 8 章 OpenDSS 计算引擎与 Python 调用设计
  - 第 9 章 可信验证、复算机制与证据链
  - 第 17 章 任务实施流程与文件接口（CLI 契约）
  - 第 20-21 章 主配微协同优化模型与执行流程

铁律（来自设计文档 4.9）：
  1. 监督 Agent 只编排，不修改技能步骤、规则参数或计算结果。
  2. 所有潮流/电压/载流/短路数值只能来自 OpenDSS 适配器。
  3. 同一任务必须冻结 snapshot_id / rule_package / skill_version /
     engine_version，任一变化即产生新 run_id。
  4. 结果解释必须来自结构化结果与证据引用；矛盾时阻断发布。
"""

__version__ = "1.1.0"
__schema_version__ = "hc-input-v1"
__rules_version__ = "hc-rules-v1"
