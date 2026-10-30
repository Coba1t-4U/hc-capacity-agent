# HC.CONSTRAINT.SHORTCIRCUIT@1.0.0  短路容量与保护触发校核

**目的**：校验新增电源后短路水平与设备开断能力，输出短路电流与保护复核触发标志。

**适用范围**：业务场景 capacity_assessment/access_check；对象层级 feeder/substation；状态 approved；风险等级 high

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `fault_models.csv`（-）（必填）  逆变器故障电流模型与有效期；缺少模型则相关短路能力 unknown
- `rules.yaml:SHORT_CIRCUIT_LIMIT`（A）（必填）
- `零序参数 r0/x0`（ohm/km）（必填）

**前置条件**：
- 短路与零序参数完整
- 故障类型、故障电阻、接地方式、上级电源阻抗与分布式电源故障模型已冻结

**执行步骤**：
1. 设置 Set mode=FaultStudy 并求解，读取母线等值量。
2. 明确记录：FaultStudy 的母线等值量不能直接当成任意接地或相间故障的实际电流。
3. 按故障方案新建显式 Fault 元件求解具体故障电流，读取对应端子结果。
4. 与设备开断能力及系统边界比较。
5. 缺少故障模型或零序参数时标 unknown（能力未覆盖）。
6. 恢复 Set mode=snapshot 并记录状态。

**输出**：
- `fault_level`（A）
- `rating`（A）
- `margin`（A）
- `location`（-）
- `status`（enum）  pass/fail/unknown

**异常处理**：
- retryable: 依赖加载失败
- needs_data: 短路模型或零序参数缺失
- needs_human: 保护配合需专业判断
- forbidden: 把 FaultStudy 等值量当实际故障电流, 把未验证的逆变器故障响应标为已覆盖, 称为保护配合或暂态稳定验证

**证据要求**：
- fault_spec
- bus_isc
- zsc
- fault_element_currents

**安全边界**：
- 只读分析
- 仅稳态短路计算
- 不含暂态稳定与保护配合验证

**测试样例**：
- golden: IEEETestCases（含短路算例）, tests/golden/FD001
- negative_cases: 缺故障模型, 零序参数缺失, 故障元件不收敛

**责任人/审批人**：计算引擎组 / 配网专业复核人