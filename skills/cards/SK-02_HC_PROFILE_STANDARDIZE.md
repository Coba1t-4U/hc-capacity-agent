# HC.PROFILE.STANDARDIZE@1.0.0  负荷与光伏曲线标准化

**目的**：把历史与典型曲线统一为带时区的标准时序，并标注质量标签与插值记录。

**适用范围**：业务场景 capacity_assessment/capacity_publication/access_check；对象层级 dt/feeder/substation；状态 approved；风险等级 low

**是否必须专业引擎**：否

**输入**：
- `profiles.csv`（kW/kvar/pu）（必填）  profile_id,resource_id,resource_type,timestamp,phase,p_kw,q_kvar,available_p_pu,quality_flag,source_id

**前置条件**：
- timestamp 为带时区 ISO 8601
- 复合唯一键 profile_id+timestamp+phase 不重复
- p_kw 与 available_p_pu 二选一并声明输入模式

**执行步骤**：
1. 读取 CSV（UTF-8、逗号分隔、首行表头），ID 按字符串保留前导零。
2. 校验时区、resource_type 枚举、phase 枚举。
3. 校验复合唯一键，重复即阻断。
4. 校验同一设备同一时刻不得混用 ALL 与分相值。
5. 校验曲线对时间窗的覆盖率不低于配置阈值。
6. 校验负荷/光伏引用的 profile_id 在文件中存在。
7. 输出规范化长表与质量标签，原始曲线保留、派生曲线另存。

**输出**：
- `profiles[]`（-）
- `distinct_timestamps`（-）
- `coverage`（ratio）  观测时间片 / 期望时间片
- `quality_issues.csv`（-）

**异常处理**：
- needs_data: 连续缺测超过阈值, 覆盖率不足
- needs_human: 时区口径不一致, 单位无法判定
- forbidden: 用 0 代替缺失值, 未经记录的重采样

**证据要求**：
- source_id
- quality_flag
- derived_version_id
- transform_id

**安全边界**：
- 只读分析
- 不得覆盖原始曲线
- 重采样仅生成派生版本并记录方法

**测试样例**：
- golden: tests/golden/FD001/profiles.csv, tests/golden/FD002/profiles.csv
- negative_cases: 缺时区, 重复键, ALL 与分相混用, 覆盖率不足, 单位错误

**责任人/审批人**：数据治理组 / 配网专业复核人