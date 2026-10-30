# 明天从这里继续

> 最后更新：2026-10-30 23:5x（当日收工）
> 本文件是跨会话交接单，**下次开工先读这一页**。

## 一句话现状

智能体代码已在 GitHub（`Coba1t-4U/hc-capacity-agent`，775 文件）；
「以开源版 DeepSeek Harness 为底座」这件事**做到了第 1 步**：
Python 侧桥接口已写完并实测通过，**DSH 侧的 TS 插件还没写**。

## 已完成（可验证）

| 项 | 证据 |
|---|---|
| GitHub 仓库 | https://github.com/Coba1t-4U/hc-capacity-agent（public，`main`） |
| 提交身份 | `Coba1t-4U <Coba1t-4U@users.noreply.github.com>` |
| SSH 通道 | `github.com` → `ssh.github.com:443`（`~/.ssh/config`，无 BOM） |
| 本机 git | 便携版 MinGit 2.56.0，在 `%LOCALAPPDATA%\Programs\MinGit`，已入用户 PATH |
| 双引擎 | OpenDSS（主算）+ pandapower（交叉校核），验收矩阵「适配引擎数」= 2 达标 |
| **Python 桥** | `hc_agent/bridge.py`，6 个命令，实测 `engine_status` / `acceptance` 均返回正常 JSON |
| DSH 源码 | 桌面 `deepseek-harness-开源版`，v0.2.2-alpha.2，MIT，20219 文件 / 134.7 MB |
| DSH 工具链 | Node v24.21.0、`corepack pnpm` 11.28.5（pnpm 不在 PATH，须用 `corepack pnpm`） |

## 明天要做的三件事（按顺序）

### 1. 写 DSH 侧插件（核心未完成项）

目标包：`deepseek-harness-开源版/packages/tool/tool-hc-agent/`

DSH 插件写法（已从仓库真实代码抄来，`packages/session/tool-working-directory/src/index.ts`）：

```ts
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

export const name = 'tool-hc-agent'
export const inject = ['tools']

export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'hc_engine_status',
    description: '…',
    parameters: { /* 每个字段描述要写清 */ },
    output: {
      schema: { type: 'object', additionalProperties: false,
                properties: { text: { type: 'string', required: true } } },
      render: (_a, v) => [{ type: 'text', text: v.text }],
    },
    async execute(args, exec) { /* spawn python -m hc_agent.bridge ... */ },
  }))
}
```

要注册的工具（与 bridge.py 的 6 个命令一一对应）：

| 工具名 | 调用的桥命令 |
|---|---|
| `hc_engine_status` | `engine_status` |
| `hc_run_acceptance` | `acceptance --run <dir>` |
| `hc_engine_crosscheck` | `crosscheck --run <dir>` |
| `hc_case_analyze` | `case_analyze --case <path>` |
| `hc_capacity_run` | `capacity_run --input <dir> --output <dir>` |
| `hc_agent_ask` | `qa --question <text>` |

`package.json` 照 `packages/session/tool-working-directory/package.json` 抄，
改 `name` / `description` / `repository.directory`，peerDependencies 只需
`@deepseek-ai/cordis` 与 `@deepseek-ai/dsh-tools`。

工作区 globs 已含 `packages/*/*`，放进去就会被 pnpm 识别。

### 2. 重装 DSH 依赖并构建

桌面 DSH 目录是**重新解包的，node_modules 没了**：

```powershell
$env:Path = "C:\Program Files\nodejs;" + $env:Path
cd "$env:USERPROFILE\Desktop\deepseek-harness-开源版"
corepack pnpm install          # 上次 45.6 秒
corepack pnpm build            # 或只构建该包
```

验证语法可以先用 `corepack pnpm exec tsx <插件入口>` 快速跑一遍。

### 3. 挂载并实测（这一步最容易卡）

- 挂载方式：profile 的 `cordis.patch.yml` 加一条 insert（参考
  `C:\Users\Chen\.dsh\profiles\desktop\cordis.patch.yml` 现有格式），
  **先复制一份 profile 做备份**，改坏了能回退。
- 环境变量：插件需要知道 Python 路径与工程根，建议
  `HC_PYTHON` / `HC_PROJECT`（bridge.py 默认走当前解释器）。
- 实测：在 DSH 里问「当前接了哪些计算引擎」，看是否调到 `hc_engine_status`。

## 需要记住的坑（已踩过，别重踩）

1. **`github.com:443` 不通** —— 所有 git 操作必须走 `ssh.github.com:443`。
   `~/.ssh/config` 必须是 **UTF-8 无 BOM**，否则 Git 自带 ssh 报
   `Bad configuration option: \357\273\277#`。
2. **`corepack enable pnpm` 会 EPERM**（要写 `C:\Program Files\nodejs`）。
   用 `corepack pnpm ...` 直接代理，不要 enable。
3. **DSH 源码不能直接 `git clone`** —— SSH 克隆 337 MB 会卡死在 0 MB。
   用 `curl https://codeload.github.com/deepseek-ai/deepseek-harness/tar.gz/refs/heads/master`
   （38.5 MB，约 4 秒）。
4. **解包必须用 `tools/_extract_dsh.py`**，不能用 `tar`：仓库有 14 个符号链接，
   bsdtar 在非管理员下会 `Invalid argument` 中断，只解出 20%。
5. **不要用 `Set-Content -Encoding UTF8` 写配置文件**（PS 5.1 带 BOM）。
6. **桌面曾有两个目录被删进回收站**（2026-10-30 23:41），随后回收站被清空。
   代码没丢是因为 GitHub 有备份 —— 这也是「先推再干活」的价值。

## 今天没做、明天要不要做

- **`.venv`**：桌面工程是重新 clone 的，**没有 .venv 了**。要用 PyCharm 跑，
  需重建：`python -m venv --system-site-packages .venv`（继承运行时依赖，免联网安装）。
- **PyCharm 工程**：重新 clone 后 `.idea` 在仓库里，直接打开即可；
  解释器需在 PyCharm 里重新指向新的 `.venv`。
- **`hc_agent_ui/node_modules`（259 MB）**：从未纳入仓库，也别纳入。
- **PyCharm 试用只剩几天**（截图右上角显示 5 天），到期不影响代码与仓库。

## 关键路径速查

```
智能体工程（GitHub 克隆）   %USERPROFILE%\Desktop\配网承载力智能体Agent
智能体母本（含 runs/dss_runs）C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project
DSH 开源版                  %USERPROFILE%\Desktop\deepseek-harness-开源版
Python 解释器               C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
引擎库                      ...\default-workspace\opendss_demo\pylibs、...\pylibs_pandapower
```
