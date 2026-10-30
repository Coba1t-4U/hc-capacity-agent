# HC.CAPACITY.SEARCH@1.0.0  开放容量搜索

**目的**：在指定对象、时间窗、场景、分配与控制权限下搜索使全部强制约束仍满足的最大新增容量。

**适用范围**：业务场景 capacity_publication/capacity_assessment；对象层级 dt/feeder/substation；状态 approved；风险等级 high

**是否必须专业引擎**：是（opendss / OpenDSSDirect.py）

**输入**：
- `task.search`（kW）（必填）  lower_kw、upper_kw、precision_kw、max_iter
- `task.allocation_mode`（-）（必填）
- `rules.yaml:search`（-）（必填）

**前置条件**：
- 基线全场景收敛且满足基线准入规则
- 候选接入点与分配权重已确定
- 单调性已验证或明确采用离散扫描

**执行步骤**：
1. 计算当前基线状态并确认所有基准场景收敛；基线已违规时先输出存量问题。
2. 试探下界；下界不可行则物理容量为 0。
3. 试探上界；上界仍可行只能输出'至少为上界'并按规则扩展搜索，不得宣称找到最大值。
4. 上界不可行才形成可行/不可行括区间。
5. 仅当可行性随增量容量单调已验证且采用固定接入分配时执行二分；否则使用经批准步长的离散全域扫描。
6. unknown（超时/不收敛/短路模型缺失）不得当作不可行上界。
7. 终止时保留可行下界、不可行上界及间隔。
8. 取各约束容量上界的最小值作为物理约束容量，记录主导限制因素。

**输出**：
- `capacity_physical_kw`（kW）  含已并网源基线之上可增加的容量，不再扣一次已并网装机
- `feasible_lower_kw`（kW）
- `infeasible_upper_kw`（kW）
- `bracket_gap_kw`（kW）
- `binding_constraint`（-）
- `search_status`（enum）  ok / at_least_upper_bound / unknown / blocked

**异常处理**：
- needs_data: 搜索上下界或精度非法
- needs_human: 需扩展搜索上界, 需改变接入分配策略
- forbidden: 对上界仍可行宣称已找到最大值, 把 unknown 当作不可行, 把各点独立最大值直接相加

**证据要求**：
- search_trace.csv
- capacity.csv
- result_hash
- monotonicity.json

**安全边界**：
- 只读分析
- 不得使用未批准的管理口径

**测试样例**：
- golden: tests/golden/FD001, tests/golden/FD002
- negative_cases: 上界仍可行, 非单调搜索, unknown 试探点, 基线已违规, 多点独立上限相加

**责任人/审批人**：Agent/平台研发组 / 配网专业复核人