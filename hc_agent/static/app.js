"use strict";
/* ============================================================
   配网承载力 Agent — 桌面端渲染层
   视觉语言对齐天枢一号；数据全部来自后端产物，取不到就显示「未获取」。
   ============================================================ */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

const NA = '<span class="faint">未获取</span>';

/**
 * 口径声明等文本里用了 ``**加粗**`` —— 那是为 Word / Markdown 报告写的。
 * 在网页上要转成 <b> 而不是把星号原样显示出来（实测踩过：表格里出现
 * 字面量 "**不等于**"）。先转义再做最小替换，因此不会有注入风险。
 */
function md(v) {
  return esc(v).replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
}
function num(v, digits = 4) {
  if (v === null || v === undefined || v === "") return NA;
  const n = Number(v);
  if (!isFinite(n)) return NA;
  return Number.isInteger(n) ? n.toLocaleString("en-US")
    : n.toLocaleString("en-US", { maximumFractionDigits: digits });
}
const tag = (text, kind) =>
  `<span class="tag ${kind}">${esc(text)}</span>`;
const dotTag = (text, kind) =>
  `<span class="tag ${kind}"><span class="dot"></span>${esc(text)}</span>`;
const kv = (label, value, mono) =>
  `<div class="kvrow"><span>${esc(label)}</span>` +
  `<b class="${mono ? "mono" : ""}">${value}</b></div>`;

/* ------------------------------------------------ 传输层（Electron / 浏览器两用） */
const IS_ELECTRON = typeof window.hc !== "undefined";
if (!IS_ELECTRON) document.body.classList.add("no-chrome");

async function api(path) {
  if (IS_ELECTRON) {
    const r = await window.hc.apiGet(path);
    if (!r.ok) return { ok: false, error: r.error || ("HTTP " + r.status) };
    try { return { ok: true, data: JSON.parse(r.body) }; }
    catch (e) { return { ok: false, error: "响应不是合法 JSON" }; }
  }
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return { ok: false, error: "HTTP " + res.status };
    return { ok: true, data: await res.json() };
  } catch (e) { return { ok: false, error: String(e.message || e) }; }
}

/* ------------------------------------------------------------------ 状态 */
const state = {
  page: "overview",
  sub: "overview",
  diag: null,
  channels: null,
  runs: [],
  detail: {},
  busy: false,
};

/* ============================================================ 运行总览 */
function subnavItems(page) {
  if (page === "overview") {
    return [
      ["group", "运行时"],
      ["overview", "环境与就绪度"],
      ["reliability", "可靠性设施"],
      ["channels", "引擎通道"],
      ["group", "数据"],
      ["runs", "运行记录"],
    ];
  }
  if (page === "workbench") {
    return [
      ["group", "容量分析"],
      ["howto", "如何发起一次评估"],
      ["scenario", "算例与场景"],
      ["constraints", "约束与阈值"],
    ];
  }
  return [
    ["group", "结果与证据"],
    ["result", "容量结论"],
    ["caliber", "求解口径声明"],
    ["evidence", "证据链与门禁"],
    ["verify", "独立复算"],
  ];
}

function renderSubnav() {
  const nav = $("#subnav");
  if (state.page === "workbench" && state.sub === "howto") {
    // 工作台仍然显示子导航，保持与总览一致的骨架
  }
  nav.classList.remove("hidden");
  nav.innerHTML = subnavItems(state.page).map(([k, label]) =>
    k === "group"
      ? `<div class="subnav-group-title">${esc(label)}</div>`
      : `<div class="subnav-item ${state.sub === k ? "active" : ""}"
              data-sub="${k}">${esc(label)}</div>`
  ).join("");
  $$("#subnav .subnav-item").forEach((el) => {
    el.onclick = () => { state.sub = el.dataset.sub; render(); };
  });
}

/* -------------------------------------------------- 页面 1：运行总览 */
function pageOverview() {
  const d = state.diag;
  if (!d) return loadingCard("运行总览");
  if (d.error) {
    return `<div class="section-title">运行总览</div>
      <div class="card"><div class="notice err">
        <b>无法读取诊断信息</b><br>${esc(d.error)}<br>
        请确认后端已启动（<code>python -m hc_agent.web</code>）。
      </div></div>`;
  }
  if (state.sub === "reliability") return pageReliability(d);
  if (state.sub === "channels") return pageChannels();
  if (state.sub === "runs") return pageRuns();

  const wd = d.watchdog || {};
  const cl = d.crashlog || {};
  const ch = (state.channels || {}).channels || {};
  const avail = Object.values(ch).filter((c) => c && c.available).length;
  const runs = state.runs || [];

  let h = `<div class="section-title">环境与就绪度</div>
  <div class="grid grid-4">
    ${kpi("计算后端", d.service ? "在线" : "—", tag("已连接", "ok"),
          (d.process && ("PID " + d.process.pid)) || "")}
    ${kpi("OpenDSS 引擎通道", `${avail} / 3`, 
          avail > 0 ? tag("可用", "ok") : tag("不可用", "err"),
          (state.channels && state.channels.selected) || "")}
    ${kpi("引擎看门狗",
          wd.started ? "运行中" : (wd.enabled ? "未启动" : "已禁用"),
          wd.started ? tag("已启动", "ok") : tag("未启动", "warn"),
          "复检 " + num(wd.probes, 0) + " 次")}
    ${kpi("崩溃可观测",
          cl.installed ? "已安装" : "未安装",
          cl.installed ? tag("已安装", "ok") : tag("未安装", "gray"),
          "已记录 " + num(cl.records_written, 0) + " 条")}
  </div>`;

  h += `<div class="section-title">最近一次运行</div>`;
  const oc = d["run_outcome.json"];
  if (oc && oc.exit_code !== undefined) {
    const kind = oc.exit_code === 0 ? "ok"
      : oc.exit_code === 5 ? "warn"
      : oc.exit_code === 6 ? "warn" : "err";
    h += `<div class="card">
      <div class="card-title"><span>${esc(oc.run_id || "")}</span>
        ${tag("退出码 " + oc.exit_code, kind)}</div>
      <div class="grid grid-3">
        ${kpi("物理可开放容量", num(oc.capacity_physical_kw) + " kW", "", "")}
        ${kpi("管理可开放容量", num(oc.capacity_managed_kw) + " kW", "", "")}
        ${kpi("红黄绿等级", levelTag(oc.level), "", "")}
      </div>
      ${oc.error ? `<div class="notice err">${esc(oc.error)}</div>` : ""}
      ${oc.note ? `<div class="notice info">${esc(oc.note)}</div>` : ""}
    </div>`;
  } else {
    h += `<div class="card"><div class="route-loading">
      <div class="msg">还没有跑过任务</div>
      <div class="hint">去「容量分析」按提示执行一次评估，跑完这里会显示真实结果。</div>
    </div></div>`;
  }
  return h;
}

function pageReliability(d) {
  const wd = d.watchdog || {};
  const cl = d.crashlog || {};
  const st = d.state || {};
  let h = `<div class="section-title">可靠性设施</div>
  <div class="grid grid-3">
    ${kpi("看门狗状态", wd.started ? "运行中" : "未启动",
          wd.started ? tag("已启动", "ok") : tag("未启动", "warn"),
          wd.last_reason ? esc(wd.last_reason) : "")}
    ${kpi("熔断器", wd.breaker_open ? "已打开" : "闭合",
          wd.breaker_open ? tag("熔断", "warn") : tag("正常", "ok"),
          "窗口内重启 " + num(wd.restarts_in_window, 0) + " 次")}
    ${kpi("崩溃日志", cl.installed ? "已安装" : "未安装",
          cl.installed ? tag("已安装", "ok") : tag("未安装", "gray"),
          "写盘失败 " + num(cl.write_errors, 0) + " 次")}
  </div>`;

  h += `<div class="section-title">看门狗参数与计数</div>
  <div class="card"><div class="grid grid-2">
    <div>${kv("复检次数", num(wd.probes, 0))}
      ${kv("复检失败", num(wd.probe_failures, 0))}
      ${kv("触发重启", num(wd.triggers, 0))}
      ${kv("熔断拦截", num(wd.breaker_blocked, 0))}</div>
    <div>${(() => {
      const p = wd.params || {};
      return kv("探测间隔", esc(p.interval_s) + " s")
        + kv("复检超时", esc(p.probe_timeout_s) + " s")
        + kv("卡死阈值", esc(p.task_stuck_s) + " s")
        + kv("看门狗退出码", esc(p.exit_code))
        + kv("熔断上限", esc(p.breaker_max) + " 次 / " + esc(p.breaker_window_s) + " s");
    })()}</div>
  </div>
  <div class="notice info" style="margin-top:14px">
    看门狗用「再问引擎一句 Version」区分<b>算例慢</b>与<b>引擎中毒</b>：
    秒回则继续运行，超时才判中毒并以退出码 70 退出，交由桌面壳重启。
    没有外层重启者时它按设计<b>拒绝启用</b>，以免任务静默消失。
  </div></div>`;

  h += `<div class="section-title">状态遗言</div>
  <div class="card">
    ${kv("最后阶段", esc(d.last_stage || "(无痕迹)"), true)}
    ${kv("状态", esc(st.status || "—"))}
    ${kv("写入时间", esc(st._persisted_at_str || "—"))}
    <div class="notice info" style="margin-top:12px">
      <b>interrupted</b> 表示「没来得及判定」，与「判定为失败」是两种语义：
      前者查崩溃日志与最后落盘阶段，后者查错误码。
      混同会让看板把基础设施问题混进业务分布，成功率分子分母都被污染。
    </div>
    <div class="notice warn" style="margin-top:10px">
      ${esc(cl.limitation || "")}
    </div>
  </div>`;
  return h;
}

function pageChannels() {
  const c = state.channels;
  if (!c) return loadingCard("引擎通道");
  if (c.error) return `<div class="card"><div class="notice err">${esc(c.error)}</div></div>`;
  const chans = c.channels || {};
  const rows = Object.entries(chans).map(([name, info]) => {
    const cap = info.capability || {};
    const sel = c.selected === name;
    return `<tr>
      <td><b>${esc(name)}</b>${sel ? " " + tag("本次选用", "info") : ""}</td>
      <td>${info.available ? dotTag("可用", "ok") : dotTag("不可用", "gray")}</td>
      <td>${cap.killable_on_hang ? "独立进程（可强杀）" : "进程内"}</td>
      <td class="n">${cap.significant_digits == null ? "双精度" : cap.significant_digits + " 位"}</td>
      <td class="n">${esc(cap.min_reliable_tolerance ?? "—")}</td>
      <td class="muted" style="font-size:11.5px">${esc(info.detail || "")}</td>
    </tr>`;
  }).join("");
  return `<div class="section-title">引擎通道</div>
  <div class="card">
    <div class="card-title"><span>三条通道与能力声明</span>
      ${c.selected ? tag("当前：" + c.selected, "info") : tag("无可用通道", "err")}</div>
    <div class="scroll"><table>
      <thead><tr><th>通道</th><th>状态</th><th>隔离性</th>
        <th class="n">有效位</th><th class="n">复算容差下限</th><th>说明</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="notice info" style="margin-top:14px">
      <b>能力差异是显式声明的，不是隐含的。</b>
      CLI 通道经 CSV 导出，有效位约 6 位，因此<b>不可用于字段级复算比对</b>。
      需要 1e-9 容差时选择器会自动排除它，而不是安静地给出一个看似正常的错误结论。
    </div>
  </div>`;
}

function pageRuns() {
  if (!state.runs.length) {
    return `<div class="section-title">运行记录</div>
      <div class="card"><div class="route-loading">
        <div class="msg">还没有运行记录</div>
        <div class="hint">运行 <code>python -m hc_agent.pipeline</code> 生成产物后会出现在这里。</div>
      </div></div>`;
  }
  const rows = state.runs.map((r) => `<tr>
    <td><b>${esc(r.run_id)}</b></td>
    <td>${esc(r.task_id || "—")}</td>
    <td class="n">${num(r.capacity_physical_kw)}</td>
    <td>${levelTag(r.level)}</td>
    <td class="n">${r.exit_code === undefined ? NA : esc(r.exit_code)}</td>
    <td class="muted" style="font-size:11.5px">${esc(r.last_stage || "—")}</td>
    <td><button class="btn sm" data-open="${esc(r.run_id)}">查看</button></td>
  </tr>`).join("");
  return `<div class="section-title">运行记录</div>
  <div class="card">
    <div class="card-title"><span>共 ${state.runs.length} 次</span></div>
    <div class="scroll"><table>
      <thead><tr><th>运行号</th><th>任务号</th><th class="n">物理容量 kW</th>
        <th>分级</th><th class="n">退出码</th><th>最后阶段</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table></div>
  </div>`;
}

/* -------------------------------------------------- 页面 2：容量分析 */
function pageWorkbench() {
  if (state.sub === "constraints") return pageConstraints();
  if (state.sub === "scenario") return pageScenario();

  let h = `<div class="section-title">如何发起一次评估</div>
  <div class="card">
    <div class="notice info">
      本 Agent 以<b>命令行为主入口</b>。界面只读展示产物，不做估算或填充 ——
      所有电气数值都由 OpenDSS 引擎产生，界面不参与计算。
    </div>
    <div class="section-title" style="margin-top:16px">1. 准备输入包</div>
    <div class="muted" style="font-size:12.5px;line-height:1.8">
      输入目录需包含 <code>network.xlsx</code>、<code>profiles.csv</code>、
      <code>rules.yaml</code>、<code>task.json</code> 等文件。
      若手上是外部算例（xlsx / csv / json / MATPOWER .m），先用转换器规范化。
    </div>
    <div class="section-title">2. 执行</div>
    <pre class="mono" style="background:#F5F7FA;border:1px solid var(--border);border-radius:6px;padding:12px;font-size:12px;line-height:1.7;overflow:auto"># 转换外部算例（可选）
python -m hc_agent.cli convert --source case.xlsx --out inputs/

# 执行承载力评估
python -m hc_agent.cli run --input-dir inputs/ --output-dir runs/RUN-001

# 查看引擎通道状态
python -m hc_agent.cli channels

# 汇总诊断（看门狗 / 崩溃日志 / 最后遗言）
python -m hc_agent.cli diagnostics --run-dir runs/RUN-001</pre>
    <div class="section-title">3. 刷新本页查看结果</div>
    <div class="muted" style="font-size:12.5px">
      点击标题栏的刷新按钮，或按 F5。
    </div>
    <div style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn primary" id="btn-refresh-2">刷新界面</button>
      <button class="btn" id="btn-logs-2">打开日志目录</button>
    </div>
  </div>`;

  h += `<div class="section-title">退出码契约</div>
  <div class="card">
    <table>
      <thead><tr><th class="n">码</th><th>含义</th><th>排查方向</th></tr></thead>
      <tbody>
        <tr><td class="n">0</td><td>成功</td><td>—</td></tr>
        <tr><td class="n">2</td><td>输入阻断</td><td><code>issues.csv</code> 中的 blocking 项</td></tr>
        <tr><td class="n">3</td><td>计算失败</td><td>错误码（DSS_*）</td></tr>
        <tr><td class="n">4</td><td>独立复算失败</td><td><code>verification/</code> 差异明细</td></tr>
        <tr><td class="n">5</td><td>未满足发布门禁</td><td>三道门禁中未提交的那一道</td></tr>
        <tr><td class="n">6</td><td>任务被中断（没来得及判定）</td><td>崩溃日志 + 最后落盘阶段</td></tr>
        <tr><td class="n">70</td><td>看门狗判定引擎不可在进程内恢复</td><td>由桌面壳自动重启</td></tr>
      </tbody>
    </table>
  </div>`;
  return h;
}

function pageScenario() {
  const d = state.diag || {};
  const oc = (state.detail && state.detail.summary) || {};
  let h = `<div class="section-title">算例与场景</div><div class="card">`;
  h += kv("运行号", esc(oc.run_id || (d.latest_run || "—")), true);
  h += kv("任务号", esc(oc.task_id || "—"));
  h += kv("业务场景", esc(oc.business_scene || "—"));
  h += kv("场景数", num(oc.scenario_count));
  h += kv("时间片总数", num(oc.slice_count));
  h += kv("基线收敛", oc.all_converged === undefined ? NA : esc(oc.all_converged));
  h += `</div>`;
  if (oc.scenarios && oc.scenarios.length) {
    const rows = oc.scenarios.map((s) => `<tr>
      <td>${esc(s.scenario_id || "—")}</td>
      <td>${esc(s.kind || s.type || "—")}</td>
      <td class="n">${num(s.slice_count, 0)}</td>
      <td>${esc(s.description || "")}</td>
    </tr>`).join("");
    h += `<div class="card" style="margin-top:14px">
      <div class="card-title"><span>场景明细</span></div>
      <div class="scroll"><table><thead><tr><th>场景</th><th>类型</th>
      <th class="n">时间片</th><th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>
    </div>`;
  }
  return h;
}

function pageConstraints() {
  const oc = (state.detail && state.detail.summary) || {};
  const cs = oc.constraint_summary;
  let h = `<div class="section-title">约束与阈值</div>`;
  h += `<div class="card">
    <div class="notice info">
      所有限值来自<b>经批准的规则包</b>，Agent 不内置任何未经确认的常数。
      规则为 <code>null</code> 表示未批准，必须阻断而非猜测。
    </div>
    <div style="margin-top:14px">
      ${kv("规则包", esc(oc.rule_package || "—"), true)}
    </div>
  </div>`;
  if (cs) {
    h += `<div class="grid grid-4" style="margin-top:14px">
      ${kpi("约束总数", num(cs.total, 0), "", "")}
      ${kpi("通过", num(cs.pass, 0), tag("pass", "ok"), "")}
      ${kpi("不通过", num(cs.fail, 0), cs.fail > 0 ? tag("fail", "err") : "", "")}
      ${kpi("未覆盖", num(cs.unknown, 0), cs.unknown > 0 ? tag("unknown", "warn") : "", "")}
    </div>`;
    const bc = cs.binding_constraints || [];
    if (bc.length) {
      const rows = bc.map((b) => `<tr>
        <td>${esc(b.constraint_id || "")}</td>
        <td class="mono">${esc(b.object_id || "")}</td>
        <td>${esc(b.metric || "")}</td>
        <td class="n">${num(b.value)}</td>
        <td class="n">[${num(b.limit_lower)}, ${num(b.limit_upper)}]</td>
        <td class="n">${num(b.margin)}</td>
      </tr>`).join("");
      h += `<div class="card" style="margin-top:14px">
        <div class="card-title"><span>临界约束</span>
          ${tag("共 " + bc.length + " 项", "gray")}</div>
        <div class="scroll"><table><thead><tr><th>约束</th><th>对象</th>
        <th>指标</th><th class="n">实测</th><th class="n">限值区间</th>
        <th class="n">余量</th></tr></thead><tbody>${rows}</tbody></table></div>
      </div>`;
    }
  }
  h += `<div class="card" style="margin-top:14px">
    <div class="notice warn">
      <b>unknown 口径</b>：能力未覆盖（缺故障模型、零序参数等）记为 unknown，
      它<b>既不计为通过、也不计为不可行</b>。把 unknown 当成不可行会让容量被
      系统性低估，当成通过则会发布无依据的结论。
    </div>
  </div>`;
  return h;
}

/* -------------------------------------------------- 页面 3：结果与证据 */
function pageResults() {
  const sum = (state.detail && state.detail.summary) || null;
  const diag = state.diag || {};
  const oc = diag["run_outcome.json"] || {};

  if (!sum && !oc.capacity_physical_kw) {
    return `<div class="section-title">容量结论</div>
      <div class="card"><div class="route-loading">
        <div class="msg">还没有可展示的结果</div>
        <div class="hint">先在「容量分析」执行一次评估。</div>
      </div></div>`;
  }
  const s = sum || oc;
  if (state.sub === "caliber") return pageCaliber(s);
  if (state.sub === "evidence") return pageEvidence(s);
  if (state.sub === "verify") return pageVerify(s);

  let h = `<div class="section-title">容量结论</div>
  <div class="grid grid-3">
    ${kpi("物理可开放容量", num(s.capacity_physical_kw) + " kW", "", "受物理约束的最大可接入量")}
    ${kpi("管理可开放容量", num(s.capacity_managed_kw) + " kW", "", "再叠加管理裕度后的可发布值")}
    ${kpi("红黄绿等级", levelTag(s.level), "", esc(s.level_reason || ""))}
  </div>`;

  h += `<div class="card" style="margin-top:14px">
    <div class="card-title"><span>关键指标</span>
      ${tag("运行 " + esc(s.run_id || oc.run_id || "—"), "gray")}</div>
    ${kv("主导限制因素", esc(s.binding_constraint || "—"))}
    ${kv("搜索方法", esc(s.search_method || "—"))}
    ${kv("搜索迭代次数", num(s.search_iterations, 0))}
    ${kv("搜索状态", esc(s.search_status || "—"))}
    ${kv("场景数 / 时间片", num(s.scenario_count, 0) + " / " + num(s.slice_count, 0))}
  </div>`;

  const notes = s.search_notes || [];
  if (notes.length) {
    h += `<div class="card" style="margin-top:14px">
      <div class="card-title"><span>搜索说明</span></div>
      ${notes.map((n) => `<div class="kvrow"><span style="text-align:left">${esc(n)}</span></div>`).join("")}
    </div>`;
  }
  return h;
}

function pageCaliber(s) {
  const cal = s.caliber;
  if (!cal || cal.error) {
    return `<div class="section-title">求解口径声明</div>
      <div class="card"><div class="notice warn">
        本次产物未包含口径声明${cal && cal.error ? "：" + esc(cal.error) : ""}。
        重新运行一次评估即可生成。
      </div></div>`;
  }
  const rows = (cal.items || []).map((it) => `<tr>
    <td class="nowrap"><b>${esc(it.label)}</b></td>
    <td>${md(it.value)}</td>
    <td class="muted" style="font-size:11.5px">${md(it.note || "")}</td>
  </tr>`).join("");
  let h = `<div class="section-title">求解口径声明</div>
  <div class="card">
    <div class="card-title"><span>本次求解口径</span>
      ${tag(cal.phase_state || "balanced", "info")}</div>
    <div class="scroll"><table><thead><tr><th>项</th><th>本次口径</th>
      <th>说明</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${cal.phase_statement
      ? `<div class="notice info" style="margin-top:14px">${md(cal.phase_statement)}</div>`
      : ""}
  </div>`;
  if ((cal.assumptions || []).length) {
    h += `<div class="card" style="margin-top:14px">
      <div class="card-title"><span>模型假设</span></div>
      ${cal.assumptions.map((a) =>
        `<div class="kvrow"><span style="text-align:left">${md(a)}</span></div>`).join("")}
    </div>`;
  }
  if ((cal.unsupported || []).length) {
    h += `<div class="card" style="margin-top:14px">
      <div class="card-title"><span>本次未覆盖</span>
        ${tag(cal.unsupported.length + " 项", "warn")}</div>
      ${cal.unsupported.map((u) =>
        `<div class="kvrow"><span style="text-align:left">${md(u)}</span></div>`).join("")}
    </div>`;
  }
  return h;
}

function pageEvidence(s) {
  const d = state.detail || {};
  let h = `<div class="section-title">证据链与门禁</div>
  <div class="card">
    <div class="card-title"><span>三道发布门禁</span></div>
    <div class="notice info">
      独立复算 + 专业复核 + 审批，<b>任一道未满足即不得发布</b>（退出码 5）。
      这是设计约束，不是提示。
    </div>
    <div style="margin-top:14px">
      ${kv("门禁结论", (d.gate && d.gate.passed)
          ? tag("已通过", "ok") : tag("未通过", "warn"))}
      ${kv("产物签封", d.publication ? tag("已签封", "ok") : tag("未签封", "gray"))}
      ${kv("状态遗言阶段", esc((d.state && d.state._persisted_stage) || "—"), true)}
    </div>
  </div>`;
  if (d.summary && d.summary.input_digest) {
    h += `<div class="card" style="margin-top:14px">
      <div class="card-title"><span>输入与引擎指纹</span></div>
      ${kv("输入清单摘要", esc(d.summary.input_digest), true)}
      ${kv("引擎", esc((d.summary.engine_fingerprint || {}).interface || "—"))}
    </div>`;
  }
  return h;
}

function pageVerify(s) {
  const d = state.detail || {};
  const oc = (state.diag || {})["run_outcome.json"] || {};
  let h = `<div class="section-title">独立复算</div>
  <div class="card">
    <div class="notice info">
      独立复算由<b>另一条引擎通道</b>重跑并逐字段比对，用来发现单一通道的系统性错误。
      有效位不足的通道会被自动排除，不参与比对。
    </div>
    <div style="margin-top:14px">
      ${kv("复算是否执行", d.verify === undefined && !s.verification ? NA : "已执行")}
    </div>
  </div>`;
  return h;
}

/* ------------------------------------------------------------------ 组件 */
function kpi(label, value, tagHtml, foot) {
  return `<div class="status-card">
    <div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${tagHtml ? `<div style="margin-top:6px">${tagHtml}</div>` : ""}
    ${foot ? `<div class="foot">${foot}</div>` : ""}
  </div>`;
}
function levelTag(level) {
  if (!level) return NA;
  const k = { green: "ok", yellow: "warn", red: "err", grey: "gray" }[level] || "gray";
  const zh = { green: "绿色", yellow: "黄色", red: "红色", grey: "灰色（内部态）" }[level] || level;
  return dotTag(zh, k);
}
function loadingCard(name) {
  return `<div class="section-title">${esc(name)}</div>
    <div class="card"><div class="route-loading">
      <div class="spinner"></div>
      <div class="msg">正在加载「${esc(name)}」…</div>
      <div class="hint">首次打开需要读取运行产物，可能要几秒。</div>
    </div></div>`;
}

/* ------------------------------------------------------------------ 渲染 */
function render() {
  $$("#nav .nav-item").forEach((el) =>
    el.classList.toggle("active", el.dataset.page === state.page));
  renderSubnav();
  const crumbs = {
    overview: "运行总览 / 环境与就绪度",
    workbench: "容量分析 / 如何发起一次评估",
    results: "结果与证据 / 容量结论",
  };
  $("#sb-crumb").textContent = crumbs[state.page] || "";

  const body = $("#page-body");
  if (state.page === "overview") body.innerHTML = pageOverview();
  else if (state.page === "workbench") body.innerHTML = pageWorkbench();
  else body.innerHTML = pageResults();

  $$("#page-body [data-open]").forEach((el) => {
    el.onclick = () => openRun(el.dataset.open);
  });
  const r2 = $("#btn-refresh-2"); if (r2) r2.onclick = refresh;
  const l2 = $("#btn-logs-2");
  if (l2) l2.onclick = () => window.hc && window.hc.openLogs();
}

function setStatus(id, text, kind) {
  const el = $(id);
  if (!el) return;
  el.querySelector(".dot").className = "dot " + kind;
  el.querySelector("span:last-child").textContent = text;
}

async function openRun(runId) {
  const r = await api("/api/runs/" + encodeURIComponent(runId));
  if (r.ok) { state.detail = r.data; state.page = "results"; state.sub = "result"; render(); }
}

async function refresh() {
  const [diag, chans, runs] = await Promise.all([
    api("/api/diagnostics"), api("/api/channels"), api("/api/runs"),
  ]);
  state.diag = diag.ok ? diag.data : { error: diag.error };
  state.channels = chans.ok ? chans.data : null;
  state.runs = (runs.ok && runs.data.runs) ? runs.data.runs : [];
  $("#sb-run-count").textContent = state.runs.length;

  const d = state.diag || {};
  setStatus("#sb-backend", diag.ok ? "后端正常" : "后端未连接",
            diag.ok ? "ok" : "err");
  const avail = Object.values((state.channels || {}).channels || {})
    .filter((c) => c && c.available).length;
  setStatus("#sb-engine",
            avail ? ("引擎就绪（" + avail + "/3）") : "引擎不可用",
            avail ? "ok" : "err");
  const wd = d.watchdog || {};
  setStatus("#sb-watchdog",
            wd.started ? "看门狗运行中" : (wd.enabled ? "看门狗未启动" : "看门狗已禁用"),
            wd.started ? "ok" : "warn");
  const cl = d.crashlog || {};
  setStatus("#sb-crashlog", cl.installed ? "崩溃日志已装" : "崩溃日志未装",
            cl.installed ? "ok" : "gray");

  // 若有最近运行且尚未选过，自动加载明细（供口径/证据页使用）
  if (!state.detail.summary && state.runs.length) {
    const first = state.runs.find((r) => r.exit_code !== undefined) || state.runs[0];
    if (first && first.run_id && first.run_id !== "_logs") {
      const rr = await api("/api/runs/" + encodeURIComponent(first.run_id));
      if (rr.ok) state.detail = rr.data;
    }
  }
  render();
}

/* ------------------------------------------------------------------ 事件 */
$$("#nav .nav-item").forEach((el) => {
  el.onclick = () => {
    state.page = el.dataset.page;
    state.sub = subnavItems(state.page).find((x) => x[0] !== "group")[0];
    render();
  };
});
$("#btn-refresh").onclick = refresh;
$("#btn-help").onclick = () => {
  alert("配网承载力与主配微协同优化 Agent\n\n"
    + "· 界面只读展示运行产物，不参与任何电气计算\n"
    + "· 所有数值来自 OpenDSS 引擎，取不到的显示「未获取」\n"
    + "· 视觉语言对齐「天枢一号」桌面端\n\n"
    + "刷新：点标题栏 ↻ 按钮，或按 F5。");
};
["btn-min", "btn-max", "btn-close"].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.onclick = () => {
    if (window.hc && window.hc.win) window.hc.win(id.replace("btn-", ""));
  };
});
document.addEventListener("keydown", (e) => {
  if (e.key === "F5" || (e.ctrlKey && e.key.toLowerCase() === "r")) {
    e.preventDefault(); refresh();
  }
  if (e.key === "Escape" && !IS_ELECTRON) return;
});

/* ------------------------------------------------------------------ 启动 */
// 深链：?page=results&sub=caliber 直接打开某一页（便于验证与分享定位）
(function applyDeepLink() {
  const q = new URLSearchParams(location.search);
  const page = q.get("page");
  if (page && ["overview", "workbench", "results"].includes(page)) {
    state.page = page;
  }
  const sub = q.get("sub");
  if (sub) state.sub = sub;
  const run = q.get("run");
  if (run) state.wantRun = run;
})();

async function boot() {
  await refresh();
  if (state.wantRun) {
    await openRun(state.wantRun);
    state.wantRun = null;
  }
  // 供自动化验收：把当前页与渲染结果暴露出来
  window.__hc_ready = {
    page: state.page, sub: state.sub,
    runs: state.runs.length,
    hasCaliber: !!(state.detail && state.detail.summary
                   && state.detail.summary.caliber),
    nodeCount: document.querySelectorAll("#page-body *").length,
  };
}
boot();

setInterval(() => {
  // 状态栏心跳；不重绘页面主体，避免打断阅读
  api("/api/diagnostics").then((r) => {
    if (!r.ok) { setStatus("#sb-backend", "后端未连接", "err"); return; }
    setStatus("#sb-backend", "后端正常", "ok");
    const wd = r.data.watchdog || {};
    setStatus("#sb-watchdog", wd.started ? "看门狗运行中" : "看门狗未启动",
              wd.started ? "ok" : "warn");
  });
}, 5000);
