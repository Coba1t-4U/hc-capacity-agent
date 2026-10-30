# HC.REMEDIATION.FROM_RUN@1.0.0  按运行产物出整改候选

**目的**：从一次已完成的运行里直接读出约束状态并给整改候选，避免人工转录错误。

**适用范围**：全部业务场景；权限要求 **候选生成（L2）**。

**是否必须专业引擎**：否

**输入**：
- `run_dir`（路径）（必填）

**前置条件**：
- 运行目录下至少有 `results/run_summary.json` 或 `run_outcome.json`

**执行步骤**：
1. 依次尝试读 `results/run_summary.json`、`run_outcome.json`。
2. 取出 `binding_constraint`、`binding_detail`、`voltage_violations`、`overloaded`。
3. 转交 `HC.REMEDIATION.PROPOSE` 的候选匹配逻辑。
4. 输出候选与免责声明。

**输出**：
- `candidates[]`、`binding_constraint`、`disclaimer`

**异常**：
- 两个文件都读不到 → `ok=false` 并说明"读不到该运行的结论"，
  **不返回空候选冒充成功**。

**证据**：候选 ID + 运行的 `run_id`。

**边界**：同 SK-13（不给容量数字、不做经济性、不做选型）。

**测试样例**：
| 输入 | 期望 |
|---|---|
| FD002 运行目录 | `VOLTAGE_LIMIT`，命中 3 条电压类候选 |
| 不存在的目录 | `ok=false`，原因明确 |
