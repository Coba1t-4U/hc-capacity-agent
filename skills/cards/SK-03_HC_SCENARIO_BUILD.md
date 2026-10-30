# HC.SCENARIO.BUILD@1.0.0  运行场景生成

**目的**：按批准场景集生成典型日、极端时段与检修转供场景，形成场景参数快照。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check；对象层级 dt/feeder/substation；状态 approved；风险等级 low

**是否必须专业引擎**：否

**输入**：
- `profiles.csv`（kW/kvar/pu）（必填）
- `rules.yaml:scenarios`（-）（必填）
- `scenario_set.json`（-）（可选）  已登记场景集；提供时按其定义切片

**前置条件**：
- 时间窗内至少存在一个时间片
- 场景权重与拓扑引用完整

**执行步骤**：
1. 解析场景集定义；未登记时生成基准典型日场景（使用真实曲线）。
2. 按 rule_ref 指定的场景类别派生极端场景，取值一律来自真实曲线。
3. 每个时间片先应用开关/分接状态，再写入负荷 P/Q 与已有光伏出力。
4. 同时率如已体现在曲线中不得再次相乘。
5. 输出场景集、参数快照与适用范围。

**输出**：
- `scenarios.json`（-）  scenario_id,topology_ref,profile_ref,time_window,control_ref

**异常处理**：
- needs_data: 时间窗内无时间片
- needs_human: 业务要求未登记的运行方式
- forbidden: 为凑场景虚构曲线数据

**证据要求**：
- scenarios.json
- profile_ref
- topology_ref

**安全边界**：
- 只读分析
- 不得改变业务口径

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 空时间窗, 场景引用缺失

**责任人/审批人**：Agent/平台研发组 / 配网专业复核人