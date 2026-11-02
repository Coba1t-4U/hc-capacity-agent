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
cd "$env:USERPROFILE\Desktop\配网承载力\配网承载力智能体Agent\deepseek-harness"
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
智能体工程（GitHub 克隆）   %USERPROFILE%\Desktop\配网承载力\配网承载力智能体Agent
智能体母本（含 runs/dss_runs）C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project
DSH 开源版                  %USERPROFILE%\Desktop\配网承载力\配网承载力智能体Agent\deepseek-harness
Python 解释器               C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
引擎库                      ...\default-workspace\opendss_demo\pylibs、...\pylibs_pandapower
```

---

## 2026-10-31 更新：底座接入**已跑通**

开源版 DSH 从源码构建后，通过 `--patch` 挂载 `tool-hc-agent`，在真实模型会话中
调用了 `hc_engine_status`，拿回 Python 侧引擎数据并作答。证据与复现步骤见
`docs/DSH底座接入验证.md`。

已完成：
- 插件编译进 `lib/index.js`（`tsc -b` 通过，注入错误能报错）
- `corepack pnpm build` 通过（10 gate / 30 秒）
- `--dump-config` 确认插件进 profile 树
- headless 真实会话调用工具成功

仍未做：把插件挂进**日常使用的那个 DSH profile**（需备份，属改动现有环境）。

---

## 2026-10-31 再次更正：那两个目录不是"被删"，是"被移动"

两次"目录消失"（10-30 23:41、10-31 20:5x）的真实情况：它们被**移动**进了
`Desktop\配网承载力\`，所以回收站里当然找不到。我之前判断成"被删除并清空回收站"，
是错的，在此更正。

移动后的位置：

| 东西 | 现在的位置 | 状态 |
|---|---|---|
| DSH 开源版 | `Desktop\配网承载力\配网承载力智能体Agent\deepseek-harness` | 完好（122,983 文件 / 2,360 MB） |
| 智能体工程（旧副本） | `Desktop\配网承载力\配网承载力智能体Agent` | **`.git/objects` 已丢失**，git 不认它是仓库；内容停在 `a644e98`，无独有文件 |
| 智能体工程（权威副本） | `Desktop\配网承载力\配网承载力智能体Agent` | 完好，HEAD `00d531c`，`.git` 完整 |

**结论**：以桌面根目录那份为准（`publish.py` 也指向它）。旧副本可以直接删除，
它没有任何独有文件（已逐文件比对：只在 A 里的文件数 = 0）。

---

## 2026-10-31 晚：业务流八段闭环已进入智能体

- `hc_agent/business_flow.py`：八段状态/缺口的结构化定义（如实标注，非目标状态）
- `knowledge/business_flow/配网承载力业务流.md`：业务口径原文参照
- 问答命中：`业务流是怎样的`、`第5步是什么`、`哪些环节还没做`
- 桥命令：`python -m hc_agent.bridge business_flow [--stage N]`
- DSH 工具：`hc_business_flow`（插件现共 7 个工具，已 build 进 lib/index.js）
- 自检：`tests/verify_business_flow.py` 19 项全过；`verify_qa` 46 项无回归

**注意**：DSH 源码移到 `桌面\配网承载力\` 后，node_modules 需重链；
`corepack pnpm install` 在无 TTY 时会拒绝，需 `$env:CI='true'`。

---

## 2026-11-02：两个工程合并为一个

DSH 开源版已移入智能体工程内部，PyCharm 只需打开 **一个** 工程：

```
Desktop\配网承载力\配网承载力智能体Agent\
├─ hc_agent / tests / tools / docs …   ← 智能体本体（入 git）
└─ deepseek-harness\                   ← 开源版底座（2.3 GB，已 gitignore）
```

**注意**：DSH 目录移动后 pnpm 的 node_modules 链接失效，需要重链一次：

```powershell
$env:CI = 'true'; $env:DSH_CLIENT_COMMIT_HASH = 'a644e98'
cd "$env:USERPROFILE\Desktop\配网承载力\配网承载力智能体Agent\deepseek-harness"
corepack pnpm install
corepack pnpm build
```
