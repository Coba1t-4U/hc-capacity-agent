# HC.ACCEPTANCE.MATRIX@1.0.0  验收指标矩阵

**目的**：按开发文档 11.2 的九条指标，判断一次运行是否达到验收门槛，并给出可追溯依据。

**适用范围**：全部业务场景；只读技能；任意权限级别。

**是否必须专业引擎**：否

**输入**：
- `run_dir`（路径）（必填）

**前置条件**：
- 运行目录存在

**执行步骤**：
1. 逐条评估 9 项指标，每项都从**具体产物**取数：
   | 指标 | 门槛 | 数据来源 |
   |---|---|---|
   | 评估覆盖（约束类数） | ≥5 类 | `constraint_results.csv` 的 `constraint_id` 去重 |
   | 红黄绿一致率 | ≥90% | **需要专家标签** → unavailable |
   | 可开放容量偏差 | ≤3% | `metric_diff.csv` 的 `status` |
   | 跨版本复现 | 完全一致 | **需要多版本运行** → unavailable |
   | 独立复算通过率 | 100% | `verification.json` 的 `pass` |
   | 原始数据字节保护率 | 100% | `verification.json` 的 `input_match` |
   | 可追溯率 | 100% | 审计链校验 + 6 个证据文件是否齐备 |
   | 适配引擎数 | ≥2 种 | `manifest.engine_fingerprint.engine_id` |
   | 单台区耗时降幅 | ≥50% | **需要人工基准** → unavailable |
2. 统计四态分布：pass / fail / unknown / unavailable。
3. 附发布门禁状态（`review/release_gate.json`）。

**输出**：
- `items[]`（每项含 name/target/value/status/detail）
- `summary`（各状态计数）、`gate`（门禁是否通过、未通过项）

**异常**：
- 产物缺失 → 该指标 `unknown`，**不得记为 pass**。
- 整个运行目录不存在 → 返回 `ok=false`。

**证据**：每项的 `detail` 写明取数依据。

**边界**：
- **"无数据"不等于通过**。四态必须如实区分，否则看板会变成自我安慰。
- 需要专家标签或人工基准的指标一律 `unavailable`，**不猜、不估算**。
- 本技能只评估，不改变任何产物。

**测试样例**：
| 场景 | 期望 |
|---|---|
| FD002 | 覆盖 5 类达标、复算通过达标、数据保护达标、可追溯达标；适配引擎数 1 为**未达标**；3 项 unavailable；1 项 unknown |
| 无 constraint_results.csv | 覆盖项为 unknown，不是 fail |
| 目录不存在 | `ok=false` |
