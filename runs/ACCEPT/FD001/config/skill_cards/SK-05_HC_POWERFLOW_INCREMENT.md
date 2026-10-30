# HC.POWERFLOW.INCREMENT@1.0.0  增量接入潮流计算

**目的**：在冻结基线之上注入指定新增容量并计算增量场景，输出基线与增量的差异。

**适用范围**：业务场景 capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 high

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `task.candidate_nodes`（-）（必填）  node_id、phase、weight（和为 1）
- `task.application_ref`（-）（可选）
- `increment_kw`（kW）（必填）

**前置条件**：
- 基线全场景收敛且满足基线准入规则
- 候选接入点在模型中存在
- 分配权重之和为 1

**执行步骤**：
1. 每次从冻结基线复制或重建算例，避免新增容量累积。
2. 按固定分配权重计算各接入点新增容量。
3. 注入新增容量（PVSystem.kVA 为视在容量、Pmpp 为阵列功率，不得默认等同）。
4. 逐场景逐时间片求解并检查收敛。
5. 计算基线/增量差异并定位新增申请引起的约束变化。
6. 失败记为 unknown，不得当作 pass 或不可行。

**输出**：
- `element_results_increment.csv`（kW/kvar/A）
- `baseline_vs_increment`（-）
- `status`（enum）  ok/unknown

**异常处理**：
- retryable: 引擎临时错误
- needs_data: 接入点不存在
- needs_human: 需改变接入位置或控制方式
- forbidden: 把 unknown 当作不可行上界, 复用主任务中间结果

**证据要求**：
- snapshot_hash
- engine_run_id
- increment_kw
- allocation

**安全边界**：
- 只读分析
- 不得虚构设备或接线

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 接入点不存在, 权重和不为 1, 注入不收敛

**责任人/审批人**：计算引擎组 / 配网专业复核人