# DSH 插件：配网承载力智能体接入 DeepSeek Harness

本目录是插件的**源码副本**，真正运行时它必须位于 DSH 源码树内：

```
deepseek-harness-开源版/packages/tool/tool-hc-agent/   <- 运行时位置（工作区包）
```

## 为什么这样放

DSH 的 pnpm 工作区 globs 是 `packages/*/*`，插件必须放在那里才能被识别、构建、
被 profile 挂载。本目录只是源码备份，方便随仓库版本管理。

## 同步到 DSH

```powershell
$dsh = "$env:USERPROFILE\Desktop\deepseek-harness-开源版"
$pkg = Join-Path $dsh "packages\tool\tool-hc-agent"
New-Item -ItemType Directory -Force -Path "$pkg\src" | Out-Null
Copy-Item .\package.json "$pkg\package.json" -Force
Copy-Item .\src\index.ts "$pkg\src\index.ts" -Force
```

## 依赖的两个环境变量

| 变量 | 含义 | 缺省 |
|---|---|---|
| `HC_PYTHON` | 跑 hc_agent 的解释器 | `python` |
| `HC_PROJECT` | hc_agent 工程根目录 | 当前工作目录 |

## 注册的 6 个工具

| 工具 | 调用的桥命令 | 用途 |
|---|---|---|
| `hc_engine_status` | `engine_status` | 查看引擎注册表与可用性 |
| `hc_run_acceptance` | `acceptance --run` | 9 条验收指标（四态） |
| `hc_engine_crosscheck` | `crosscheck --run` | pandapower × OpenDSS 交叉校核 |
| `hc_case_analyze` | `case_analyze --case` | OpenDSS 算例单断面分析 |
| `hc_capacity_run` | `capacity_run` | 完整承载力评估 |
| `hc_agent_ask` | `qa --question` | 规则问答（数值来自产物） |

Python 侧实现见 `hc_agent/bridge.py`。

## 已验证 / 未验证

**已验证**（用 tsx 直接加载插件、注入假 ctx、实际调用工具）：

- 插件可被加载：`name`、`inject = ['tools']`、`apply` 均正常
- 6 个工具全部注册成功
- `hc_engine_status` 返回 2 个引擎（OpenDSS 主算 + pandapower 交叉校核）
- `hc_run_acceptance` 对 FD002 返回「评估覆盖 达标：5」

**未验证**（明天要做的）：

- `corepack pnpm build` 把插件编译进 DSH 产物
- 在 profile 的 `cordis.patch.yml` 里挂载，并在真实 DSH 会话中由模型调用

## 新增工具：`hc_business_flow`（2026-10-31）

| 工具 | 桥命令 | 用途 |
|---|---|---|
| `hc_business_flow` | `business_flow [--stage N]` | 业务流八段闭环的实现状态与缺口 |

业务流八段：数据准备 → 评估计算 → 分级发布 → 接入应用 → 提升改造 → 动态更新。
每段如实标注 `已实现 / 部分实现 / 未实现`，未实现的环节（如"发布公示"）
**不会被本系统代做**。状态定义在 `hc_agent/business_flow.py`，
业务口径原文在 `knowledge/business_flow/配网承载力业务流.md`。

自检：`python tests/verify_business_flow.py`（19 项）。
