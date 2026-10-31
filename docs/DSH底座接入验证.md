# 以开源版 DeepSeek Harness 为底座的接入验证

> 结论先行：**已跑通**。开源版 DSH 从源码构建后，通过 `--patch` 挂载
> `tool-hc-agent` 插件，在真实模型会话中调用了 `hc_engine_status`，
> 取回 Python 侧引擎数据并回答了问题。
>
> 验证时间：2026-10-31 20:5x　DSH 版本：`0.2.2-alpha.2`（MIT）

## 一、架构关系

```
DeepSeek Harness（开源版，底座）
   └─ DSH 智能体循环：规划 → 选工具 → 执行 → 汇总
        └─ 工具 tool-hc-agent（TypeScript，Cordis 插件）
             └─ spawn 子进程：python -m hc_agent.bridge <command>
                  └─ 配网承载力智能体（Python）
                       └─ OpenDSS（主算） / pandapower（交叉校核）
```

**数值只来自 Python 侧引擎**，插件只做参数校验、起进程、解析单行 JSON、交给 DSH 渲染。

## 二、证据（真实会话输出，非推测）

命令：

```powershell
$env:HC_PYTHON  = "C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$env:HC_PROJECT = "C:\Users\Chen\Desktop\配网承载力智能体Agent"
$env:DEEPSEEK_API_KEY = "<你的 key>"
cd "$env:USERPROFILE\Desktop\配网承载力\deepseek-harness-开源版"
node apps\cli\lib\bin.js headless --patch .\hc-agent.patch.yml --json `
  "调用 hc_engine_status 工具，然后只回答它返回的 count 数值和两个引擎名称"
```

事件流（节选）：

```json
{"type":"tool_call","tool":"hc_engine_status","input":{}}
{"type":"tool_result","status":"completed",
 "result":"{\n  \"count\": 2,\n  \"names\": \"OpenDSS（三相不平衡潮流）、pandapower（正序平衡（交叉校核））\",\n  ..."}
{"type":"final","reason":{"kind":"completed"},
 "text":"count = 2\n两个引擎名称：OpenDSS（三相不平衡潮流，主算）、pandapower（正序平衡，交叉校核）。"}
```

这三行说明整条链都通了：**DSH 选工具 → 工具起 Python → 引擎返回 → 模型据此作答**。

## 三、可复现的完整步骤

### 1. 准备源码与依赖

```powershell
$env:Path = "C:\Program Files\nodejs;$env:LOCALAPPDATA\Programs\MinGit\cmd;" + $env:Path
cd "$env:USERPROFILE\Desktop\配网承载力\deepseek-harness-开源版"
corepack pnpm install          # 约 12 秒
```

> `pnpm` 不在 PATH 时用 `corepack pnpm`，**不要** `corepack enable`（会 EPERM，需管理员）。

### 2. 放入插件

```powershell
# 从本仓库同步（integrations/dsh-plugin → DSH 源码树）
$pkg = "packages\tool\tool-hc-agent"
New-Item -ItemType Directory -Force "$pkg\src" | Out-Null
Copy-Item integrations\dsh-plugin\package.json "$pkg\package.json" -Force
Copy-Item integrations\dsh-plugin\tsconfig.json  "$pkg\tsconfig.json"  -Force
Copy-Item integrations\dsh-plugin\src\index.ts   "$pkg\src\index.ts"   -Force
Copy-Item integrations\dsh-plugin\hc-agent.patch.yml .\hc-agent.patch.yml -Force
```

### 3. 构建

```powershell
# 解包出来的源码没有 .git，构建脚本要读 commit hash，用环境变量顶上
$env:DSH_CLIENT_COMMIT_HASH = "<任意 7 位十六进制>"
corepack pnpm build            # 10 个 gate，约 30 秒
```

产物：`packages/tool/tool-hc-agent/lib/index.js`。

### 4. 挂载并运行

见上面「二、证据」的命令。`hc-agent.patch.yml` 内容：

```yaml
- insert:
    - id: tool-hc-agent
      name: "./packages/tool/tool-hc-agent/lib/index.js"
```

## 四、过程中的四个坑（都踩过）

| 坑 | 现象 | 处理 |
|---|---|---|
| `build:check` 不覆盖新包 | 故意注入类型错误，`pnpm build:check` 仍 PASS | 新包要加进 `tsconfig.host.json` 的 `references`，并**用 `tsc -b <包>/tsconfig.json` 单独验** |
| 输出 schema 类型不兼容 | `TS2322 ... not assignable to '{ required?: true }'` | 独立常量要加 `as const`，否则 `required: true` 被推成 `boolean` |
| 构建脚本调 git | `spawnSync git ENOENT` | 把 MinGit 加进 PATH；且设置 `DSH_CLIENT_COMMIT_HASH` 绕过 `git rev-parse` |
| `cmd` 的 `set` 吃空格 | `DSH_CLIENT_COMMIT_HASH` 变成 `"a644e98 "` | 用 `set "VAR=value"` 的引号写法 |

## 五、尚未做的

- **挂到你日常使用的那个 DSH 实例**：需要在 `~/.dsh/profiles/desktop/cordis.patch.yml`
  里加一条 insert，并把插件装进该 profile 的 node_modules。这会改动正在用的环境，
  **需先备份**（本仓库不代替你做这个决定）。
- 更多工具的实调用验证（目前只逐一验证到 `hc_engine_status` 与 `hc_run_acceptance`）。
