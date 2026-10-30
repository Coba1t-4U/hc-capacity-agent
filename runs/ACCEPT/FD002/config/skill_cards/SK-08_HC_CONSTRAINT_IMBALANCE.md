# HC.CONSTRAINT.IMBALANCE@1.0.0  三相不平衡校核

**目的**：由三相复电压求序分量，按批准定义计算不平衡度并定位最不利相与时段。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder；状态 approved；风险等级 medium

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `bus_results.csv`（V/deg）（必填）  需要三相复电压（幅值+相角）
- `rules.yaml:IMBALANCE_LIMIT`（pct）（必填）

**前置条件**：
- 相别映射经校验：导体编号不等于业务相别的假设必须验证
- 同一母线同一时刻三相齐全

**执行步骤**：
1. 按 (scenario,bus,timestamp) 聚合 A/B/C 三相复电压。
2. 求序分量：V1=(Va+aVb+a²Vc)/3，V2=(Va+a²Vb+aVc)/3，a=1∠120°。
3. 不平衡度 = 100×|V2|/|V1|。
4. 仅幅值不能完整求该指标；缺相角时标 unknown。
5. 若业务采用电流幅值偏差等其他定义，须使用不同 metric_id 与独立规则。
6. 输出最不利母线、时段与三相幅值明细。

**输出**：
- `imbalance`（pct）
- `phase`（-）
- `threshold`（pct）
- `time`（-）
- `metric_definition`（-）

**异常处理**：
- needs_data: 三相电压不全, 相角缺失
- needs_human: 不平衡定义口径需确认
- forbidden: 混用不同的不平衡定义, 用幅值近似替代序分量计算

**证据要求**：
- metric_definition
- constraint_id
- bus_results.csv

**安全边界**：
- 只读分析
- 模型不支持中性线时不得宣称已覆盖全部低压不平衡风险

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 三相不平衡越限, 缺相, 相别映射错误

**责任人/审批人**：配网专业组 / 配网专业复核人