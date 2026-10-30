# HC.POWERFLOW.BASELINE@1.0.0  基线潮流计算

**目的**：在冻结模型与场景下计算全时间片基线潮流并返回收敛状态与约束结果。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 high

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `Master.dss 及引用文件`（-）（必填）
- `scenarios.json`（-）（必填）
- `task.solver`（-）（必填）  max_iter、tolerance、timeout_s

**前置条件**：
- 拓扑校验通过且无阻断缺陷
- 引擎版本与锁定版本一致
- 电压基准由 CalcVoltageBases 依元件额定值推导（不得下发 Set voltagebases）

**执行步骤**：
1. 新建独立 OpenDSS 上下文与独立工作目录。
2. Compile 经审核的 Master.dss。
3. 设置该场景的开关、分接与控制。
4. 逐时间片写入负荷 P/Q 与光伏出力参数。
5. Solution.Solve() 并检查 Solution.Converged()。
6. 按母线节点与设备端子提取电压、电流、功率。
7. 执行静默失效检测：Converged 为真但全网电压恒为 0 时必须判为无解。
8. 保存原生模型、参数与输出。

**输出**：
- `bus_results.csv`（V/pu/deg）  scenario_id,timestamp,node_id,phase,v_v,v_pu,angle_deg,converged
- `element_results.csv`（kW/kvar/A/%）  明确端子方向；分相量与三相合计分列
- `convergence_status`（-）
- `engine_fingerprint`（-）

**异常处理**：
- retryable: 引擎临时错误, 依赖加载失败
- needs_data: 关键参数缺失
- needs_human: 需改变业务边界或网络状态才能收敛
- forbidden: 通过关闭必要控制、改变拓扑或放宽业务约束使结果表面收敛, 把不收敛记为可行或不可行

**证据要求**：
- snapshot_hash
- engine_run_id
- raw_result_uri
- engine_fingerprint
- opendss_adapter.log

**安全边界**：
- 只读分析
- 不得下发控制命令
- 不得覆盖原始数据
- 不得执行用户上传的任意 DSS 脚本

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002, IEEETestCases/13Bus
- negative_cases: 不收敛算例, 电压基准错误, 电源未接入

**责任人/审批人**：计算引擎组 / 配网专业复核人