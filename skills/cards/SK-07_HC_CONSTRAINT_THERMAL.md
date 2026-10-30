# HC.CONSTRAINT.THERMAL@1.0.0  设备载流与反向潮流校核

**目的**：按设备允许能力检查线路、变压器载流率，并按批准边界检查反向潮流。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 medium

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `element_results.csv`（A/kW）（必填）
- `equipment_limits.csv`（A/kW）（必填）
- `rules.yaml:THERMAL_LIMIT`（pct）（必填）
- `rules.yaml:REVERSE_FLOW_LIMIT`（kW）（必填）

**前置条件**：
- 设备允许载流值 rating_a 齐备
- 反向潮流判定的端子方向已确认

**执行步骤**：
1. 取元件端子 1 的各相电流与额定值，载流率 = 100×I/I_rated。
2. 只按端子 1 的最大相电流判定，不得跨端子累加（会重复计入）。
3. 缺少设备允许值时标 unknown，不得判为 pass。
4. 反向潮流只对输电类元件（line/transformer/reactor）判定。
5. 端子 1 P>0 表示功率倒送回 bus1，即反向潮流；反向功率 = max(0, P)。
6. 注入型元件（PVSystem/Generator）是反向潮流成因而非承载通道，不参与判定。
7. 余量统一换算至电流或功率口径，不同量纲不得混比。

**输出**：
- `current`（A）
- `rating`（A）
- `loading`（pct）
- `margin`（pct）
- `max_reverse_flow`（kW）
- `status`（enum）

**异常处理**：
- needs_data: 设备额定载流值缺失
- needs_human: 反送边界未批准
- forbidden: 用电压推算额定电流, 内置未经确认的载流常数

**证据要求**：
- constraint_id
- rating_a
- equipment_limits.csv
- sign_convention

**安全边界**：
- 只读分析
- 不得改变原始计算值

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 过载, 临界负载, 反向潮流, 额定值缺失

**责任人/审批人**：配网专业组 / 配网专业复核人