# HC.ACCESS.CHECK@1.0.0  并网申请方案校核

**目的**：对具体并网申请组织基线与增量校核，输出可行性候选、限制因素与整改方向。

**适用范围**：业务场景 access_check/remediation_review；对象层级 dt/feeder；状态 approved；风险等级 high

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `application.json`（kW/kV）（必填）
- `task.business_scene`（-）（必填）  必须为 access_check 或 remediation_review

**前置条件**：
- 申请材料关键字段完整
- 前置判定通过：重复申请、接入点存在性、容量单位、设备归属、快照完整性、基线收敛

**执行步骤**：
1. 解析并结构化申请；关键字段缺失则生成补充清单并暂停。
2. 依据规则生成候选接入点与允许的方案变量，不得虚构设备或接线。
3. 使用申请容量（而非容量搜索试探值）执行基线与增量校核。
4. 执行潮流、电压、载流量、三相不平衡与短路容量校核，定位新增申请引起的差异。
5. 区分存量问题与新增变化，不得一概推导为申请导致违规。
6. 基于真实约束结果生成整改方向；建议仅供专业人员复核。
7. 需比较多个接入方案时，各方案分别生成 scenario_set 与 scheme_id。

**输出**：
- `decision_candidate`（enum）  可行 / 有条件可行 / 不可行 / 无法评估
- `binding_constraints`（-）
- `baseline_vs_increment`（-）
- `recommended_actions`（-）
- `uncertainties`（-）
- `review_status`（enum）

**异常处理**：
- needs_data: 申请关键字段缺失
- needs_human: 多方案取舍, 例外放行
- forbidden: 自动批准并网, 生成未经计算的可行性结论

**证据要求**：
- application_ref
- constraint_results.csv
- baseline_vs_increment

**安全边界**：
- 只读分析
- 不得作出业务审批决定
- 不得自动批准并网、停电或改接线

**测试样例**：
- golden: tests/golden/FD001
- negative_cases: 字段缺失, 接入点不存在, 重复申请, 存量问题误判为新增

**责任人/审批人**：配网专业组 / 业务主管部门