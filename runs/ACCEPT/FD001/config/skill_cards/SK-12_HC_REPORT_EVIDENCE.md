# HC.REPORT.EVIDENCE@1.0.0  证据包与专业报告生成

**目的**：将任务清单、结果、规则、复算与审批信息组织为报告与可审计证据包。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 medium

**是否必须专业引擎**：否

**输入**：
- `run_summary.json`（-）（必填）
- `verification.json`（-）（必填）  缺失时报告须声明尚未独立复算，禁止发布
- `manifest.json`（-）（必填）

**前置条件**：
- 结果哈希已计算
- 报告所有数字可回链至数据快照、规则版本或引擎输出

**执行步骤**：
1. 汇集运行摘要、容量、约束、搜索轨迹、质量缺陷。
2. 按附录 B 的九个章节组织报告。
3. 逐项执行回链检查：无法回链的内容不得进入正式报告。
4. 计算 result_hash 供审批绑定。
5. 生成 Word 报告；不可用时退化为 Markdown。
6. 封存证据包（inputs/config/model/results/logs/verification/review/audit）。

**输出**：
- `report.docx`（-）
- `evidence.zip`（-）
- `traceability`（-）  回链完整性检查
- `result_hash`（-）

**异常处理**：
- retryable: 报告生成失败可重试
- needs_human: 回链缺失需补正
- forbidden: 把无法回链的内容写入正式报告, 在证据包中保存密钥或令牌

**证据要求**：
- result_hash
- package_digest
- audit events

**安全边界**：
- 只读分析
- 不得修改已封存的正式证据

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 缺复算结论, 回链缺失

**责任人/审批人**：Agent/平台研发组 / 配网专业复核人