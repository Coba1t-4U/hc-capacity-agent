# 运行监控台（前端页面）

本目录是配网承载力与主配微协同优化 Agent 的**只读运行监控台**：
把 `runs/` 下的运行产物渲染成可视化页面，用于复核、汇报与审计抽查。

## 桌面快捷方式（推荐）

已在桌面创建两个快捷方式，双击即用：

| 快捷方式 | 行为 | 适用场景 |
|---|---|---|
| **配网承载力监控台（离线查看）** | 直接用默认浏览器打开单文件版（数据已内联） | 快速查看；不需要服务器、不依赖 Python、可发给他人 |
| **配网承载力监控台（刷新并查看）** | 先重新导出 `runs/` 最新产物，再起本地服务并打开 | 刚跑完任务、要看最新结果 |

- 「刷新并查看」会打开一个控制台窗口；**关闭该窗口即停止服务**。
- 若 8778 端口已有服务在跑，它会直接复用，不再启第二个。

## 手动运行

```powershell
$py = "C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
cd C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project

# 1) 由运行产物生成前端数据包（每次跑完任务后重跑一次即可）
& $py tools/export_dashboard.py --root runs/ACCEPT --out web/data.json

# 2) 起本地静态服务器
& $py -m http.server 8778 --directory web --bind 127.0.0.1
```

然后浏览器打开 <http://127.0.0.1:8778/>。等价于直接双击 `web\打开监控台.ps1`。

> **为什么必须用 http 而不能双击 index.html**：页面通过 `fetch()` 读取同目录的
> `data.json`，浏览器的 `file://` 同源策略会拒绝该请求。需要离线单文件时请用
> `dashboard_standalone.html`（数据内联，无此限制）。

支持深链到具体算例：<http://127.0.0.1:8778/#FD002>

## 两个易踩的坑（均已修复）

1. **`.bat` 存中文会乱码**：`cmd.exe` 按 ANSI/OEM 代码页解析批处理文件，UTF-8
   的中文注释会被当成命令执行并报错。因此启动器用 **PowerShell（`.ps1`）**，
   它原生按 UTF-8 读取（本机为 Windows PowerShell 5.1，脚本按 5.1 兼容编写）。
2. **导出必须用绝对路径**：快捷方式的工作目录是 `web\`，若传相对路径
   `runs\ACCEPT`，会被解析成 `web\runs\ACCEPT`（不存在），导出器会**静默写出
   0 个算例的空数据包且退出码仍为 0** —— 页面打开后一片空白却没有任何报错。
   启动器现已改用绝对路径，并显式校验导出的算例数，把这类静默失败变成可见警告。

## 页面内容

| 区块 | 展示内容 | 数据来源 |
|---|---|---|
| 算例总览 | 4 个算例的容量、分级、复算结论、主导限制 | 各算例 `run_summary.json` |
| 结论 KPI | 物理/管理容量、约束通过·越限·unknown、复算结论 | `run_summary.json` |
| 容量搜索轨迹 | 每个试探点的 ΔP、可行/不可行/unknown、绑定的约束、通过·越限计数；可行下界与不可行上界 | `search_trace.csv` |
| 并网申请校核 | 申请号、结论候选、存量越限、**申请引入的新增越限** | `access_check.json` |
| 五类强制约束 | 电压/载流/反送/不平衡/短路的通过·越限·unknown 及构成条 | `constraint_results.csv` |
| 越限明细 | 逐条列出对象、场景、时刻、计算值、限值、余量 | `constraint_results.csv` |
| 可信验证与追溯 | 独立复算结论与差异数、发布门禁三道门状态、快照/规则/技能/引擎版本与输入摘要 | `verification.json`、`release_gate.json`、`manifest.json` |
| 风险与缺口 | 基线问题、unknown 试探点、未覆盖能力 | `run_summary.json` |
| 主配微研究 | 容量边界逐点结果、各区域新增容量、接口协调轨迹、非预见性与能量守恒检查、范围声明 | `capacity_frontier.csv`、`interface_trace.csv`、`research_summary.json` |

## 设计约束（与主体工程一致）

1. **不编造任何数值。** 页面显示的每个数字都来自运行产物文件；
   取不到的字段显示为 `未获取`，绝不用 0 或估算值填充。
   `data.json` 中对应字段为 `null`，前端统一走同一套 null 处理。
2. **状态原样透出。** `unknown` 不会被当成通过，也不会被当成不可行；
   灰色分级、未通过的门禁、未提交的审批都如实显示。
3. **结论可回链。** 每个算例都展示 snapshot / topology / rule / skill /
   engine / input_digest，使页面上的结论能追到数据快照、规则版本与引擎输出。
4. **口径一致。** 同一算例的约束统计只用一个口径。单笔并网审查
   （`access_check`）的统计取自**含申请场景**，页面并标注"口径"，
   避免出现 KPI 与校核面板两套互相矛盾的数字。

## 离线单文件（可选）

若需把页面连同数据一起交付（例如发给不便起服务的人），用：
```powershell
& $py tools/export_dashboard.py --root runs/ACCEPT --out web/data.json
& $py tools/bundle_single_file.py       # 生成 web/dashboard_standalone.html
```
单文件版把 `data.json` 内联进 HTML，双击即可打开，无需服务器或网络。
页面不引用任何 CDN、字体或外部脚本，完全离线可用。

## 文件说明

| 文件 | 说明 |
|---|---|
| `index.html` | 页面本体（HTML + CSS + 原生 JS，无框架、无外部依赖） |
| `data.json` | 由 `tools/export_dashboard.py` 生成的数据包 |
| `dashboard_standalone.html` | 单文件离线版（可选生成） |
| `_shot/` | 验证用截图（可删除） |

## 验证

```powershell
& $py tools/check_dashboard.py     # 校验内联 JS 语法与 DOM id 引用一致性
```

该检查会解析 `<script>` 内容交给 Node 做语法校验，并比对 JS 中引用的
元素 id 是否都存在于 HTML——仅"服务器返回 200"不能证明页面真的能跑起来。
