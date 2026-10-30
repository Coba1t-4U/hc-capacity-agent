# HC.CONSTRAINT.VOLTAGE@1.0.0  节点电压越限校核

**目的**：按节点、相别、时间片检查电压幅值、持续时间与变化量，输出越限清单与最不利节点。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 medium

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `bus_results.csv`（pu）（必填）
- `rules.yaml:VOLTAGE_LIMIT`（pu）（必填）

**前置条件**：
- 电压 pu 的相电压基准与模型 kVBase 对齐
- 规则包中 min/max 与 duration_minutes 已批准（非 null）

**执行步骤**：
1. 按 (scenario,node,phase) 收集时间序列。
2. 排除 OpenDSS 内部等值母线（sourcebus），它不是被评估的网络节点。
3. 求最小值与最大值对应的时间片。
4. 按规则 operator 与限值判定，并按相邻时间戳差值累加越限持续时间。
5. 持续时间未达配置阈值时不计为越限。
6. 输出 v_min/v_max、节点、持续时间、余量。

**输出**：
- `v_min`（pu）
- `v_max`（pu）
- `node`（-）
- `duration_minutes`（min）
- `margin`（pu）
- `status`（enum）

**异常处理**：
- needs_data: 电压结果缺失
- needs_human: 阈值未批准
- forbidden: 内置未经确认的电压常数, 用经验阈值替代规则

**证据要求**：
- constraint_id
- limit_source=rule_package
- bus_results.csv

**安全边界**：
- 只读分析
- 不得改变原始计算值

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 越上限, 越下限, 临界, 持续时间不足

**责任人/审批人**：配网专业组 / 配网专业复核人