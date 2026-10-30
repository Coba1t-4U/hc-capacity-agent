"""pipeline.py — 唯一任务入口（设计文档 17.3 主流程执行表 / 17.10 CLI 契约）。

用法：
  python -m hc_agent.pipeline --task inputs/task.json --input-dir inputs
                              --output-dir runs/RUN-001

步骤（17.3）：
  S01 freeze_inputs        manifest.json、只读副本
  S02 validate_bundle      quality_issues.csv；blocking_count=0 才继续
  S03 build_case           Master.dss 与引用文件、scenarios.json
  S04 run_baseline         baseline 结果；全场景收敛且满足基线准入规则
  S05 evaluate_increment   分场景约束结果；失败记为 unknown，不能当 pass
  S06 search_capacity      capacity.csv、search_trace.csv
  S07 verify_run           verification.json、metric_diff.csv
  S08 generate_report      report.docx、approval.json
  S09 publish_approved     publication.json、evidence.zip

每一步写检查点及产物清单；恢复时重新核对输入与版本哈希，
不得跳过质量、复算或审批门禁（17.3）。

退出码：0 成功、2 输入阻断、3 计算失败、4 复算失败、5 未满足发布门禁。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent import __version__
from hc_agent.capacity_search import (
    SEARCH_AT_LEAST_UPPER,
    SEARCH_OK,
    SEARCH_UNKNOWN,
    SearchResult,
    TrialResult,
    check_monotonicity,
    search_capacity,
)
from hc_agent.errors import (
    STATUS_FAIL,
    STATUS_INTERRUPTED,
    EXIT_INTERRUPTED,
    EXIT_COMPUTE_FAILED,
    EXIT_INPUT_BLOCKED,
    EXIT_RELEASE_GATE_FAILED,
    EXIT_SUCCESS,
    EXIT_VERIFICATION_FAILED,
    ERR_DSS_NOT_CONVERGED,
    ComputeFailedError,
    HCAgentError,
    InputBlockedError,
    ReleaseGateError,
)
from hc_agent.evidence import (
    EvidenceLedger,
    clear_readonly_tree,
    freeze_inputs,
    read_json,
    sha256_obj,
    utc_now_iso,
    verify_file_immutable,
    write_json,
)
from hc_agent.io.ingest import InputBundle, load_input_bundle

def _capture_invocation(parameters: Dict[str, Any],
                       input_dir: Path, out_dir: Path) -> Dict[str, Any]:
    """记录"这次运算是怎么被触发的"，供界面「运行代码」页如实展示。

    记三样东西：
      * `argv`      —— 进程真实收到的命令行参数；
      * `channel`   —— 触发渠道（cli / gui / engine-host / 测试）；
      * `equivalent_cli` —— **可复现的等效命令**（用真实参数拼出来）。

    为什么还要 equivalent_cli：GUI 触发时 argv 是窗口程序的参数，
    对用户复现没有意义；拼一条等价 CLI 才是他能直接跑的东西。
    """
    argv = list(sys.argv) if sys.argv else []
    chan = os.environ.get("HC_LAUNCH_CHANNEL", "").strip()
    if not chan:
        # 没有显式标注时按 argv 猜：出现 -m hc_agent.cli 视为 cli，
        # 出现 --engine-host 视为 GUI 引擎宿主，其余归为"未知"
        joined = " ".join(argv)
        if "hc_agent.cli" in joined:
            chan = "cli"
        elif "--engine-host" in joined:
            chan = "engine-host"
        else:
            chan = "unknown"
    equiv = [
        "python", "-m", "hc_agent.cli", "run",
        "--input", str(input_dir),
        "--out", str(out_dir),
    ]
    ms = parameters.get("max_slices")
    if ms is not None:
        equiv += ["--max-slices", str(ms)]
    return {
        "argv": argv,
        "channel": chan,
        "cwd": os.getcwd(),
        "python": sys.executable,
        "equivalent_cli": equiv,
        "equiv_cmdline": " ".join(
            f'"{a}"' if (" " in a or "\\" in a) else a for a in equiv),
    }


from hc_agent.io.schema import (
    IssueCollector,
    as_float,
    as_str,
    write_csv,
)
from hc_agent.io.validation import raise_if_blocking, validate_bundle
from hc_agent.model.build_model import DSSModel, build_case
from hc_agent.model.constraints import (
    ConstraintEvaluator,
    ConstraintResult,
    summarize_constraints,
)
from hc_agent.model.powerflow import (
    ScenarioRun,
    apply_slice,
    make_new_pv_specs,
    require_baseline_admissible,
    run_baseline,
    run_scenario_set,
)
from hc_agent.model.scenarios import Scenario, build_scenarios
from hc_agent.rules import (
    RulePackage,
    apply_grading,
    apply_management_rules,
    check_release_gate,
    load_rule_package,
)
from hc_agent.runtime_env import check_locked_version, engine_fingerprint

SKILL_VERSION = "1.0.0"


class Pipeline:
    """任务流水线（设计文档 17.3）。"""

    def __init__(self, task_path, input_dir, output_dir, *,
                 engine_config: Optional[Dict[str, Any]] = None,
                 skip_verification: bool = False,
                 max_slices: Optional[int] = None,
                 allow_unapproved_rules: bool = False,
                 verbose: bool = True) -> None:
        self.task_path = Path(task_path).resolve()
        self.input_dir = Path(input_dir).resolve()
        self.out_dir = Path(output_dir).resolve()
        self.engine_config = engine_config or {}
        self.skip_verification = skip_verification
        self.max_slices = max_slices
        self.allow_unapproved_rules = allow_unapproved_rules
        self.verbose = verbose
        self.run_id = f"RUN-{time.strftime('%Y%m%d-%H%M%S')}"
        self.checkpoints: List[Dict[str, Any]] = []
        self.ledger: Optional[EvidenceLedger] = None
        self.bundle: Optional[InputBundle] = None
        self.rules: Optional[RulePackage] = None
        self.skill_registry = None
        self.model: Optional[DSSModel] = None
        self.scenarios: List[Scenario] = []
        self.baseline: Optional[ScenarioRun] = None
        self.search_result: Optional[SearchResult] = None
        search_result: Optional[SearchResult] = None
        self.managed: Dict[str, Any] = {}
        self.grading: Dict[str, Any] = {}
        self.access_check: Optional[Dict[str, Any]] = None
        self.baseline_constraints: List[ConstraintResult] = []
        # --- 运行时可靠性（移植自天枢一号，见 docs/移植自天枢一号.md）---
        # 崩溃可观测：必须在任何可能崩溃的初始化**之前**安装
        from hc_agent.runtime import crashlog, watchdog
        from hc_agent.runtime.state_store import StateStore
        crashlog.install()
        try:
            self.state_store: Optional[StateStore] = StateStore(
                self.out_dir, run_id=self.run_id, agent_version=__version__)
        except Exception:
            self.state_store = None
        self.watchdog = watchdog
        self.watchdog_outcome: Dict[str, Any] = {}
        # 供看门狗复检使用的适配器句柄（在 S04 创建、_finalize 前释放）
        self._probe_adapter: Optional[Any] = None
        # 外部格式转换说明（若本次输入由 xlsx/csv/json/MATPOWER 转换而来）
        self.conversion_report: Optional[Any] = None
        # 引擎通道选择结果（S03 填入）
        self.channel_info: Dict[str, Any] = {}
        self.baseline_summary: Dict[str, Any] = {}
        self.verification: Optional[Dict[str, Any]] = None
        self.t_start = time.perf_counter()

    # ------------------------------------------------------------ 工具
    def say(self, msg: str) -> None:
        if self.verbose:
            # 一律走 stderr：本模块会运行在 engine_host 子进程里，那里 stdout 是
            # **行分隔 JSON 协议**，任何 print 都会污染它，父进程随即解析失败。
            # 打包后实测踩过：run_task 的应答被进度行冲掉，界面上表现为"卡住"。
            print(f"[{time.strftime('%H:%M:%S')}] {msg}",
                  file=sys.stderr, flush=True)

    def checkpoint(self, step: str, status: str, **extra) -> None:
        rec = {"step": step, "status": status, "at": utc_now_iso(),
               "elapsed_s": round(time.perf_counter() - self.t_start, 3)}
        rec.update(extra)
        self.checkpoints.append(rec)
        # checkpoints.json 是**累积列表**，给人看进程轨迹。
        # 它非原子写，单独用它无法回答"死在哪一步"——进程被 Kill 时可能留下
        # 半截 JSON，解析失败又变成"不知道发生了什么"。
        write_json(self.out_dir / "checkpoints.json", self.checkpoints)
        # state.json 是**最新遗言**，原子写，给机器读"最后推进到哪一步"。
        # 两者用途不同，都要保留（移植自天枢一号，见 docs/移植自天枢一号.md）。
        if self.state_store is not None:
            self.state_store.persist(step, status=status,
                                     extra={"detail": extra} if extra else None)
        if self.ledger:
            self.ledger.append(f"step:{step}", object_ref=step,
                               result=status,
                               reason=extra.get("note", ""))
        self.say(f"{step} -> {status}")

    def _dir(self, *parts: str) -> Path:
        p = self.out_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -------------------------------------------------------------- S01
    def s01_freeze(self) -> Dict[str, Any]:
        """S01 冻结输入（设计文档 17.3 / 5.4）。"""
        self._dir()
        self.ledger = EvidenceLedger(self.out_dir / "audit" / "events.jsonl",
                                     run_id=self.run_id)
        # 把 task.json 一起纳入输入快照。
        # 重跑时 _stage 可能残留上一次的只读副本，先清空再铺。
        stage = self._dir("_stage")
        clear_readonly_tree(stage)
        for f in stage.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        for f in self.input_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, stage / f.name)

        manifest = freeze_inputs(stage, self.out_dir / "snapshots",
                                 read_only=True)
        manifest.update({
            "run_id": self.run_id,
            "task_id": "",
            "agent_version": __version__,
            "skill_version": SKILL_VERSION,
            "run_dir": str(self.out_dir),
            "started_at": utc_now_iso(),
            "engine_fingerprint": engine_fingerprint(),
            # 运行参数写入清单（设计文档 4.3：模型温度、采样参数、提示词版本和
            # 技能版本写入运行清单）。max_slices 属运行参数，复算必须沿用同一
            # 值，否则场景覆盖面不同、比对不成立。
            "parameters": {"max_slices": self.max_slices},
        })
        # 记录实际触发方式（界面「运行代码」页据此区分"实际执行/等效复现"）
        manifest["invocation"] = _capture_invocation(
            manifest["parameters"], self.input_dir, self.out_dir)
        write_json(self.out_dir / "manifest.json", manifest)
        self.ledger.append("freeze_inputs", object_ref=str(self.input_dir),
                           after_hash=manifest.get("input_digest"),
                           result="ok",
                           reason=f"{len(manifest['inputs'])} 个输入文件已冻结为只读")
        self.checkpoint("S01_freeze_inputs", "ok",
                        input_count=len(manifest["inputs"]),
                        input_digest=manifest.get("input_digest"))
        return manifest

    # -------------------------------------------------------------- S02
    def s02_validate(self) -> InputBundle:
        """S02 输入校验（设计文档 17.3：blocking_count=0 才继续）。"""
        snap = self.out_dir / "snapshots"
        col = IssueCollector()
        bundle = load_input_bundle(snap, col)
        validate_bundle(bundle, require_profiles=True)
        # 开发文档 9.5：quality_issues.csv 是**运行根目录**的契约文件。
        # 同时保留 results/ 下那份（既有查看器与测试在读它），两处一致。
        qi_path = self._dir("results") / "quality_issues.csv"
        col.write(qi_path)
        try:
            from hc_agent.io import quality as _q
            _q.write(self.out_dir, col)
        except Exception:
            pass

        self.bundle = bundle
        blocking = col.blocking
        write_json(self.out_dir / "results" / "validation_summary.json", {
            "total_issues": len(col.issues),
            "blocking_count": len(blocking),
            "issued_at": utc_now_iso(),
        })
        if blocking:
            self.checkpoint("S02_validate_bundle", "blocked",
                            blocking_count=len(blocking),
                            note="输入存在阻断缺陷，输出补充清单")
            raise InputBlockedError(
                f"输入校验未通过：{len(blocking)} 项阻断缺陷",
                issues=[i.to_row(n + 1) for n, i in enumerate(blocking)])
        self.checkpoint("S02_validate_bundle", "ok",
                        total_issues=len(col.issues), blocking_count=0)
        return bundle

    # -------------------------------------------------------------- S03
    def s03_build(self) -> Tuple[DSSModel, List[Scenario]]:
        """S03 建模与场景（设计文档 17.3）。

        同时装载技能注册中心：同一任务必须冻结 skill_version
        （设计文档 4.9），清单写入 manifest.skill_version。
        """
        col = IssueCollector()
        rules_path = self.out_dir / "snapshots" / "rules.yaml"
        # rules.yaml 缺失属于**输入阻断**（退出码 2），不是计算失败（退出码 3）。
        # 设计文档表 47 将 rules.yaml 列为"全部任务必需"。
        if not rules_path.exists():
            col.add(file="rules.yaml", code="FILE_MISSING",
                    message="rules.yaml 不存在（全部任务必需：已批准限值、"
                            "搜索与容量管理、容差、审批门禁）",
                    severity="error", blocking=True)
            col.write(self._dir("results") / "quality_issues.csv")
            try:
                from hc_agent.io import quality as _q
                _q.write(self.out_dir, col)
            except Exception:
                pass
            self.checkpoint("S03_build_case", "blocked",
                            blocking_count=len(col.blocking),
                            note="缺少必需规则包")
            raise InputBlockedError("缺少必需规则包 rules.yaml",
                                    issues=[i.to_row(n + 1)
                                            for n, i in enumerate(col.blocking)])
        rules = load_rule_package(rules_path, collector=col,
                                  require_approved=not self.allow_unapproved_rules)
        if col.blocking:
            col.write(self._dir("results") / "quality_issues.csv")
            try:
                from hc_agent.io import quality as _q
                _q.write(self.out_dir, col)
            except Exception:
                pass
            raise_if_blocking(col)
        self.rules = rules
        (self._dir("config") / "rules.yaml").write_text(
            rules_path.read_text(encoding="utf-8"), encoding="utf-8")

        # 技能注册（设计文档 4.4 / 4.7）：只选用已批准技能
        try:
            from hc_agent.skills import (default_registry_path,
                                         export_skill_cards,
                                         load_skill_registry)
            reg = load_skill_registry(default_registry_path())
            self.skill_registry = reg
            write_json(self._dir("config") / "skill_registry.json",
                       reg.summary())
            export_skill_cards(reg, self._dir("config") / "skill_cards")
            # 把技能版本冻结进运行清单（设计文档 4.9：冻结 skill_version）
            manifest = read_json(self.out_dir / "manifest.json")
            manifest["skill_version"] = reg.version_manifest()
            manifest["rule_version"] = rules.rule_package
            manifest["snapshot_id"] = as_str(
                (self.bundle.meta or {}).get("snapshot_id"))
            manifest["topology_version"] = as_str(
                (self.bundle.meta or {}).get("topology_version"))
            manifest["task_id"] = as_str((self.bundle.task or {}).get("task_id"))
            write_json(self.out_dir / "manifest.json", manifest)
        except Exception as exc:
            raise ComputeFailedError(f"技能注册中心加载失败: {exc}",
                                     code="SKILL_REGISTRY_INVALID")

        # 引擎版本兼容矩阵核对（设计文档 8.2）
        fp = check_locked_version(strict=False)
        if not fp.get("available"):
            raise ComputeFailedError(
                "OpenDSS 引擎不可用，无法执行任何电气计算",
                code="DSS_DEPENDENCY_LOAD_FAILED")

        # 引擎通道选择与降级（移植自天枢一号，见 docs/移植自天枢一号.md）
        from hc_agent.engines import engine_channels
        ch, ch_info = engine_channels.select_channel(
            preferred=self.engine_config.get("channel"),
            fallback=bool(self.engine_config.get("fallback", True)))
        self.channel_info = ch_info
        fp["channel"] = ch
        fp["channel_info"] = ch_info
        if ch is None:
            # 选不到通道时**明确阻断**，不静默使用一个不满足要求的通道
            raise ComputeFailedError(
                f"没有可用的引擎通道：{ch_info.get('reason')}；"
                f"已排除的通道：{ch_info.get('rejected')}",
                code="DSS_DEPENDENCY_LOAD_FAILED")
        if ch_info.get("fell_back"):
            self.say(f"引擎通道降级：{ch_info.get('requested')} 不可用，"
                     f"改用 {ch}")
        write_json(self._dir("config") / "engine_channels.json", ch_info)

        bundle = self.bundle
        assert bundle is not None
        model = build_case(bundle, self.engine_config,
                           out_dir=self._dir("model"))
        self.model = model

        scenarios = build_scenarios(bundle, rules, max_slices=self.max_slices)
        self.scenarios = scenarios
        write_json(self._dir("results") / "scenarios.json",
                   {"scenarios": [s.to_dict() for s in scenarios]})

        write_json(self._dir("config") / "engine_fingerprint.json", fp)
        from hc_agent.evidence import sha256_file
        master_hash = sha256_file(model.master_path) if model.master_path else ""
        write_json(self._dir("config") / "model_manifest.json", {
            "model_files": model.files,
            "master_hash": master_hash,
            "id_map": model.id_map,
            "notes": model.notes,
        })
        self.checkpoint("S03_build_case", "ok",
                        scenarios=len(scenarios),
                        slices=sum(len(s.slices) for s in scenarios),
                        model_hash=master_hash[:12])
        return model, scenarios

    # -------------------------------------------------------------- S04/S05
    def _make_feasibility(self, adapter, model_new: DSSModel,
                          new_pv_ids: Sequence[str],
                          weights: Dict[str, float], solver: Dict[str, Any],
                          trace: List[TrialResult],
                          fault_bus: str = "",
                          fault_spec: Optional[Dict[str, Any]] = None):
        """构造可行性判定函数（设计文档 17.12）。

        除潮流类约束外，若算例请求了 shortcircuit 分析且提供了故障模型，
        则在每个试探点执行短路研究（SK-09，表 55 run_fault_study），
        使 SHORT_CIRCUIT_LIMIT 由 unknown 变为真实的 pass/fail；
        否则保持 unknown（能力未覆盖），而不是伪装成通过。
        """
        evaluator = ConstraintEvaluator(self.rules)
        counter = {"n": 0}
        task = self.bundle.task or {}
        wants_sc = "shortcircuit" in [as_str(a) for a in
                                      (task.get("analysis_types") or [])]
        has_fault_models = bool(self.bundle.fault_models)

        def feasible(delta_kw: float, iteration: int) -> TrialResult:
            counter["n"] += 1
            it = abs(iteration) if iteration else counter["n"]
            run = run_scenario_set(
                adapter, self.bundle, model_new, self.scenarios,
                increment_kw=delta_kw, allocation=weights,
                new_pv_ids=list(new_pv_ids), solver=solver)
            if not run.all_converged:
                t = TrialResult(
                    iteration=it, delta_kw=delta_kw, status="unknown",
                    binding_constraint="NON_CONVERGENCE",
                    result_hash=sha256_obj({"d": delta_kw, "u": True}),
                    detail={"unserved": run.unserved_slices[:5],
                            "reason": "不收敛/超时属 unknown，"
                                      "不可等同于电气不可行"})
                trace.append(t)
                return t

            # ---- SK-09 短路容量校核（仅在请求且具备模型时执行）
            fault_results: Optional[Dict[str, Any]] = None
            if wants_sc and fault_bus:
                if has_fault_models:
                    try:
                        fc = adapter.short_circuit(
                            fault_spec=fault_spec or {"bus": fault_bus},
                            scenario_id="SC",
                            timestamp=(self.scenarios[0].slices[0].timestamp
                                       if self.scenarios else ""))
                        fault_results = {
                            "available": fc.status == "ok",
                            "bus": fault_bus,
                            "fault_type": (fault_spec or {}).get(
                                "fault_type", "single_line_ground"),
                            "fault_element_currents":
                                (fc.metrics or {}).get(
                                    "fault_element_currents") or [],
                            "bus_isc_a": (fc.metrics or {}).get("bus_isc_a"),
                        }
                    except Exception as exc:
                        fault_results = {
                            "available": False,
                            "reason": f"短路研究异常: {type(exc).__name__}: {exc}"}
                else:
                    fault_results = {
                        "available": False,
                        "reason": "未提供 fault_models.csv，"
                                  "短路能力未覆盖（设计文档 17.11）"}

            results = evaluator.evaluate(run, fault_results=fault_results)
            counts = ConstraintEvaluator.count_by_status(results)
            binding = ConstraintEvaluator.binding(results)
            b = binding[0] if binding else None
            # 可行性判定用 feasible（无 fail），而非 all_pass（无 fail 且无 unknown）。
            # 设计文档 17.12：unknown 不可等同于电气不可行，因此不得作为
            # 不可行上界；若用 all_pass，任一 unknown 都会让全部试探点不可行、
            # 容量恒为 0。unknown 仍在 counts 与分级中显式暴露，使 level 保持
            # 灰色、正式结论不可发布。
            status = ("feasible" if ConstraintEvaluator.feasible(results)
                      else "infeasible")
            t = TrialResult(
                iteration=it, delta_kw=delta_kw, status=status,
                binding_constraint=(b.constraint_id if b else ""),
                scenario_id=(b.scenario_id if b else ""),
                timestamp=(b.timestamp if b else ""),
                constraint_status=counts,
                result_hash=sha256_obj([
                    {"o": r.object_id, "c": r.constraint_id, "s": r.status,
                     "v": round(r.value, 9) if r.value is not None else None}
                    for r in results]),
                detail=({
                    "object_id": b.object_id, "metric": b.metric,
                    "value": b.value, "unit": b.unit,
                    "limit_lower": b.lower_limit, "limit_upper": b.upper_limit,
                    "margin": b.margin,
                } if b else {}))
            trace.append(t)
            return t

        return feasible, evaluator

    def s04_s05_s06(self) -> Dict[str, Any]:
        """S04 基线 → S05 增量校核 → S06 容量搜索（设计文档 17.3）。"""
        from hc_agent.engines.opendss_adapter import OpenDSSAdapter

        bundle = self.bundle
        model = self.model
        rules = self.rules
        task = bundle.task
        solver = task.get("solver") or {}
        assert bundle and model and rules

        search_cfg = dict(rules.search or {})
        for k in ("lower_kw", "upper_kw", "precision_kw", "max_iter"):
            if k not in search_cfg and k in (task.get("search") or {}):
                search_cfg[k] = task["search"][k]

        specs, weights = make_new_pv_specs(bundle, task)
        new_pv_ids = [s["pv_id"] for s in specs]
        allocation_mode = as_str(task.get("allocation_mode"))
        if not allocation_mode:
            allocation_mode = as_str(search_cfg.get("allocation_mode")) \
                or "fixed_weights"
        # 短路校核的故障点：取候选接入点（新增电源对短路水平影响最直接的节点）
        fault_bus = ""
        for n in (task.get("candidate_nodes") or []):
            if isinstance(n, dict) and as_str(n.get("node_id")):
                fault_bus = as_str(n["node_id"])
                break
        fault_spec = {
            "bus": fault_bus,
            "fault_type": as_str((task.get("short_circuit") or {}).get(
                "fault_type")) or "three_phase",
            "r_fault_ohm": as_float((task.get("short_circuit") or {}).get(
                "r_fault_ohm")) or 0.0,
        }

        trace: List[TrialResult] = []
        baseline_results: List[ConstraintResult] = []
        baseline_summary: Dict[str, Any] = {}
        model_new: Optional[DSSModel] = None
        adapter = OpenDSSAdapter(work_dir=self._dir("engine"),
                                 engine_config=self.engine_config)
        # 交给看门狗：引擎类失败时可立刻复检这个适配器是否还活着
        self._probe_adapter = adapter

        try:
            with adapter:
                adapter.build_case(model.master_text(), case_name="base",
                                   node_phase_map=model.node_phase_map,
                                   id_map=model.id_map, base_kv=model.base_kv,
                                   equipment=model.equipment)

                # ---------------- S04 基线
                self.say("S04 执行基线潮流（全场景）…")
                baseline = run_baseline(adapter, bundle, model, self.scenarios,
                                        solver=solver)
                self.baseline = baseline
                ok, problems = require_baseline_admissible(baseline)
                evaluator = ConstraintEvaluator(rules)
                # 基线的短路校核（SK-09）：与试探点使用同一口径，
                # 使基线约束明细与复算逐行可比（设计文档 9.1-4）
                baseline_fault: Optional[Dict[str, Any]] = None
                if ("shortcircuit" in [as_str(a) for a in
                                       (task.get("analysis_types") or [])]
                        and fault_bus and bundle.fault_models):
                    try:
                        fc = adapter.short_circuit(
                            fault_spec=fault_spec, scenario_id="SC",
                            timestamp=(self.scenarios[0].slices[0].timestamp
                                       if self.scenarios else ""))
                        baseline_fault = {
                            "available": fc.status == "ok", "bus": fault_bus,
                            "fault_type": fault_spec.get("fault_type"),
                            "fault_element_currents":
                                (fc.metrics or {}).get(
                                    "fault_element_currents") or [],
                            "bus_isc_a": (fc.metrics or {}).get("bus_isc_a"),
                        }
                    except Exception as exc:
                        baseline_fault = {
                            "available": False,
                            "reason": f"短路研究异常: {type(exc).__name__}: {exc}"}
                baseline_results = evaluator.evaluate(baseline,
                                                      fault_results=baseline_fault)
                baseline_summary = summarize_constraints(baseline_results)
                # 冻结基线约束结论，供 run_summary 与独立复算比对复用，
                # 避免在报告中重复求解产生第二套数字（设计文档 9.1-1）
                self.baseline_constraints = list(baseline_results)
                self.baseline_summary = dict(baseline_summary)
                # 冻结基线约束明细，作为独立复算的逐行比对基准
                # （设计文档 9.1-4：比较输入哈希、场景覆盖、收敛状态、约束判定、
                # 电压电流及容量边界）
                write_csv(self._dir("results") / "baseline_constraint_rows.csv",
                          [r.to_row() for r in baseline_results],
                          columns=["scenario_id", "timestamp", "object_id",
                                   "constraint_id", "metric", "value", "unit",
                                   "lower_limit", "upper_limit", "margin",
                                   "status", "evidence_ref", "detail"])

                self._write_run_outputs(baseline, baseline_results, "baseline")
                if not ok:
                    self.checkpoint("S04_run_baseline", "blocked",
                                    problems=problems,
                                    note="基线未全场景收敛，按设计不给出可开放结论")
                    return {
                        "stage": "S04", "status": "blocked",
                        "capacity_physical_kw": None,
                        "baseline_summary": baseline_summary,
                        "problems": problems,
                        "search": None,
                    }

                base_fail = baseline_summary.get("fail", 0)
                if base_fail:
                    self.say(f"S04 基线存在 {base_fail} 项存量越限"
                             f"（先输出存量问题）")
                self.checkpoint("S04_run_baseline", "ok",
                                slices=len(baseline.slices),
                                constraint_pass=baseline_summary.get("pass", 0),
                                constraint_fail=base_fail,
                                baseline_admissible=True)

                if not new_pv_ids:
                    self.checkpoint("S06_search_capacity", "skipped",
                                    note="无候选接入点，跳过容量搜索")
                    return {"stage": "S06", "status": "no_candidates",
                            "capacity_physical_kw": None,
                            "baseline_summary": baseline_summary,
                            "search": None}

                # ---------------- 单笔并网审查：按申请容量校核，不做容量搜索
                #
                # 设计文档 17.3：单笔并网审查在 S05 使用申请容量而不是容量搜索
                # 试探值，仍执行 S07—S09。设计文档 7.2-3：分别计算无新增申请的
                # 基线与含申请的增量场景，并定位新增申请引起的差异。
                application = bundle.application
                if as_str(task.get("business_scene")) in ("access_check",
                                                          "remediation_review") \
                        and application:
                    req_kw = as_float(application.get("requested_capacity_kw")) \
                        or 0.0
                    self.say(f"S05 按申请容量 {req_kw:g} kW 校核（单笔审查）…")
                    model_new = build_case(bundle, self.engine_config,
                                           out_dir=self._dir("model_increment"),
                                           extra_pv=specs)
                    adapter.build_case(model_new.master_text(),
                                       case_name="increment",
                                       node_phase_map=model_new.node_phase_map,
                                       id_map=model_new.id_map,
                                       base_kv=model_new.base_kv,
                                       equipment=model_new.equipment)
                    app_run = run_scenario_set(
                        adapter, bundle, model_new, self.scenarios,
                        increment_kw=req_kw, allocation=weights,
                        new_pv_ids=new_pv_ids, solver=solver)
                    app_results = evaluator.evaluate(app_run)
                    app_summary = summarize_constraints(app_results)
                    self._write_run_outputs(app_run, app_results, "application")
                    if not app_run.all_converged:
                        decision = "无法评估"
                        self.checkpoint("S05_access_check", "unknown",
                                        note="含申请场景未全收敛，结论为无法评估")
                    else:
                        decision = ("可行"
                                    if ConstraintEvaluator.feasible(app_results)
                                    else "不可行")
                        self.checkpoint("S05_access_check", "ok",
                                        requested_capacity_kw=req_kw,
                                        decision_candidate=decision,
                                        pass_count=app_summary.get("pass", 0),
                                        fail_count=app_summary.get("fail", 0))
                    # 存量问题与新增变化的区分（17.3：不得一概推导为申请导致）
                    base_fail_keys = {
                        (r.constraint_id, r.object_id) for r in baseline_results
                        if r.status == STATUS_FAIL}
                    new_fail_keys = {
                        (r.constraint_id, r.object_id) for r in app_results
                        if r.status == STATUS_FAIL}
                    added = sorted(new_fail_keys - base_fail_keys)
                    self.access_check = {
                        "application_id": as_str(application.get("application_id")),
                        "requested_capacity_kw": req_kw,
                        "decision_candidate": decision,
                        "constraint_summary": app_summary,
                        "baseline_fail": len(base_fail_keys),
                        "application_fail": len(new_fail_keys),
                        "newly_induced": [
                            {"constraint_id": c, "object_id": o}
                            for c, o in added],
                        "preexisting": [
                            {"constraint_id": c, "object_id": o}
                            for c, o in sorted(base_fail_keys)],
                        "binding_constraints": app_summary.get(
                            "binding_constraints", []),
                    }
                    write_json(self._dir("results") / "access_check.json",
                               self.access_check)
                    return {
                        "stage": "S05", "status": "ok",
                        "capacity_physical_kw": req_kw,
                        "capacity_managed_kw": None,
                        "level": ("green" if decision == "可行" else "red"),
                        "level_reason": f"单笔并网审查结论：{decision}",
                        "binding_constraint": (
                            (self.access_check["binding_constraints"] or
                             [{}])[0].get("constraint_id", "")
                            if decision == "不可行" else ""),
                        "search_status": "access_check",
                        "baseline_summary": baseline_summary,
                        "access_check": self.access_check,
                        "grading": {"level": ("green" if decision == "可行"
                                              else "red"),
                                    "formal": True,
                                    "reason": f"申请校核结论：{decision}"},
                        "search": None,
                        "new_pv_ids": new_pv_ids,
                        "weights": weights,
                    }

                # ---------------- S05 增量模型
                self.say("S05 构建含新增容量的算例…")
                model_new = build_case(bundle, self.engine_config,
                                       out_dir=self._dir("model_increment"),
                                       extra_pv=specs)
                adapter.build_case(model_new.master_text(),
                                   case_name="increment",
                                   node_phase_map=model_new.node_phase_map,
                                   id_map=model_new.id_map,
                                   base_kv=model_new.base_kv,
                                   equipment=model_new.equipment)

                feasible, evaluator = self._make_feasibility(
                    adapter, model_new, new_pv_ids, weights, solver, trace,
                    fault_bus=fault_bus, fault_spec=fault_spec)

                # ---------------- S06 容量搜索
                mono_cfg = dict(search_cfg)
                if self.max_slices:
                    # 单调性验证样本量受时间片数限制
                    pass
                self.say("S06 验证单调性…")
                mono = check_monotonicity(feasible, mono_cfg, samples=5)
                write_json(self._dir("results") / "monotonicity.json", mono)

                self.say(f"S06 容量搜索（方法="
                         f"{'bisect' if mono['validated'] else 'discrete_scan'}）…")
                result = search_capacity(
                    feasible, search_cfg,
                    monotonicity_validated=mono.get("validated"),
                    allocation_mode=allocation_mode)
                result.trace = trace
                self.search_result = result
                self.checkpoint("S06_search_capacity", "ok",
                                method=result.method,
                                capacity=result.capacity_physical_kw,
                                search_status=result.search_status,
                                iterations=result.iterations)
        finally:
            adapter.close()

        # ---------------- 管理口径与分级
        reservations = sum(bundle.reservations_by_object().values())
        self.managed = apply_management_rules(
            self.search_result.capacity_physical_kw if self.search_result else None,
            reservations, rules)
        write_json(self._dir("results") / "management.json", self.managed)

        final_results = baseline_results
        if self.search_result and self.search_result.trace:
            last_feasible = [t for t in self.search_result.trace
                             if t.status == "feasible"]
        self.grading = apply_grading(
            self.managed.get("capacity_managed_kw"), final_results, rules,
            data_risk=bool(self.search_result and self.search_result.unknowns))
        write_json(self._dir("results") / "grading.json", self.grading)

        return {
            "stage": "S06", "status": "ok",
            "capacity_physical_kw": (self.search_result.capacity_physical_kw
                                     if self.search_result else None),
            "capacity_managed_kw": self.managed.get("capacity_managed_kw"),
            "level": self.grading.get("level"),
            "level_reason": self.grading.get("reason"),
            "binding_constraint": (self.search_result.binding_constraint
                                   if self.search_result else ""),
            "binding_detail": (self.search_result.binding_detail
                               if self.search_result else {}),
            "search_status": (self.search_result.search_status
                              if self.search_result else ""),
            "baseline_summary": baseline_summary,
            "management": self.managed,
            "grading": self.grading,
            "search": self.search_result,
            "new_pv_ids": new_pv_ids,
            "weights": weights,
        }

    def _write_run_outputs(self, run: ScenarioRun,
                           results: Sequence[ConstraintResult],
                           tag: str) -> None:
        res = self._dir("results")
        suffix = "" if tag == "baseline" else f"_{tag}"
        buses = [r for r in run.slices if r.get("kind") == "bus"]
        elems = [r for r in run.slices if r.get("kind") == "element"]
        if buses:
            write_csv(res / f"bus_results{suffix}.csv", buses,
                      columns=["scenario_id", "timestamp", "node_id", "bus",
                               "node", "phase", "v_v", "v_pu", "angle_deg",
                               "kv_base", "converged"])
        if elems:
            write_csv(res / f"element_results{suffix}.csv", elems,
                      columns=["scenario_id", "timestamp", "equipment_id",
                               "element_class", "terminal", "phase", "p_kw",
                               "q_kvar", "i_a", "rating_a", "loading_pct"])
        if tag == "baseline" and results:
            write_csv(res / "constraint_results.csv",
                      [r.to_row() for r in results],
                      columns=["scenario_id", "timestamp", "object_id",
                               "constraint_id", "metric", "value", "unit",
                               "lower_limit", "upper_limit", "margin",
                               "status", "evidence_ref", "detail"])

    # -------------------------------------------------------------- S07
    def s07_verify(self) -> Optional[Dict[str, Any]]:
        """S07 独立复算（设计文档 17.3 / 9.1）。

        注意顺序：复算器需要主计算的冻结结论（run_summary.json）才能比对，
        故本步先落盘主计算结果摘要，再启动独立复算。
        （设计文档 9.1-1：主计算完成后冻结输入清单、规则、技能、引擎和
        参数指纹，然后才由验证器创建独立 run_id 复算。）
        """
        if self.skip_verification:
            self.checkpoint("S07_verify_run", "skipped",
                            note="按调用参数跳过独立复算（禁止发布）")
            return None
        from hc_agent.verification import verify_run

        # 冻结主计算结果摘要，供独立复算比对
        summary = self._run_summary()
        write_json(self._dir("results") / "run_summary.json", summary)
        if self.search_result:
            write_csv(self._dir("results") / "search_trace.csv",
                      [t.to_row() for t in self.search_result.trace],
                      columns=["iteration", "delta_kw", "status",
                               "binding_constraint", "scenario_id",
                               "timestamp", "pass_count", "fail_count",
                               "unknown_count", "engine_run_ref",
                               "result_hash"])
            write_csv(self._dir("results") / "capacity.csv",
                      [self._capacity_row(summary)],
                      columns=["object_id", "allocation_id", "window_start",
                               "window_end", "capacity_physical_kw",
                               "capacity_managed_kw", "search_lower_kw",
                               "search_upper_kw", "search_status", "level",
                               "binding_constraint", "result_hash"])

        self.say("S07 独立复算（全新目录、全新进程、从冻结输入重建）…")
        vout = self._dir("verification")
        try:
            result = verify_run(self.out_dir / "manifest.json",
                                output_dir=vout,
                                engine_config=self.engine_config)
        except Exception as exc:
            self.checkpoint("S07_verify_run", "failed",
                            error=f"{type(exc).__name__}: {exc}")
            self.verification = {"pass": False,
                                 "error": f"{type(exc).__name__}: {exc}"}
            return self.verification
        self.verification = result
        self.checkpoint("S07_verify_run",
                        "ok" if result.get("pass") else "failed",
                        mismatch_count=(result.get("comparison") or {}
                                        ).get("mismatch_count"))
        return result

    # -------------------------------------------------------------- S08/S09
    def s08_report(self) -> Dict[str, Any]:
        """S08 报告与发布门禁（设计文档 17.3 / 11.2）。

        独立复算 + 专业复核 + 发布审批三道门，任何一项失败均不得发布。
        """
        from hc_agent.reporting import generate_report

        summary = self._run_summary()
        write_json(self._dir("results") / "run_summary.json", summary)

        report = generate_report(self.out_dir, self.verification)

        gate = check_release_gate(
            self.rules,
            verification_passed=bool(self.verification
                                     and self.verification.get("pass")),
            review_approved=False,      # 人工复核未提交 → 门禁未通过
            approval_ref=None,          # 审批未提交
            result_hash=report.get("result_hash"))
        write_json(self._dir("review") / "release_gate.json", gate)
        self.checkpoint("S08_generate_report", "ok",
                        report=report.get("report_path"),
                        gate_passed=gate["passed"],
                        failed_gates=gate["failed_gates"])
        return {"report": report, "gate": gate}

    def s09_seal(self, gate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """S09 封存证据包（设计文档 17.3）。

        只有审批绑定 result_hash 后才能发布正式结果。
        """
        from hc_agent.evidence import seal_evidence

        ev = seal_evidence(self.out_dir, self.out_dir / "evidence" /
                           "evidence.zip")
        self.checkpoint("S09_seal_evidence", "ok",
                        files=ev.get("file_count"),
                        digest=ev.get("package_digest"))
        publication = {
            "publication_id": None,
            "status": "not_published",
            "reason": "发布门禁未通过（缺人工复核与审批）；"
                      "审批绑定 result_hash 后才能发布",
            "failed_gates": gate.get("failed_gates"),
            "candidate_result_hash": gate.get("result_hash"),
            "scope": (self.bundle.task.get("scope") if self.bundle else {}),
            "evidence_package": ev.get("package_path"),
            "evidence_digest": ev.get("package_digest"),
            "generated_at": utc_now_iso(),
        }
        write_json(self._dir("review") / "publication.json", publication)
        return publication

    # ------------------------------------------------------------ 汇总
    def _capacity_row(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        task = self.bundle.task if self.bundle else {}
        scope = task.get("scope") or {}
        ids = scope.get("ids") or []
        node_ids = [as_str(n.get("node_id"))
                    for n in (task.get("candidate_nodes") or [])
                    if isinstance(n, dict)]
        return {
            "object_id": ",".join(ids) or "SYSTEM",
            "allocation_id": ",".join(node_ids),
            "window_start": (task.get("time_window") or {}).get("start"),
            "window_end": (task.get("time_window") or {}).get("end"),
            "capacity_physical_kw": summary.get("capacity_physical_kw"),
            "capacity_managed_kw": summary.get("capacity_managed_kw"),
            "search_lower_kw": (self.rules.search or {}).get("lower_kw"),
            "search_upper_kw": (self.rules.search or {}).get("upper_kw"),
            "search_status": summary.get("search_status"),
            "level": summary.get("level"),
            "binding_constraint": summary.get("binding_constraint"),
            "result_hash": sha256_obj({
                "capacity_physical_kw": summary.get("capacity_physical_kw"),
                "capacity_managed_kw": summary.get("capacity_managed_kw"),
                "level": summary.get("level"),
                "search_status": summary.get("search_status")}),
        }

    def _run_summary(self) -> Dict[str, Any]:
        task = self.bundle.task if self.bundle else {}
        tw = task.get("time_window") or {}
        scope = task.get("scope") or {}
        manifest = read_json(self.out_dir / "manifest.json")
        base = self.baseline
        sr = self.search_result
        return {
            "run_id": self.run_id,
            "task_id": task.get("task_id", ""),
            "business_scene": task.get("business_scene", ""),
            "scope_type": scope.get("type", ""),
            "scope_ids": ",".join(as_str(x) for x in (scope.get("ids") or [])),
            "time_window_start": tw.get("start", ""),
            "time_window_end": tw.get("end", ""),
            "snapshot_id": task.get("snapshot_id", ""),
            "rule_package": (self.rules.rule_package if self.rules else ""),
            "skill_version": SKILL_VERSION,
            "scenario_count": len(self.scenarios),
            "scenario_ids": [s.scenario_id for s in self.scenarios],
            "slice_count": sum(len(s.slices) for s in self.scenarios),
            "all_converged": bool(base and base.all_converged),
            "baseline_constraint_summary": self.baseline_summary,
            "constraint_summary": self.baseline_summary,
            # 约束明细行级摘要：独立复算据此逐行比对（设计文档 9.1-4）
            "constraint_rows": (self._constraint_rows_digest()),
            "search_method": (sr.method if sr else ""),
            "search_iterations": (sr.iterations if sr else 0),
            "search_status": (sr.search_status if sr else ""),
            "search_notes": (sr.notes if sr else []),
            "feasible_lower_kw": (sr.feasible_lower_kw if sr else None),
            "infeasible_upper_kw": (sr.infeasible_upper_kw if sr else None),
            "capacity_physical_kw": (sr.capacity_physical_kw if sr else None),
            "capacity_managed_kw": self.managed.get("capacity_managed_kw"),
            "binding_constraint": (sr.binding_constraint if sr else ""),
            "binding_detail": (sr.binding_detail if sr else {}),
            "level": self.grading.get("level", "grey"),
            "level_reason": self.grading.get("reason", ""),
            "unknowns": (sr.unknowns if sr else []),
            "risks": self._collect_risks(),
            "evidence_refs": [
                "manifest.json", "results/capacity.csv",
                "results/constraint_results.csv", "results/search_trace.csv",
                "results/bus_results.csv", "results/element_results.csv",
                "verification/verification.json", "config/rules.yaml",
                "audit/events.jsonl",
            ],
            "input_digest": manifest.get("input_digest"),
            "engine_fingerprint": manifest.get("engine_fingerprint"),
            # 本次求解口径声明（移植自天枢一号）：由运行产物推导，
            # 不让报告作者手写，避免口径陈述与代码漂移
            "caliber": self._build_caliber(),
            "generated_at": utc_now_iso(),
        }

    def _build_caliber(self) -> Dict[str, Any]:
        try:
            from hc_agent.renderers.caliber import build_caliber
            conv = None
            if getattr(self, "conversion_report", None):
                conv = self.conversion_report.to_dict()
            return build_caliber(
                task=(self.bundle.task if self.bundle else {}),
                manifest=read_json(self.out_dir / "manifest.json"),
                summary={"scenario_count": len(self.scenarios),
                         "slice_count": sum(len(s.slices)
                                            for s in self.scenarios)},
                conversion=conv,
                # 场景覆盖的说明应当是"覆盖怎么来的"（场景类别），
                # **不是**接入校核结论。此前误把 decision_candidate（"可行"）
                # 塞进这一格，口径表里于是出现牛头不对马嘴的说明（实测踩过）。
                scenario_basis=("、".join(sorted({
                    str(getattr(s, "scenario_class", "") or "")
                    for s in self.scenarios} - {""})) or "")).to_dict()
        except Exception as exc:
            # 口径声明失败不应让报告生成失败；但必须留下痕迹
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _constraint_rows_digest(self) -> Dict[str, Any]:
        """基线约束明细的行级摘要（主计算侧，供独立复算逐行比对）。"""
        from hc_agent.verification import _rows_digest
        return _rows_digest(self.baseline_constraints)

    def _collect_risks(self) -> List[str]:
        risks: List[str] = []
        for cp in self.checkpoints:
            if cp["status"] not in ("ok",):
                risks.append(f"{cp['step']} 状态 {cp['status']}："
                             f"{cp.get('note') or cp.get('problems') or ''}")
        if self.search_result and self.search_result.search_status == \
                SEARCH_AT_LEAST_UPPER:
            risks.append("上界仍可行，未找到最大值，需扩展搜索上界")
        if self.search_result and self.search_result.unknowns:
            risks.append(f"{len(self.search_result.unknowns)} 个试探点为 "
                         f"unknown（不收敛/超时/短路模型缺失）")
        bundle = self.bundle
        if bundle:
            col = bundle.collector
            if col:
                warn = [i for i in col.issues if not i.blocking]
                if warn:
                    risks.append(f"{len(warn)} 项非阻断质量问题（见 "
                                 f"quality_issues.csv）")
            if not bundle.fault_models:
                risks.append("缺少故障模型，短路容量能力未覆盖（unknown）")
        return risks

    # -------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        """执行全流程（S01 → S09）。"""
        self.say(f"=== hc_agent {__version__} run_id={self.run_id} ===")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        outcome: Dict[str, Any] = {"run_id": self.run_id, "exit_code":
                                   EXIT_SUCCESS}
        if self.state_store is not None:
            self.state_store.persist("init", status="running")
        # 登记任务租约：看门狗的兜底判据靠它判断"引擎是否卡死"。
        # 必须在真正开始干活**之前**登记，否则卡死期间根本看不到有任务在跑。
        task_id = as_str((getattr(self, "bundle", None) or {}) and "") or \
            self.run_id
        self.watchdog.acquire_task(task_id)
        try:
            self.s01_freeze()
            self.s02_validate()
            self.s03_build()
            cap = self.s04_s05_s06()
            outcome.update(cap)
            if cap.get("status") == "blocked":
                outcome["exit_code"] = EXIT_INPUT_BLOCKED
                self._finalize(outcome)
                return outcome

            self.s07_verify()
            rep = self.s08_report()
            outcome["release_gate"] = rep["gate"]
            outcome["report"] = rep["report"]
            pub = self.s09_seal(rep["gate"])
            outcome["publication"] = pub
            if not rep["gate"]["passed"]:
                outcome["exit_code"] = EXIT_RELEASE_GATE_FAILED
                outcome["note"] = ("结果候选已具备，但人工复核与审批未提交，"
                                   "按设计不得发布")
        except InputBlockedError as exc:
            outcome.update({"exit_code": EXIT_INPUT_BLOCKED,
                            "error": exc.message, "issues": exc.issues})
            self._finalize(outcome)
            return outcome
        except ComputeFailedError as exc:
            outcome.update({"exit_code": EXIT_COMPUTE_FAILED,
                            "error": exc.message, "error_code": exc.code})
            # 只有"引擎类"错误才值得花 ~20 秒复检引擎；业务失败去复检
            # 纯属白等，且毫无信息量（天枢一号的过滤条件，照抄）。
            self._maybe_watchdog_recheck(exc.code, exc.message, outcome)
            self._finalize(outcome)
            return outcome
        except HCAgentError as exc:
            outcome.update({"exit_code": exc.exit_code, "error": exc.message,
                            "error_code": exc.code})
            self._finalize(outcome)
            return outcome
        except Exception as exc:
            outcome.update({"exit_code": EXIT_COMPUTE_FAILED,
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc()})
            self._finalize(outcome)
            return outcome
        finally:
            # 无论成败都必须释放租约，否则看门狗会把正常结束的任务当成卡死
            self.watchdog.release_task()

        self._finalize(outcome)
        return outcome

    def _maybe_watchdog_recheck(self, code: Optional[str], message: str,
                                outcome: Dict[str, Any]) -> None:
        """引擎类失败后复检引擎健康，必要时触发受控重启。

        **时序要求**：必须在释放 OpenDSS 上下文**之前**调用，否则复检对象
        已失效 → 假阳性重启。本方法在 _finalize 之前执行，适配器此时仍存活
        （s04_s05_s06 的 finally 只关闭它自己的局部 adapter，不影响探针可用性
        ——探针会新建上下文自行判定）。
        """
        if not self.watchdog.is_reportable_error(code):
            return
        try:
            adapter = getattr(self, "_probe_adapter", None)
            decision = self.watchdog.note_engine_failure(adapter, message)
        except Exception as exc:
            # 自愈机制出错不能反过来搞垮任务（本模块的第三条纪律）
            decision = {"action": "error",
                        "reason": f"{type(exc).__name__}: {exc}"}
        self.watchdog_outcome = decision
        outcome["watchdog"] = decision
        self.say(f"看门狗复检：{decision.get('action')} —— "
                 f"{decision.get('reason', '')}")

    def _finalize(self, outcome: Dict[str, Any]) -> None:
        outcome["elapsed_s"] = round(time.perf_counter() - self.t_start, 3)
        outcome["finished_at"] = utc_now_iso()
        # 收尾遗言：区分"判为失败"与"没来得及判"。
        # 注意 exit_code=5 是"门禁未满足"而非计算失败 —— 计算本身是成功的，
        # 标成 failed 会让看板把"等人工审批"误统计为故障。
        if self.state_store is not None:
            try:
                code = outcome.get("exit_code")
                if code == EXIT_SUCCESS:
                    status = "success"
                elif code == EXIT_RELEASE_GATE_FAILED:
                    status = "awaiting_approval"
                elif code == EXIT_INTERRUPTED:
                    status = STATUS_INTERRUPTED
                else:
                    status = "failed"
                self.state_store.persist("final", status=status)
            except Exception:
                pass
        write_json(self.out_dir / "run_outcome.json", outcome)
        if self.ledger:
            self.ledger.append("run_complete",
                               result=("ok" if outcome["exit_code"] == 0
                                       else "failed"),
                               reason=f"exit_code={outcome['exit_code']} "
                                      f"{outcome.get('error','') or ''}")
        self.say(f"=== 完成 exit_code={outcome['exit_code']} "
                 f"用时 {outcome['elapsed_s']}s ===")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口（设计文档 17.10）。"""
    parser = argparse.ArgumentParser(
        prog="hc_agent.pipeline",
        description="配网承载力评估 Agent 主流程（S01-S09）")
    parser.add_argument("--task", default=None, help="task.json 路径")
    parser.add_argument("--input-dir", required=True, help="输入目录")
    parser.add_argument("--output-dir", required=True, help="运行输出目录")
    parser.add_argument("--engine-config", default=None,
                        help="引擎配置 JSON（可选）")
    parser.add_argument("--skip-verification", action="store_true",
                        help="跳过独立复算（调试用；结果不得发布）")
    parser.add_argument("--max-slices", type=int, default=None,
                        help="限制每场景时间片数（调试用）")
    parser.add_argument("--allow-unapproved-rules", action="store_true",
                        help="允许未批准规则包（仅调试；正式任务禁止）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    engine_config = None
    if args.engine_config and Path(args.engine_config).exists():
        engine_config = json.loads(Path(args.engine_config)
                                   .read_text(encoding="utf-8"))

    input_dir = Path(args.input_dir)
    task_path = Path(args.task) if args.task else (input_dir / "task.json")

    pipe = Pipeline(task_path, input_dir, args.output_dir,
                    engine_config=engine_config,
                    skip_verification=args.skip_verification,
                    max_slices=args.max_slices,
                    allow_unapproved_rules=args.allow_unapproved_rules,
                    verbose=not args.quiet)
    result = pipe.run()

    printable = {k: v for k, v in result.items()
                 if k not in ("search", "report")}
    if result.get("search") is not None:
        printable["search"] = result["search"].to_row()
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=str))
    return int(result.get("exit_code", EXIT_COMPUTE_FAILED))


if __name__ == "__main__":
    sys.exit(main())
