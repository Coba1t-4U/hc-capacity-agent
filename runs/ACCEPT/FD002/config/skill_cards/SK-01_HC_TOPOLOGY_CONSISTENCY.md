# HC.TOPOLOGY.CONSISTENCY@1.0.0  拓扑与模型一致性检查

**目的**：校验网架连通性、孤岛、环路、相别与版本一致性，生成可计算网络的前提结论。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check/remediation_review；对象层级 dt/feeder/substation；状态 approved；风险等级 low

**是否必须专业引擎**：否

**输入**：
- `network.xlsx:buses`（-）（必填）  节点主键 node_id
- `network.xlsx:lines`（ohm/km）（必填）
- `network.xlsx:transformers`（kVA/kV/%）（可选）
- `network.xlsx:switches`（A）（可选）
- `network.xlsx:sources`（ohm/pu）（必填）
- `network.xlsx:meta.topology_version`（-）（必填）

**前置条件**：
- network.xlsx 的 meta 工作表必需键齐备
- task.snapshot_id 与 meta.snapshot_id 一致
- 对象范围 scope.ids 能解析为节点或馈线

**执行步骤**：
1. 校验外键：lines/transformers/loads/pv 的节点引用必须存在于 buses。
2. 校验开关状态与有效时间，断开开关不得构成供电路径。
3. 建图（networkx，不可用时退化为邻接表），求连通分量。
4. 标记无供电路径至 sources 的孤岛节点为阻断缺陷。
5. 从 sources 出发做可达性遍历，校验 scope 与候选接入点可达。
6. 检测多电源边界并记录运行方式确认要求。
7. 校验设备相别不超过节点相别。

**输出**：
- `components`（-）  连通分量列表
- `islands`（-）  孤岛节点，blocking
- `unreachable`（-）
- `multi_source_boundaries`（-）
- `quality_issues.csv`（-）  issue_id/file/row/field/severity/code/blocking

**异常处理**：
- retryable: 临时文件读取失败
- needs_data: 节点或线路字段缺失
- needs_human: 拓扑与现场台账不符, 开关状态时标冲突
- forbidden: 自动补全缺失的网架连接, 猜测相别

**证据要求**：
- snapshot_hash
- topology_version
- quality_issues.csv

**安全边界**：
- 只读分析
- 不得修改源数据
- 不得推断不存在的设备或接线

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 拓扑孤岛, 开关状态矛盾, 重复 node_id, 版本冲突

**责任人/审批人**：数据治理组 / 配网专业复核人