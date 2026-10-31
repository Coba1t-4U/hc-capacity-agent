/**
 * 配网承载力智能体（hc_agent）接入 DeepSeek Harness 的工具插件。
 *
 * 位置关系：**DSH 是底座**，本插件把 hc_agent 的专业能力注册成 DSH 工具，
 * 由 DSH 的智能体循环调用。数值计算全部发生在 Python 侧，插件只负责
 * 「参数校验 → 起子进程 → 解析单行 JSON → 交给 DSH 渲染」。
 *
 * 与 Python 的约定见 `hc_agent/bridge.py`：
 *   python -m hc_agent.bridge <command> [--run ...]  ->  stdout 一行 JSON
 *
 * 两个环境变量（缺省值见下）：
 *   HC_PYTHON   运行 hc_agent 的 Python 解释器（默认 "python"）
 *   HC_PROJECT  hc_agent 工程根目录（默认当前工作目录）
 *
 * @module @deepseek-ai/dsh-tool-hc-agent
 */

import { spawn } from 'node:child_process'
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'

/** Cordis 插件名。 */
export const name = 'tool-hc-agent'
/** 依赖的宿主服务。 */
export const inject = ['tools']

/** 运行 Python 桥的解释器。 */
function pythonPath(): string {
  return process.env.HC_PYTHON?.trim() || 'python'
}

/** hc_agent 工程根目录。 */
function projectRoot(): string {
  return process.env.HC_PROJECT?.trim() || process.cwd()
}

/** 桥返回的结构。 */
interface BridgeResult {
  ok?: boolean
  text?: string
  error?: string
  [key: string]: unknown
}

/**
 * 执行一次 Python 桥，返回解析后的 JSON。
 *
 * 约定：桥只在 stdout 输出**一行** JSON；失败也在 JSON 里说明，
 * 因此这里把最后一行当结果、其余内容当作噪声丢弃并记录到错误信息里。
 */
function runBridge(args: string[], signal?: AbortSignal): Promise<BridgeResult> {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonPath(), ['-m', 'hc_agent.bridge', ...args], {
      cwd: projectRoot(),
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      signal,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let out = ''
    let err = ''
    child.stdout?.on('data', (chunk: Buffer) => { out += chunk.toString('utf8') })
    child.stderr?.on('data', (chunk: Buffer) => { err += chunk.toString('utf8') })
    child.on('error', (e: Error) => reject(e))
    child.on('close', (code: number | null) => {
      const line = out.trim().split(/\r?\n/).filter(Boolean).pop() ?? ''
      if (!line) {
        reject(new Error(`hc_agent.bridge 无输出（exit=${code}）：${err.slice(0, 400)}`))
        return
      }
      let parsed: BridgeResult
      try {
        parsed = JSON.parse(line) as BridgeResult
      } catch {
        reject(new Error(`hc_agent.bridge 输出不是 JSON（exit=${code}）：${line.slice(0, 400)}`))
        return
      }
      if (parsed.ok === false) {
        reject(new Error(String(parsed.error ?? 'hc_agent.bridge 返回失败')))
        return
      }
      resolve(parsed)
    })
  })
}

/** 统一的输出形状：一段可读文本（DSH 直接渲染）。
 *
 * `as const` 不能省：`required` 必须是字面量 `true`，否则被推断成 `boolean`，
 * 与 DSH 的 `ParameterPropertySpec` 不兼容，报 TS2322（2026-10-31 实测踩过）。
 */
const TEXT_OUTPUT = {
  schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      text: { type: 'string', required: true, description: '面向读者的结果文本' },
    },
  },
  render: (_args: unknown, value: { text: string }) => [{ type: 'text' as const, text: value.text }],
} as const

/** 把 JSON 结果压成一段可读文本，避免把原始 JSON 直接丢给模型。 */
function summarize(payload: BridgeResult): string {
  if (typeof payload.text === 'string' && payload.text.trim()) return payload.text
  const { ok: _ok, command: _c, ...rest } = payload
  return JSON.stringify(rest, null, 2)
}

/**
 * 注册全部 hc_agent 工具。
 * @param ctx - 提供工具注册表的宿主上下文。
 */
export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'hc_engine_status',
    description: '查看配网承载力智能体当前接入了哪些计算引擎（主算 OpenDSS、交叉校核 pandapower）以及各自是否可用。',
    parameters: {},
    output: TEXT_OUTPUT,
    async execute(_args, exec) {
      const r = await runBridge(['engine_status'], exec.signal)
      return { text: summarize(r) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'hc_run_acceptance',
    description: '对某一次承载力运行计算 9 条验收指标，返回四态判定（达标/未达标/无数据/不适用）。「无数据」不等于通过。',
    parameters: {
      run: { type: 'string', description: '运行目录的绝对路径，需包含 results/run_summary.json' },
    },
    output: TEXT_OUTPUT,
    async execute(args, exec) {
      const r = await runBridge(['acceptance', '--run', String(args.run)], exec.signal)
      return { text: summarize(r) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'hc_engine_crosscheck',
    description: '用 pandapower（正序平衡）与 OpenDSS（三相不平衡）对同一次运行做交叉校核，只比量级与极值，不参与容量判定。',
    parameters: {
      run: { type: 'string', description: '运行目录的绝对路径' },
    },
    output: TEXT_OUTPUT,
    async execute(args, exec) {
      const r = await runBridge(['crosscheck', '--run', String(args.run)], exec.signal)
      return { text: summarize(r) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'hc_case_analyze',
    description: '分析一个 OpenDSS 算例（单断面潮流与约束校核），返回收敛状态、电压极值、网损与报告路径。不产出开放容量。',
    parameters: {
      case: { type: 'string', description: '算例目录、Master.dss 路径，或算例名称' },
    },
    output: TEXT_OUTPUT,
    async execute(args, exec) {
      const r = await runBridge(['case_analyze', '--case', String(args.case)], exec.signal)
      return { text: summarize(r) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'hc_capacity_run',
    description: '对一份输入包执行完整承载力评估（冻结输入→校验→基线潮流→容量搜索→独立复算→报告），返回可开放容量与绑定约束。',
    parameters: {
      input: { type: 'string', description: '输入包目录（含 network.xlsx / profiles.csv / rules.yaml / task.json）' },
      output: { type: 'string', description: '运行结果输出目录' },
    },
    output: TEXT_OUTPUT,
    async execute(args, exec) {
      const r = await runBridge(
        ['capacity_run', '--input', String(args.input), '--output', String(args.output)],
        exec.signal,
      )
      return { text: summarize(r) }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'hc_agent_ask',
    description: '用规则引擎回答关于运行记录、约束、容量、口径的问题（不调用大模型，数值全部来自产物）。',
    parameters: {
      question: { type: 'string', description: '自然语言问题，例如「FD002 能接多少」「最低电压是多少」' },
    },
    output: TEXT_OUTPUT,
    async execute(args, exec) {
      const r = await runBridge(['qa', '--question', String(args.question)], exec.signal)
      return { text: summarize(r) }
    },
  }))
}
