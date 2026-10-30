# 配网承载力智能体Agent

> 依据《配网承载力与主配微协同优化 Agent 设计文档》部署的工程实现。
> 设计文档路径：`C:\Users\Chen\Desktop\配网承载力Agent设计文档.docx`

本工程把设计文档中的架构、技能体系、规则中心、OpenDSS 适配器、数据契约、
独立复算证据链和主配微协同流程落地为**可运行、可复算、可审计**的软件。

---

## 1. 一句话说明它做什么

回答两个业务问题：

1. **还能接多少** —— 在当前网架与批准场景下，某台区/馈线/变电站还能接入多少新增光伏；
2. **这笔申请行不行** —— 某个并网申请接入后，电压、载流、反送、三相不平衡、
   短路能力是否满足要求。

**Agent 只做编排**：数据治理、任务调度、技能调用、异常处置、证据组织、报告生成。
**所有电气数值只来自 OpenDSS**，Agent 不产生也不修改任何潮流类数值。

---

## 2. 快速开始

```powershell
$py = "C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
cd C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project

# ① 承载力评估主流程（S01-S09）
& $py -m hc_agent.pipeline --input-dir tests/golden/FD002 --output-dir runs/MY_RUN

# ② 单笔并网申请校核（business_scene=access_check，按申请容量校核）
& $py -m hc_agent.pipeline --input-dir tests/golden/APP001 --output-dir runs/MY_APP

# ③ 独立复算（全新目录、全新进程、从冻结输入重建）
& $py -m hc_agent.verification --manifest runs/MY_RUN/manifest.json `
                              --output-dir runs/MY_RUN_VERIFY

# ④ 报告与证据包
& $py -m hc_agent.reporting --run-dir runs/MY_RUN --seal

# ⑤ 主配微协同研究（R01-R08）
& $py -m hc_agent.research_pipeline --inputs tests/research/RM001 `
                                   --output runs/MY_STUDY

# ⑥ 回归测试套件（37 项）
& $py tests/test_agent.py

# ⑦ 设计文档验收核对（77 项）
& $py tests/acceptance_check.py

# ⑧ 运行监控台（前端页面）
& $py tools/export_dashboard.py --root runs/ACCEPT --out web/data.json
& $py -m http.server 8777 --directory web --bind 127.0.0.1
#    浏览器打开 http://127.0.0.1:8777/   （或直接用 web/dashboard_standalone.html 离线查看）
```

退出码（设计文档 17.10）：
`0` 成功 · `2` 输入阻断 · `3` 计算失败 · `4` 复算失败 · `5` 未满足发布门禁

> **退出码 5 是正常的、也是正确的**：主流程跑完并产出候选结论，但"独立复算 +
> 专业复核 + 发布审批"三道门中后两道需要人工提交，因此不得自动发布。

---

## 3. 目录结构

```
hc_agent_project/
├── hc_agent/                      # 主包
│   ├── pipeline.py                # ★ 唯一任务入口（S01-S09）
│   ├── research_pipeline.py       # ★ 主配微研究入口（R01-R08）
│   ├── verification.py            # ★ 独立复算（全新目录/进程/上下文）
│   ├── reporting.py               # ★ 报告与证据包（SK-12）
│   ├── capacity_search.py         # ★ 开放容量搜索（SK-10）
│   ├── constraints.py             # (见 model/) 约束判定
│   ├── rules.py                   # ★ 规则中心（4.8）
│   ├── skills.py                  # ★ 技能注册中心（4.4）
│   ├── evidence.py                # ★ 不可变快照、哈希、血缘、审计账本（5.4/9.2）
│   ├── runtime_env.py             # 引擎定位与版本锁定（8.1）
│   ├── errors.py                  # 标准错误码与退出码
│   ├── io/
│   │   ├── schema.py              # 字段契约与校验（17.4/17.5）
│   │   ├── ingest.py              # 输入包读取（17.4）
│   │   └── validation.py          # 质量与拓扑校验（SK-01）
│   ├── model/
│   │   ├── build_model.py         # 结构化输入 → Master.dss（17.11）
│   │   ├── scenarios.py           # 场景生成（SK-03 / 6.1）
│   │   ├── powerflow.py           # 基线/增量潮流（SK-04/SK-05）
│   │   └── constraints.py         # 五类约束判定（SK-06..SK-09）
│   ├── engines/
│   │   └── opendss_adapter.py     # ★ 唯一电气计算引擎调用层（第 8 章）
│   ├── research/
│   │   └── capacity_outer.py      # 容量外层搜索（R03）
│   ├── coordination/
│   │   └── atc.py                 # 目标级联协调（20.3）
│   ├── uncertainty/
│   │   └── probability_adversary.py  # 分布鲁棒概率子问题（20.4）
│   └── optimization/
│       └── local_dispatch.py      # 区域局部调度（21.4）
├── skills/
│   ├── skill_registry.json        # ★ 12 项技能注册表（SK-01..SK-12）
│   └── cards/                     # 自动导出的 Skill Card（Markdown）
├── rules/
│   └── rules_template.yaml        # 待审规则包模板（14 类规则，限值为 null）
├── web/                           # ★ 运行监控台（前端页面）
│   ├── index.html                 # 页面本体（原生 HTML/CSS/JS，零外部依赖）
│   ├── data.json                  # 由运行产物导出的数据包
│   ├── dashboard_standalone.html  # 单文件离线版（数据内联，可双击打开）
│   └── README.md                  # 页面使用说明
├── tools/
│   ├── export_dashboard.py        # runs/ → web/data.json
│   ├── bundle_single_file.py      # 生成单文件离线版
│   └── check_dashboard.py         # 页面 JS 语法与 DOM 引用校验
├── tests/
│   ├── test_agent.py              # ★ 37 项回归测试
│   ├── acceptance_check.py        # ★ 77 项设计文档验收核对
│   ├── golden/FD001/              # 受限馈线（基线越限，容量 0）
│   ├── golden/FD002/              # 健康馈线（基线洁净，容量 334 kW，含短路校核）
│   ├── golden/APP001/             # 单笔并网申请校核（SK-11，250 kW 申请）
│   └── research/RM001/            # 配网 + 3 微网协同研究算例
└── runs/                          # 运行产物（每次运行一个目录）
```

---

## 4. 12 项专业技能（SK-01 … SK-12）

技能不是提示词，而是**受约束的专业工具契约**（设计文档 4.6）。
每项技能含：目的、适用范围、输入、前置条件、执行步骤、输出、异常策略、
证据要求、安全边界、测试样例、责任人/审批人。

| 编号 | 技能 | 模块入口 | 需引擎 |
|---|---|---|---|
| SK-01 | 拓扑与模型一致性检查 | `io.validation.check_topology` | 否 |
| SK-02 | 负荷与光伏曲线标准化 | `io.ingest._normalize_profiles` | 否 |
| SK-03 | 运行场景生成 | `model.scenarios.build_scenarios` | 否 |
| SK-04 | 基线潮流计算 | `model.powerflow.run_baseline` | **是** |
| SK-05 | 增量接入潮流计算 | `model.powerflow.run_scenario_set` | **是** |
| SK-06 | 节点电压越限校核 | `model.constraints.evaluate_voltage` | 是 |
| SK-07 | 设备载流与反向潮流校核 | `model.constraints.evaluate_thermal` | **是** |
| SK-08 | 三相不平衡校核 | `model.constraints.evaluate_imbalance` | **是** |
| SK-09 | 短路容量与保护触发校核 | `engines.opendss_adapter.short_circuit` | **是** |
| SK-10 | 开放容量搜索 | `capacity_search.search_capacity` | **是** |
| SK-11 | 并网申请方案校核 | `pipeline.Pipeline.s04_s05_s06` | **是** |
| SK-12 | 证据包与专业报告生成 | `reporting.generate_report` | 否 |

只有 `status: approved` 的技能可被正式任务选用；注册表校验器会拒绝
含"适当/合理/必要时"等不可验收措辞的步骤。

---

## 5. 关键设计约束及落实位置

| 设计文档要求 | 落实位置 |
|---|---|
| 全部电气量只由 OpenDSS 产生 | `engines/opendss_adapter.py` 是唯一调用点；其余模块只用 pandas/networkx 读表与建图 |
| 原始数据字节级保护、只读快照 | `evidence.freeze_inputs` + `make_readonly`，`input_digest` 写入清单 |
| 同一任务冻结 snapshot/rule/skill/engine | `manifest.json` 的 `snapshot_id`、`rule_version`、`skill_version`、`engine_fingerprint` |
| 规则 null 值必须阻断（17.9） | `RulePackage.require()` 抛 `RuleNotApprovedError`；`management.formula_id` 为 null 时拒绝计算管理容量 |
| 未批准规则包禁止用于正式任务 | `load_rule_package(require_approved=True)` → 退出码 2 |
| 上界仍可行不得宣称最大值（17.12） | `capacity_search` 返回 `at_least_upper_bound` 并在备注中声明 |
| unknown ≠ 不可行（17.12） | `ConstraintEvaluator.feasible()`（无 fail）用于搜索；`all_pass()`（无 fail 且无 unknown）只用于可发布性 |
| 独立复算必须重建、不复用缓存（9.1） | `verification.verify_run` 在全新目录用新 `OpenDSSAdapter`（新上下文）从冻结输入重建 |
| 复算零差异 + 状态必须一致 | `compare_runs` 逐行比对约束明细 + 字段级容差 |
| 三道门（复算/复核/审批） | `rules.check_release_gate`，任一未过即 `exit 5` |
| 证据包不含密钥 | `evidence.seal_evidence` |
| Agent 不得有现场控制能力 | 适配器无任何遥控/分合闸/定值接口；`tests/test_agent.py` 用词边界断言 |

---

## 6. 黄金算例与实测结果

### FD001 —— 受限低压馈线（红色场景）

10 kV 馈线 → 400 kVA Dyn11 配变 → 250 A 低压线路，负荷峰值 240 kW。

| 指标 | 实测 |
|---|---|
| 基线约束 | pass 106 / **fail 14** / unknown 1 |
| 越限构成 | 6 项电压（LV 末端 0.9033 pu < 0.93）+ 8 项载流（LV 线路 117.7% > 100%） |
| 物理容量 | **0 kW**（基线即违规，先输出存量问题） |
| 分级 | grey（存在 unknown，不生成正式红黄绿结论） |
| 独立复算 | **pass，0 差异** |

物理解释：250 A 的低压线路带不动 240 kW 末端负荷，电压跌落与过载同时发生，
因此可开放空间为 0。这正是设计文档要求的"先输出存量问题，不直接给出可开放结论"。

### FD002 —— 健康馈线（绿色场景，真正的容量搜索演示）

10 kV 馈线 → 1000 kVA Dyn11 配变 → 600 A 低压线路，已有光伏 100 + 300 kW。

| 指标 | 实测 |
|---|---|
| 基线约束 | pass 120 / **fail 0** / unknown 0 |
| 物理可开放容量 | **333.98 kW** |
| 管理可开放容量 | 270.53 kW（同时率 0.9 → 裕度 10% → 扣占用） |
| 搜索 | `bracket_and_bisect`，**11 次迭代**，状态 `ok` |
| 主导限制因素 | `VOLTAGE_LIMIT`（LV 母线触及 1.10 pu 上限） |
| 分级 | **green**（短路校核执行后 unknown 归零，结论可正式发布） |
| 独立复算 | **pass，0 差异** |

搜索轨迹体现了完整的定界过程：`0 可行 → 3000 不可行 → 二分收敛到
可行下界 333.98 / 不可行上界 339.84`，间隔小于批准精度。

本算例启用了 `shortcircuit` 分析，因此 **SK-09 短路容量校核真实执行**：
故障点取候选接入点 `BUS-LV-002`，三相故障电流 4081.9 A 小于 20000 A 限值，
`SHORT_CIRCUIT_LIMIT` 由 unknown 变为 `pass`，分级随之由 grey 升为 green。
若未提供 `fault_models.csv`，该项会如实保持 `unknown` 而不会伪装通过。

### APP001 —— 单笔并网申请校核（SK-11）

同一馈线上的一笔 250 kW 并网申请（`application.json`，表 52 契约）。

| 申请容量 | 结论 | 申请引入的新增越限 | 主导限制 |
|---|---|---|---|
| 250 kW | **可行** | 0 | — |
| 320 kW | **可行** | 0 | — |
| **340 kW** | **不可行** | 3 | `VOLTAGE_LIMIT` @ bus-lv-002 |
| 400 kW | 不可行 | 3 | `VOLTAGE_LIMIT` @ bus-lv-002 |
| 600 kW | 不可行 | 11 | `REVERSE_FLOW_LIMIT` @ MV 线路 |

关键点：单笔审查**在申请容量处校核**（不是容量搜索），并按设计文档 17.3
区分"存量问题"与"申请引入的变化"——上表中 `newly_induced` 列只统计申请
新引入的越限，避免把既有问题误判为申请导致。

值得注意的是，**两条独立路径给出同一物理边界**：容量搜索找到
333.98 / 339.84 kW，并网校核在 320 kW 判可行、340 kW 判不可行。二者相互印证。

### RM001 —— 主配微协同研究

配网 D1 + 3 个微网（M1/M2/M3）+ 上级 T1 接口等值，4 个联合场景 × 24 时间片。

| 指标 | 实测 |
|---|---|
| 基线 | 40 项 pass / 0 fail |
| 容量边界 | **λ = 1.40625 MW** 可行，上界 1.64062 MW 不可行 |
| 收敛 | 间隔 0.234 MW ≤ 精度 0.25 MW |
| 主导限制 | λ=1.64 MW 时 MV 线路反向潮流 502 kW > 400 kW 限值 |
| ATC 接口残差 | 逐接口记录，非预见性与储能能量守恒检查均通过 |

---

## 7. 输入文件契约

### 承载力评估（设计文档 17.4 / 表 47）

| 文件 | 必需性 | 说明 |
|---|---|---|
| `task.json` | 全部任务必需 | 任务类型、范围、时间窗、引擎、搜索与求解参数 |
| `network.xlsx` | 结构化建模必需 | 工作表：`meta, buses, lines, transformers, switches, loads, pv, sources` |
| `profiles.csv` | 时序评估必需 | 长表；复合唯一键 `profile_id+timestamp+phase` |
| `rules.yaml` | 全部任务必需 | 已批准限值、搜索与容量管理、容差、发布门禁 |
| `application.json` | 并网审查必需 | 申请容量、候选节点、相别、控制方式 |
| `reservations.csv` | 管理容量必需 | 已受理未并网占用；无占用时提交表头空表 |
| `equipment_limits.csv` | 推荐 | 设备允许载流/反送/开断边界 |
| `fault_models.csv` | 短路校核必需 | 逆变器故障模型；缺失则短路能力标 unknown |

统一约定：CSV 为 UTF-8（带 BOM 兼容）、逗号分隔、首行表头；ID 按字符串保留
前导零；数值单元格不写单位；缺失值用空单元格而非 0；时间带时区 ISO 8601；
窗口为 `[start, end)` 半开区间。

### 主配微研究（设计文档 21.1 / 表 66-67）

`research_task.json`、`regions.csv`、`pcc.csv`、`scenario_probability.csv`、
`scenario_timeseries.csv`、`capacity_direction.csv`、`market.csv`、
`generators.csv`、`storage.csv`、`flex_load.csv`、`metadata.json`

---

## 8. 运行产物

```
runs/<RUN-ID>/
├── manifest.json                  # 输入哈希清单 + 全部版本指纹（4.9）
├── checkpoints.json               # 步骤级检查点，支持断点续跑（17.3）
├── run_outcome.json               # 退出码与结论摘要
├── snapshots/                     # 只读输入副本
├── config/                        # rules.yaml、engine_fingerprint、skill_registry、Skill Cards
├── model/  model_increment/       # Master.dss 与引用文件（LineCodes/Lines/…）
├── engine/                        # OpenDSS 工作目录与原生日志
├── results/
│   ├── quality_issues.csv         # 质量问题（blocking 标记）
│   ├── scenarios.json             # 场景集（含 slice_timestamps）
│   ├── bus_results.csv            # 逐节点相时间片电压
│   ├── element_results.csv        # 逐端子相电流/功率
│   ├── constraint_results.csv     # 约束明细（pass/fail/unknown）
│   ├── capacity.csv               # 容量结果
│   ├── search_trace.csv           # 搜索轨迹（每次试探的迭代号与状态）
│   ├── monotonicity.json          # 单调性验证
│   ├── management.json            # 管理口径分步计算
│   ├── grading.json               # 红黄绿分级
│   └── run_summary.json           # 结论摘要
├── verification/                  # verification.json、metric_diff.csv
├── report/                        # report.docx、report_manifest.json
├── review/                        # release_gate.json、publication.json
├── evidence/evidence.zip          # 封存证据包（含 evidence_index.json）
└── audit/events.jsonl             # 审计事件账本（9.3）
```

---

## 9. 部署环境

| 项目 | 值 |
|---|---|
| Python | 3.12.14（64 位） |
| 引擎接口 | OpenDSSDirect.py **0.9.4** |
| 底层引擎 | DSS C-API **0.14.5**（基于 OpenDSS SVN 3723） |
| 运行库位置 | `../opendss_demo/pylibs`（可用 `HC_OPENDSS_PYLIB` 覆盖） |
| 可选依赖 | `networkx`（拓扑）、`openpyxl`（读 xlsx）、`python-docx`（Word 报告）、`scipy`（SLSQP/LP） |
| 无 PyYAML 时 | `rules.py` 内置 YAML 子集解析器自动接管 |
| 无 networkx 时 | `validation.py` 退化为纯 Python 邻接表 |

**版本锁定**：`runtime_env.LOCKED_INTERFACE_VERSION = "0.9.4"`。
引擎版本不匹配时 `check_locked_version(strict=True)` 会阻断，
保证主计算与独立复算使用同一锁定版本（设计文档 9.1-4）。

---

## 10. 运维要点

- **只读快照**：`snapshots/` 中的文件被置为只读；任何清洗/换算生成派生版本并记录血缘。
- **复算不可绕过**：`verification` 复制冻结输入到全新目录，用新上下文重建模型；
  复算通过需 `mismatch_count == 0`。
- **发布门禁**：`review/release_gate.json` 列出未通过的门；
  `publication.json` 只有在审批绑定 `result_hash` 后才会写入正式发布记录。
- **审计**：`audit/events.jsonl` 每行含 `event_id, task_id, run_id, actor, action,
  object_ref, before_hash, after_hash, timestamp, source, result, reason`。

---

## 11. 待确认事项（设计文档第 16 章）

以下参数**必须由业务主管部门与配网专业专家审定**，工程中一律留空并阻断，
不得由研发或大模型自行设定生产默认值：

1. 红黄绿阈值、管理裕度、同时率、容量离散步长 → `rules.yaml` 的 `grading` / `management` / `search`
2. 电压/载流/反送/不平衡/短路的限值与持续时间判据 → `constraints`
3. 复算字段级容差 → `verification`
4. 概率集合半径（不得沿用参考代码的 N=100、α=0.99）
5. 审批角色、有效期、证据要求 → `approval_policy`
6. 部署网络区域、RPO/RTO、保留期

`rules/rules_template.yaml` 是据此编写的**待审模板**：14 类规则齐备但限值全为
`null`，用于正式任务时会被拒绝（退出码 2），从而避免未批准口径流入生产。

---

## 12. 运行监控台（前端页面）

`web/` 下是只读可视化页面，把 `runs/` 的运行产物渲染成可复核的界面，
用于专业复核、汇报与审计抽查。详见 [web/README.md](web/README.md)。

```powershell
& $py tools/export_dashboard.py --root runs/ACCEPT --out web/data.json
& $py -m http.server 8777 --directory web --bind 127.0.0.1
# 打开 http://127.0.0.1:8777/    深链示例 http://127.0.0.1:8777/#FD002
```

页面展示：算例总览、结论 KPI、**容量搜索轨迹**（每个试探点的可行/不可行/unknown
与绑定约束）、**并网申请校核**（存量越限 vs 申请引入的新增越限）、五类强制约束
构成、越限明细、**独立复算与发布门禁三道门**、可追溯性指纹、主配微容量边界与
接口协调轨迹。

页面遵循与主体工程相同的约束：

1. **不编造数值** —— 每个数字都来自运行产物；取不到就显示"未获取"，
   绝不用 0 或估算值填充（`data.json` 中为 `null`）。
2. **状态原样透出** —— `unknown` 不被当作通过、也不被当作不可行；
   灰色分级、未通过的门禁、未提交的审批均如实显示。
3. **结论可回链** —— 每个算例都带 snapshot / topology / rule / skill /
   engine / input_digest。
4. **口径一致** —— 单笔并网审查的统计取自"含申请场景"，页面显式标注口径，
   避免同一算例出现两套互相矛盾的数字。

页面零外部依赖（无 CDN、无字体、无第三方脚本），
`web/dashboard_standalone.html` 为数据内联的单文件离线版，双击即可打开。
