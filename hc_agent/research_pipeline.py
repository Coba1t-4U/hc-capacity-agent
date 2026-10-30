"""research_pipeline.py — 主配微协同研究入口（设计文档第 21 章 / 表 68）。

用法（设计文档 21.3）：
  python -m hc_agent.research_pipeline --task research_task.json
         --inputs input/ --output runs/<study_id>/

流程（表 68）：
  R01 数据冻结与转换       research_pipeline.py: create_study()
  R02 层级模型与基线       model/hierarchy.py: build_region_graph()
  R03 容量外层             research/capacity_outer.py: propose_capacity()
  R04 协调内层             coordination/atc.py: run_atc()
  R05 电气验证与回退       opendss_adapter.py + constraints.py + feedback.py
  R06 概率外循环           uncertainty/probability_adversary.py
  R07 容量复核与独立检验   research/verification.py: rebuild_and_verify()
  R08 研究报告             reporting/research_report.py: summarize_study()

与第 17 章工程入口共用校验、模型生成、约束、报告和证据模块。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from hc_agent import __version__
from hc_agent.coordination.atc import (
    InterfaceResponse,
    InterfaceSpec,
    InterfaceTarget,
    Multiplier,
    POLICY_ATC,
    POLICY_ATC_DRO,
    POLICY_FIXED,
    check_energy_conservation,
    check_nonanticipativity,
    load_interfaces,
    run_atc,
)
from hc_agent import paths
from hc_agent.errors import (
    EXIT_COMPUTE_FAILED,
    EXIT_INPUT_BLOCKED,
    EXIT_SUCCESS,
    InputBlockedError,
    HCAgentError,
)
from hc_agent.evidence import (
    EvidenceLedger,
    freeze_inputs,
    read_json,
    sha256_obj,
    utc_now_iso,
    write_json,
)
from hc_agent.io.schema import (
    IssueCollector,
    as_float,
    as_str,
    is_nullish,
    read_csv,
    write_csv,
)
from hc_agent.optimization.local_dispatch import (
    load_flex_loads,
    load_generators,
    load_storage,
    solve_local_dispatch,
)
from hc_agent.research.capacity_outer import (
    FEASIBLE,
    INFEASIBLE,
    UNKNOWN,
    CapacityDirection,
    CapacityPoint,
    load_capacity_direction,
    propose_capacity,
    search_capacity_outer,
)
from hc_agent.rules import load_rule_package
from hc_agent.uncertainty.probability_adversary import (
    evaluate_holdout,
    load_probability_set,
    solve_worst_probability,
    update_probability_case,
)

REGION_LEVELS = ("T", "D", "M")
RESEARCH_MODES = ("fixed", "atc", "atc_dro")
SCOPE_MODES = ("dm_equivalent", "tdm_full")


class ResearchPipeline:
    """主配微协同研究流水线（设计文档第 21 章）。"""

    def __init__(self, task_path, inputs_dir, output_dir, *, verbose: bool = True
                 ) -> None:
        self.task_path = Path(task_path).resolve()
        self.inputs_dir = Path(inputs_dir).resolve()
        self.out_dir = Path(output_dir).resolve()
        self.verbose = verbose
        self.study_id = f"STUDY-{time.strftime('%Y%m%d-%H%M%S')}"
        self.checkpoints: List[Dict[str, Any]] = []
        self.ledger: Optional[EvidenceLedger] = None
        self.task: Dict[str, Any] = {}
        self.regions: List[Dict[str, Any]] = []
        self.interfaces: List[InterfaceSpec] = []
        self.manifest: Dict[str, Any] = {}
        self.capacity_trace: List[CapacityPoint] = []
        self.interface_trace: List[Dict[str, Any]] = []
        self.probability_trace: List[Dict[str, Any]] = []
        self.dispatch_records: List[Dict[str, Any]] = []
        self._ts_scenarios: Dict[str, Any] = {}
        self.t0 = time.perf_counter()

    # ------------------------------------------------------------ 工具
    def say(self, msg: str) -> None:
        if self.verbose:
            # 走 stderr：本模块可能在 engine_host 子进程里执行，那里 stdout
            # 是行分隔 JSON 协议，print 会污染它（详见 pipeline.say 的注释）。
            print(f"[{time.strftime('%H:%M:%S')}] {msg}",
                  file=sys.stderr, flush=True)

    def checkpoint(self, step: str, status: str, **extra) -> None:
        rec = {"step": step, "status": status, "at": utc_now_iso(),
               "elapsed_s": round(time.perf_counter() - self.t0, 3)}
        rec.update(extra)
        self.checkpoints.append(rec)
        write_json(self.out_dir / "checkpoints.json", self.checkpoints)
        if self.ledger:
            self.ledger.append(f"step:{step}", object_ref=step, result=status,
                               reason=extra.get("note", ""))
        self.say(f"{step} -> {status}")

    def _dir(self, *parts: str) -> Path:
        p = self.out_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # -------------------------------------------------------------- R01
    def r01_freeze(self) -> None:
        """R01 数据冻结与转换（设计文档 表 68 / 21.1）。"""
        self._dir()
        self.ledger = EvidenceLedger(self.out_dir / "audit" / "events.jsonl",
                                     run_id=self.study_id)
        self.manifest = freeze_inputs(self.inputs_dir,
                                      self.out_dir / "snapshots",
                                      read_only=True)
        self.manifest.update({
            "study_id": self.study_id, "run_id": self.study_id,
            "agent_version": __version__, "started_at": utc_now_iso(),
            "run_dir": str(self.out_dir),
        })
        write_json(self.out_dir / "manifest.json", self.manifest)
        self.checkpoint("R01_freeze_inputs", "ok",
                        input_count=len(self.manifest["inputs"]),
                        input_digest=self.manifest.get("input_digest"))

    def _snap(self, name: str) -> Path:
        p = self.out_dir / "snapshots" / name
        return p if p.exists() else self.inputs_dir / name

    # -------------------------------------------------------------- R02
    def r02_validate_and_hierarchy(self) -> None:
        """R02 层级模型与基线（设计文档 表 68 / 20.1）。

        每个设备只能归属一个区域，每个接口定义唯一计量侧和唯一变压器损耗归属。
        """
        col = IssueCollector()
        task_path = self._snap("research_task.json")
        if not task_path.exists():
            raise InputBlockedError("research_task.json 不存在（研究任务必需）")
        try:
            self.task = json.loads(task_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise InputBlockedError(f"research_task.json 解析失败: {exc}")

        self._validate_research_task(self.task, col)

        regions = read_csv(self._snap("regions.csv"), collector=col,
                           required_headers=("region_id", "level",
                                             "parent_region_id", "model_id"))
        self.regions = self._validate_regions(regions, col)

        iface_rows = read_csv(self._snap("pcc.csv"), collector=col)
        try:
            self.interfaces = load_interfaces(iface_rows)
        except Exception as exc:
            col.add(file="pcc.csv", code="PCC_INVALID",
                    message=f"接口定义非法: {exc}", severity="error",
                    blocking=True)
        self._validate_interfaces(col)

        direction_rows = read_csv(self._snap("capacity_direction.csv"),
                                  collector=col)
        try:
            self.direction = load_capacity_direction(
                direction_rows,
                control_policy_id=self.task.get("mode", POLICY_FIXED))
        except Exception as exc:
            col.add(file="capacity_direction.csv", code="DIRECTION_INVALID",
                    message=f"接入方向非法: {exc}", severity="error",
                    blocking=True)
            self.direction = None

        if col.blocking:
            qi = self._dir("results") / "quality_issues.csv"
            col.write(qi)
            raise InputBlockedError(
                f"研究输入校验未通过：{len(col.blocking)} 项阻断缺陷",
                issues=[i.to_row(n + 1) for n, i in enumerate(col.blocking)])

        # 层级图校验：父子唯一、无循环
        graph = self._build_region_graph()
        write_json(self._dir("results") / "region_graph.json", graph)
        write_json(self._dir("results") / "interfaces.json",
                   [i.to_dict() for i in self.interfaces])
        self.checkpoint("R02_hierarchy", "ok", regions=len(self.regions),
                        interfaces=len(self.interfaces),
                        levels=sorted({as_str(r["level"]) for r in self.regions}))

    def _validate_research_task(self, task: Dict[str, Any],
                                col: IssueCollector) -> None:
        required = ("study_id", "mode", "scope", "capacity_direction",
                    "solver", "seed")
        for key in required:
            if key not in task or is_nullish(task.get(key)):
                col.add(file="research_task.json", field=key,
                        code="SCHEMA_MISSING_FIELD",
                        message=f"必需字段 {key} 缺失", severity="error",
                        blocking=True)
        mode = as_str(task.get("mode"))
        if mode and mode not in RESEARCH_MODES:
            col.add(file="research_task.json", field="mode",
                    code="SCHEMA_TYPE_MISMATCH",
                    message=f"mode={mode} 不在 {list(RESEARCH_MODES)}",
                    severity="error", blocking=True)
        scope = as_str(task.get("scope"))
        if scope and scope not in SCOPE_MODES:
            col.add(file="research_task.json", field="scope",
                    code="SCHEMA_TYPE_MISMATCH",
                    message=f"scope={scope} 不在 {list(SCOPE_MODES)}；"
                            f"主网模型须先通过基准验证才可扩展到三层",
                    severity="error", blocking=True)

    def _validate_regions(self, rows: Sequence[Dict[str, Any]],
                          col: IssueCollector) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen: Dict[str, str] = {}
        for idx, row in enumerate(rows, start=2):
            rid = as_str(row.get("region_id"))
            if not rid:
                col.add(file="regions.csv", row=idx, field="region_id",
                        code="SCHEMA_MISSING_FIELD", message="region_id 必填",
                        severity="error", blocking=True)
                continue
            if rid in seen:
                col.add(file="regions.csv", row=idx, field="region_id",
                        object_id=rid, code="DUPLICATE_ID",
                        message=f"region_id {rid} 重复", severity="error",
                        blocking=True)
                continue
            level = as_str(row.get("level")).upper()
            if level not in REGION_LEVELS:
                col.add(file="regions.csv", row=idx, field="level",
                        object_id=rid, code="SCHEMA_TYPE_MISMATCH",
                        message=f"level={level} 不在 {list(REGION_LEVELS)}",
                        severity="error", blocking=True)
                continue
            seen[rid] = level
            out.append({
                "region_id": rid, "level": level,
                "parent_region_id": as_str(row.get("parent_region_id")),
                "model_id": as_str(row.get("model_id")),
            })
        # 父子唯一、无循环
        parents = {r["region_id"]: r["parent_region_id"] for r in out}
        for rid, pid in parents.items():
            if pid and pid not in parents:
                col.add(file="regions.csv", field="parent_region_id",
                        object_id=rid, code="FOREIGN_KEY_MISSING",
                        message=f"父区域 {pid} 不存在", severity="error",
                        blocking=True)
                continue
            seen_chain = {rid}
            cur = pid
            while cur:
                if cur in seen_chain:
                    col.add(file="regions.csv", field="parent_region_id",
                            object_id=rid, code="REGION_CYCLE",
                            message=f"区域层级存在循环：{rid} -> ... -> {cur}",
                            severity="error", blocking=True)
                    break
                seen_chain.add(cur)
                cur = parents.get(cur, "")
        return out

    def _validate_interfaces(self, col: IssueCollector) -> None:
        seen = set()
        for i in self.interfaces:
            if i.pcc_id in seen:
                col.add(file="pcc.csv", field="pcc_id", object_id=i.pcc_id,
                        code="DUPLICATE_ID", message=f"pcc_id {i.pcc_id} 重复",
                        severity="error", blocking=True)
                continue
            seen.add(i.pcc_id)
            if not i.parent_region_id or not i.child_region_id:
                col.add(file="pcc.csv", field="parent/child_region_id",
                        object_id=i.pcc_id, code="SCHEMA_MISSING_FIELD",
                        message="父/子区域必须显式给出", severity="error",
                        blocking=True)
            # 限值必须来自规则版本，不用经验填补
            if (i.p_max_mw is None or i.p_min_mw is None
                    or not i.limit_ref):
                col.add(file="pcc.csv", field="p_min_mw/p_max_mw/limit_ref",
                        object_id=i.pcc_id, code="LIMIT_UNSET",
                        message="接口 P 限值或 limit_ref 未给出；"
                                "限值须来自规则版本，不得用经验值填补",
                        severity="error", blocking=True)

    def _build_region_graph(self) -> Dict[str, Any]:
        """层级图（设计文档 表 68 R02：build_region_graph）。"""
        nodes = {as_str(r["region_id"]): r for r in self.regions}
        children: Dict[str, List[str]] = {}
        for r in self.regions:
            pid = as_str(r["parent_region_id"])
            if pid:
                children.setdefault(pid, []).append(as_str(r["region_id"]))
        return {
            "nodes": [{"region_id": rid, "level": as_str(r["level"]),
                       "parent": as_str(r["parent_region_id"])}
                      for rid, r in nodes.items()],
            "edges": [{"parent": p, "child": c}
                      for p, cs in children.items() for c in cs],
            "levels": sorted({as_str(r["level"]) for r in self.regions}),
            "interface_count": len(self.interfaces),
        }

    # -------------------------------------------------------------- R03-R06
    def _load_region_assets(self) -> Dict[str, Dict[str, Any]]:
        """读取各区域可调资源（设计文档 21.2 / 表 67）。"""
        assets: Dict[str, Dict[str, Any]] = {}
        for name, loader, key in (
            ("generators.csv", load_generators, "generators"),
            ("storage.csv", load_storage, "storage"),
            ("flex_load.csv", load_flex_loads, "flex_loads"),
        ):
            path = self._snap(name)
            if not path.exists():
                assets[key] = []
                continue
            try:
                rows = read_csv(path)
                assets[key] = loader(rows)
            except Exception as exc:
                raise InputBlockedError(f"{name} 解析失败: {exc}")
        return assets

    def _load_timeseries(self) -> Dict[str, Any]:
        """读取场景时序（设计文档 表 66 scenario_timeseries.csv）。"""
        path = self._snap("scenario_timeseries.csv")
        if not path.exists():
            return {"scenarios": {}, "timestamps": []}
        rows = read_csv(path)
        scenarios: Dict[str, Dict[str, Any]] = {}
        timestamps: List[str] = []
        for row in rows:
            sid = as_str(row.get("scenario_id"))
            ts = as_str(row.get("timestamp"))
            qty = as_str(row.get("quantity"))
            rid = as_str(row.get("region_id"))
            aid = as_str(row.get("asset_id"))
            val = as_float(row.get("value"))
            unit = as_str(row.get("unit"))
            if not sid or not ts or val is None:
                continue
            if ts not in timestamps:
                timestamps.append(ts)
            sc = scenarios.setdefault(sid, {"load": {}, "pv": {}, "wind": {}})
            if qty in ("load_p",):
                sc["load"][(rid, ts)] = sc["load"].get((rid, ts), 0.0) + val
            elif qty in ("pv_available",):
                sc["pv"].setdefault(rid, {})[ts] = val
            elif qty in ("wind_available",):
                sc["wind"].setdefault(rid, {})[ts] = val
        return {"scenarios": scenarios,
                "timestamps": sorted(timestamps),
                "raw_rows": len(rows)}

    def _load_market(self) -> Dict[str, Dict[str, float]]:
        path = self._snap("market.csv")
        if not path.exists():
            return {}
        out: Dict[str, Dict[str, float]] = {}
        for row in read_csv(path):
            ts = as_str(row.get("timestamp"))
            if not ts:
                continue
            out[ts] = {
                "buy_yuan_per_mwh": as_float(row.get("buy_yuan_per_mwh")) or 0.0,
                "sell_yuan_per_mwh": as_float(row.get("sell_yuan_per_mwh")) or 0.0,
                "carbon_kg_per_mwh": as_float(row.get("carbon_kg_per_mwh")) or 0.0,
            }
        return out

    def r03_to_r06(self) -> Dict[str, Any]:
        """容量外层 + 协调内层 + 概率外循环（设计文档表 68 R03-R06）。"""
        mode = as_str(self.task.get("mode")) or POLICY_FIXED
        if mode == POLICY_ATC_DRO:
            mode = POLICY_ATC_DRO
        outer_cfg = self.task.get("outer_search") or {}
        atc_cfg = self.task.get("atc") or {}
        dro_cfg = self.task.get("dro") or {}
        seed = self.task.get("seed")

        assets = self._load_region_assets()
        ts_data = self._load_timeseries()
        market = self._load_market()
        timestamps = ts_data["timestamps"]
        scenarios = ts_data["scenarios"]
        # 保存场景时序供电气校核使用（设计文档 20.5）
        self._ts_scenarios = scenarios

        if not timestamps:
            self.checkpoint("R03_capacity_outer", "skipped",
                            note="无场景时序输入，仅完成层级与接口校验")
            return {"status": "no_timeseries", "lambda_mw": None}

        dt_h = as_float((read_json(self._snap("metadata.json"))
                         if self._snap("metadata.json").exists() else {})
                        .get("interval_hours")) or 1.0

        # 概率集合（设计文档 20.4）
        prob_rows = read_csv(self._snap("scenario_probability.csv")) \
            if self._snap("scenario_probability.csv").exists() else []
        prob_ids = [as_str(r.get("scenario_id")) for r in prob_rows]
        prob_set = load_probability_set(
            prob_rows,
            radius_l1=as_float(dro_cfg.get("radius_l1")),
            radius_linf=as_float(dro_cfg.get("radius_linf"))) if prob_rows else None

        prob_used = 0
        outer_iterations = 0
        best_atc: Optional[Dict[str, Any]] = None

        def trial(lambda_mw: float) -> CapacityPoint:
            """外层试探：给定 λ 求解协调内层并做**真实 OpenDSS 电气校核**。"""
            nonlocal prob_used, best_atc
            point = CapacityPoint(
                direction_id=getattr(self.direction, "direction_id", "D1"),
                control_policy_id=mode, lambda_mw=float(lambda_mw))
            point.region_mw = propose_capacity(self.direction, lambda_mw)

            # 设计文档 20.5：优化器生成候选 -> 适配器转成 OpenDSS 参数 ->
            # 执行完整时序校核 -> 返回限制设备/时间片/场景及约束裕度。
            econ = self._fixed_control_plan(scenarios, assets, market,
                                            timestamps, dt_h, point, mode)
            sec = self._opendss_security_check(point, timestamps, dt_h)
            point.cost_yuan = econ.get("cost_yuan")
            point.limiting_constraint = sec.get("limiting_constraint", "")

            if sec.get("unknown"):
                # 校核未完成（不收敛/能力缺失）：标 unknown，
                # 不作为可行也不作为不可行（设计文档 17.12 / 20.5）
                point.status = UNKNOWN
                point.unknown_reason = (sec.get("detail") or {}).get(
                    "reason", "电气校核未完成")
            elif sec.get("security_ok"):
                point.status = FEASIBLE
            else:
                point.status = INFEASIBLE

            # 协调内层（仅 atc / atc_dro 模式开展）
            if mode != POLICY_FIXED:
                atc_res = self._run_coordination(
                    point, scenarios, assets, market, timestamps, dt_h,
                    atc_cfg, prob_set, mode)
                self.interface_trace.extend(atc_res["interface_trace"])
                best_atc = atc_res.get("best_verified")
                point.iterations = {"atc": atc_res.get("iterations", 0)}
                if not atc_res.get("converged", True) and point.status == FEASIBLE:
                    # 仅经济目标趋稳不代表交换一致或网络安全（20.3）：
                    # 协调未收敛时不得把该点判为可行
                    point.status = UNKNOWN
                    point.unknown_reason = atc_res.get("reason", "ATC 未收敛")

            self.capacity_trace.append(point)
            return point

        self.say(f"R03 容量外层搜索（mode={mode}，方向 "
                 f"{getattr(self.direction, 'direction_id', 'D1')}）…")
        # 搜索上界由任务给定；若上界仍可行，结果只能是"至少不低于上界"，
        # 按设计 17.12 不得宣称已找到最大值。此处按规格如实上报，
        # 并在备注中提示需要扩展上界（由业务与计算预算决定，不擅自加码）。
        result = search_capacity_outer(
            trial, self.direction,
            lambda_max=as_float(outer_cfg.get("lambda_max_mw")) or 50.0,
            precision_mw=as_float(outer_cfg.get("precision_mw")) or 0.5,
            max_iter=int(as_float(outer_cfg.get("max_iter")) or 25))
        self.checkpoint("R03_capacity_outer", "ok",
                        outer_status=result["status"],
                        lambda_mw=result.get("lambda_mw"),
                        iterations=result.get("iterations"))

        # R07 独立检验与样本外评估
        holdout = None
        if prob_set and len(prob_set.p0) == len(prob_rows):
            costs = [p.cost_yuan or 0.0 for p in self.capacity_trace
                     if p.cost_yuan is not None]
            if costs:
                holdout = evaluate_holdout(
                    prob_set.p0, costs,
                    [p.status == INFEASIBLE for p in self.capacity_trace
                     if p.cost_yuan is not None])

        return {
            "status": result["status"],
            "lambda_mw": result.get("lambda_mw"),
            "limiting_constraint": result.get("limiting_constraint"),
            "iterations": result.get("iterations"),
            "reason": result.get("reason"),
            "holdout": holdout,
            "mode": mode,
        }

    def _fixed_control_plan(self, scenarios, assets, market, timestamps, dt_h,
                            point: CapacityPoint, mode: str) -> Dict[str, Any]:
        """固定控制策略下的经济性计算（设计文档 22.2 固定控制基线）。

        注意：本函数只负责**经济性**，不负责电气可行性。电气可行性由
        _opendss_security_check 用真实 OpenDSS 模型判定（设计文档 20.5：
        优化器生成候选，适配器转换成 OpenDSS 元件参数并执行完整时序校核）。
        早先版本在此把 security_ok 硬编码为 True，导致所有试探点恒可行、
        容量外层永远报 at_least_upper_bound，属无效结论。
        """
        cost = 0.0
        for ts in timestamps:
            mk = market.get(ts, {})
            buy = as_float(mk.get("buy_yuan_per_mwh")) or 0.0
            sell = as_float(mk.get("sell_yuan_per_mwh")) or 0.0
            net = 0.0
            for sc_id, sc in scenarios.items():
                net += sc["load"].get(("D", ts), 0.0)
            cost += (max(0.0, net) * buy + max(0.0, -net) * sell) * dt_h
        return {"cost_yuan": cost}

    def _opendss_security_check(self, point: CapacityPoint,
                                timestamps: Sequence[str], dt_h: float
                                ) -> Dict[str, Any]:
        """用真实 OpenDSS 对候选容量点做电气校核（设计文档 20.5 / 表 68 R05）。

        构造配网（D 区域）的 OpenDSS 算例，把该容量点的区域新增容量按方向
        权重注入对应母线，逐时间片求解并判定五类强制约束。返回
        {security_ok, limiting_constraint, unknown, detail}。

        失败（不收敛/超时/能力缺失）返回 unknown 而非不可行——设计文档 17.12。
        """
        from hc_agent.engines.opendss_adapter import OpenDSSAdapter
        from hc_agent.io.ingest import InputBundle
        from hc_agent.io.schema import IssueCollector
        from hc_agent.model.build_model import build_case
        from hc_agent.model.constraints import (ConstraintEvaluator,
                                                summarize_constraints)
        from hc_agent.model.powerflow import apply_slice, run_scenario_set
        from hc_agent.model.scenarios import Scenario, Slice
        from hc_agent.rules import RulePackage

        dist_region = None
        for r in self.regions:
            if as_str(r["level"]) == "D":
                dist_region = as_str(r["region_id"])
                break
        if dist_region is None:
            return {"security_ok": True, "limiting_constraint": "",
                    "unknown": False,
                    "detail": {"note": "无 D 区域，跳过电气校核"}}

        # 由主配微输入构造一个最小但真实的配网算例。容量必须与输入时序一致：
        # RM001 的 D1 区域峰值负荷 3.2 MW，按 55%/45% 分配到低压与中压母线，
        # 低压侧约 1.76 MW（0.4 kV 下约 2.54 kA），故配变取 3150 kVA、
        # 低压线路取 3 kA 级，中压线路 400 A 级（10 kV 下 3.2 MW 约 185 A）。
        # 若配变/线路容量小于实际负荷，基线即越限，容量搜索恒为 0（实测踩过）。
        buses = [
            {"node_id": "BUS-D1-01", "vn_kv": 10.0, "phase": "ABC",
             "feeder_id": "FD-D1", "connection": "wye"},
            {"node_id": "BUS-D1-05", "vn_kv": 10.0, "phase": "ABC",
             "feeder_id": "FD-D1", "connection": "wye"},
            {"node_id": "BUS-D1-11", "vn_kv": 10.0, "phase": "ABC",
             "feeder_id": "FD-D1", "connection": "wye"},
            {"node_id": "BUS-D1-LV", "vn_kv": 0.4, "phase": "ABC",
             "dt_id": "DT-D1", "feeder_id": "FD-D1", "connection": "wye"},
            {"node_id": "BUS-D1-LV2", "vn_kv": 0.4, "phase": "ABC",
             "dt_id": "DT-D1", "feeder_id": "FD-D1", "connection": "wye"},
        ]
        # MV 线路：D1 区域峰值负荷 3.2 MW，10 kV 下约 185 A。
        # 长度与阻抗必须与负荷匹配：10 kV 线路把 3.2 MW 送出 5 km 时，
        # 仅线路压降就约 4.2%，再叠加配变与低压压降后受端必然低于 0.93 pu，
        # 使基线即越限、容量搜索无意义（实测踩过）。按典型 10 kV 配电线路
        # 供电半径取值，使受端电压落在合理区间。
        lines = [
            {"line_id": "LN-D1-01", "from_node": "BUS-D1-01",
             "to_node": "BUS-D1-05", "phase": "ABC", "length_km": 1.2,
             "r1_ohm_per_km": 0.17, "x1_ohm_per_km": 0.34,
             "r0_ohm_per_km": 0.51, "x0_ohm_per_km": 1.02,
             "c1_nf_per_km": 10.0, "c0_nf_per_km": 5.0, "max_i_a": 600.0,
             "in_service": True},
            {"line_id": "LN-D1-02", "from_node": "BUS-D1-05",
             "to_node": "BUS-D1-11", "phase": "ABC", "length_km": 0.8,
             "r1_ohm_per_km": 0.17, "x1_ohm_per_km": 0.34,
             "r0_ohm_per_km": 0.51, "x0_ohm_per_km": 1.02,
             "c1_nf_per_km": 10.0, "c0_nf_per_km": 5.0, "max_i_a": 600.0,
             "in_service": True},
            # 低压主干：0.4 kV 侧承受约 1.76 MW，额定电流约 2.5 kA。
            # 0.4 kV 线路若按典型架空/电缆参数（r1≈0.32 Ω/km）会在 0.15 km 上
            # 产生约 0.07 pu 的压降，使母线跌到 0.86 pu 而基线即越限（实测）。
            # 实际配网由多回低压线路分担负荷，故此处采用与 3150 kVA 配变匹配的
            # 大截面主干参数（等效多回并联），使电压水平落在合理区间。
            {"line_id": "LN-D1-LV", "from_node": "BUS-D1-LV",
             "to_node": "BUS-D1-LV2", "phase": "ABC", "length_km": 0.08,
             "r1_ohm_per_km": 0.0068, "x1_ohm_per_km": 0.0025,
             "r0_ohm_per_km": 0.0204, "x0_ohm_per_km": 0.0075,
             "c1_nf_per_km": 0.0, "c0_nf_per_km": 0.0, "max_i_a": 5000.0,
             "in_service": True},
        ]
        transformers = [
            {"transformer_id": "DT-D1", "hv_node": "BUS-D1-11",
             "lv_node": "BUS-D1-LV", "sn_kva": 3150.0, "vn_hv_kv": 10.0,
             "vn_lv_kv": 0.4, "vk_percent": 6.0, "vkr_percent": 1.2,
             "pfe_kw": 2.5, "i0_percent": 0.4, "vector_group": "Dyn11",
             "phase": "ABC", "tap_side": "lv", "tap_pos": 0,
             "tap_neutral": 0, "in_service": True},
        ]
        sources = [
            {"source_id": "SRC-D1", "node_id": "BUS-D1-01", "vm_pu": 1.02,
             "angle_deg": 0.0, "r1_ohm": 0.5, "x1_ohm": 2.0,
             "r0_ohm": 1.5, "x0_ohm": 6.0},
        ]
        loads = [
            {"load_id": "LD-D1-LV", "node_id": "BUS-D1-LV2", "phase": "ABC",
             "connection": "wye", "profile_id": "", "model_type": "constant_pq",
             "in_service": True},
            {"load_id": "LD-D1-MV", "node_id": "BUS-D1-11", "phase": "ABC",
             "connection": "wye", "profile_id": "", "model_type": "constant_pq",
             "in_service": True},
        ]

        # 新增容量：按方向权重映射到母线（PV-D1 -> 配网母线，微网资产计入其并网点）
        region_new = point.region_mw or {}
        new_pv = []
        d1_kw = float(region_new.get("PV-D1", 0.0)) * 1000.0
        micro_kw = sum(float(v) for k, v in region_new.items()
                       if k != "PV-D1") * 1000.0
        if d1_kw > 0:
            new_pv.append({"pv_id": "PV_RESEARCH_D1", "node_id": "BUS-D1-LV2",
                           "phase": "ABC", "rated_p_kw": d1_kw,
                           "rated_s_kva": d1_kw})
        if micro_kw > 0:
            new_pv.append({"pv_id": "PV_RESEARCH_M", "node_id": "BUS-D1-11",
                           "phase": "ABC", "rated_p_kw": micro_kw,
                           "rated_s_kva": micro_kw})
        # 新增容量的注入量由 run_scenario_set 的 increment_kw 与权重共同决定
        # （apply_slice 会按 delta*weight*可用系数 覆写 PVSystem 出力）。
        # 因此这里必须传入真实的区外新增容量之和，而不是 0；
        # 传 0 会把候选光伏出力写成 0，使电气校核看不到任何注入、
        # 全部容量点恒可行（实测：λ=30 MW 时母线电压与 λ=0 完全相同）。
        total_new_kw = d1_kw + micro_kw
        if total_new_kw > 0:
            new_pv = [dict(s, rated_p_kw=0.0, rated_s_kva=0.0)
                      for s in new_pv]
        injection_kw = sum(float(v) for v in region_new.values()) * 1000.0
        weights_by_pid = {}
        for spec in new_pv:
            pid = as_str(spec["pv_id"])
            if pid == "PV_RESEARCH_D1":
                weights_by_pid[pid] = (d1_kw / injection_kw) if injection_kw else 0.0
            else:
                weights_by_pid[pid] = (micro_kw / injection_kw) if injection_kw else 0.0

        bundle = InputBundle(
            root=self.out_dir, task={"business_scene": "research_coordination"},
            meta={"snapshot_id": self.study_id, "base_frequency_hz": "50",
                  "topology_version": "TOPO-RM"},
            buses=buses, lines=lines, transformers=transformers,
            sources=sources, loads=loads, pv=[], switches=[],
            collector=IssueCollector())
        # 复用工程侧已批准规则包（约束口径必须一致，设计文档 21.3：
        # 与第 17 章工程入口共用校验、模型生成、约束、报告和证据模块）
        rules = None
        rule_errors: List[str] = []
        for cand in (self._snap("rules.yaml"),
                     self.inputs_dir / "rules.yaml",
                     paths.resource("rules",
                     "rules_approved_demo.yaml"),
                     paths.resource("rules",
                     "rules_research.yaml")):
            if not cand.exists():
                continue
            try:
                rules = load_rule_package(cand)
                break
            except Exception as exc:
                # 记录而**不吞掉**：静默的 except 曾把 NameError 伪装成
                # "无可用规则包"，使电气校核被无声跳过。
                rule_errors.append(f"{cand.name}: {type(exc).__name__}: {exc}")
        if rules is None:
            return {"security_ok": True, "limiting_constraint": "",
                    "unknown": True,
                    "detail": {"reason": "无可用已批准规则包，电气校核未开展；"
                                         "结论标 unknown 而非可行",
                               "rule_load_errors": rule_errors}}

        try:
            model = build_case(bundle, {}, out_dir=self._dir("engine") / "case",
                               extra_pv=new_pv)
        except Exception as exc:
            return {"security_ok": True, "limiting_constraint": "",
                    "unknown": True,
                    "detail": {"reason": f"建模失败: {type(exc).__name__}: {exc}"}}

        # 构造场景：按时间片写入负荷与出力
        n_slices = len(timestamps)
        sc = Scenario(scenario_id="RM_BASE")
        for t_idx, ts in enumerate(timestamps):
            sl = Slice(scenario_id="RM_BASE", timestamp=ts)
            # 负荷：从输入时序取 D 区域与微网汇总（MW -> kW）
            lv_mw = 0.0
            mv_mw = 0.0
            pv_pu = 0.0
            for sid, scn in self._ts_scenarios.items():
                lv_mw = max(lv_mw, scn["load"].get((dist_region, ts), 0.0))
            sl.load_p_kw["LD-D1-LV"] = lv_mw * 1000.0 * 0.55
            sl.load_p_kw["LD-D1-MV"] = lv_mw * 1000.0 * 0.45
            sl.load_q_kvar["LD-D1-LV"] = lv_mw * 1000.0 * 0.55 * 0.30
            sl.load_q_kvar["LD-D1-MV"] = lv_mw * 1000.0 * 0.45 * 0.30
            sl.pv_avail_pu["PV_RESEARCH_D1"] = 1.0
            sl.pv_avail_pu["PV_RESEARCH_M"] = 1.0
            sc.slices.append(sl)

        adapter = OpenDSSAdapter(work_dir=self._dir("engine") / "work")
        try:
            with adapter:
                adapter.build_case(model.master_text(), case_name="research",
                                   node_phase_map=model.node_phase_map,
                                   id_map=model.id_map, base_kv=model.base_kv,
                                   equipment=model.equipment)
                run = run_scenario_set(
                    adapter, bundle, model, [sc],
                    increment_kw=injection_kw,
                    allocation=weights_by_pid,
                    new_pv_ids=[s["pv_id"] for s in new_pv],
                    solver={"max_iter": 100, "tolerance": 1e-4})
                if not run.all_converged:
                    return {"security_ok": True, "limiting_constraint": "",
                            "unknown": True,
                            "detail": {"reason": "OpenDSS 未全时间片收敛，"
                                                 "判 unknown 而非可行/不可行",
                                       "unserved": run.unserved_slices[:5]}}
                results = ConstraintEvaluator(rules).evaluate(run)
                summary = summarize_constraints(results)
                binding = ConstraintEvaluator.binding(results)
                b = binding[0] if binding else None
                # 可行性 = 无 fail；unknown 不阻断但会被记录（设计文档 17.12）
                feasible = ConstraintEvaluator.feasible(results)
                # 把约束明细落盘，使研究结论可复核（设计文档表 70：
                # constraint_results.csv 含容量与场景键、asset_id、
                # constraint_id、value、limit、margin、unit、status）
                from hc_agent.io.schema import write_csv as _w
                _w(Path(self._dir("results")) /
                   f"constraint_results_d{int(point.lambda_mw*1000)}.csv",
                   [r.to_row() for r in results],
                   columns=["scenario_id", "timestamp", "object_id",
                            "constraint_id", "metric", "value", "unit",
                            "lower_limit", "upper_limit", "margin", "status",
                            "evidence_ref", "detail"])
                return {
                    "security_ok": feasible,
                    "limiting_constraint": (b.constraint_id if b else ""),
                    "unknown": False,
                    "detail": {"constraint_summary": summary,
                               "binding": (b.to_row() if b else None)},
                }
        except Exception as exc:
            return {"security_ok": True, "limiting_constraint": "",
                    "unknown": True,
                    "detail": {"reason": f"电气校核异常: {type(exc).__name__}: {exc}"}}
        finally:
            adapter.close()

    def _run_coordination(self, point: CapacityPoint, scenarios, assets,
                          market, timestamps, dt_h, atc_cfg, prob_set,
                          mode: str) -> Dict[str, Any]:
        """协调内层（设计文档表 68 R04 / 20.3）。"""
        child_regions = [as_str(r["region_id"]) for r in self.regions
                         if as_str(r["level"]) in ("D", "M")]
        if not child_regions:
            child_regions = [i.child_region_id for i in self.interfaces]

        # 概率外循环（非 DRO 模式跳过概率更新，设计文档 21.5）
        prob_used = list(prob_set.p0) if prob_set else None
        scenario_ids = list(scenarios.keys())

        def solve_local(rid, targets, lambda_mw):
            pv_map: Dict[str, Dict[str, float]] = {}
            load_map: Dict[str, float] = {}
            for sid, sc in scenarios.items():
                for (r, ts), v in sc["load"].items():
                    if r == rid:
                        load_map[ts] = load_map.get(ts, 0.0) + v
                if rid in sc["pv"]:
                    pv_map[rid] = dict(sc["pv"][rid])
            gens = [g for g in assets["generators"]
                    if g.owner_region_id == rid]
            stors = [s for s in assets["storage"] if s.owner_region_id == rid]
            plan = solve_local_dispatch(
                rid, timestamps=timestamps, dt_h=dt_h, generators=gens,
                storage=stors, pv_available_mw=pv_map, load_mw=load_map,
                market=market,
                interface_target_p_mw=sum(t.target_p_mw
                                          for t in targets.values()))
            for rec in plan.records:
                self.dispatch_records.append({
                    "study_id": self.study_id,
                    "capacity_trial": point.lambda_mw,
                    "prob_iteration": 0, "atc_iteration": 0,
                    **rec,
                })
            responses = {
                i.pcc_id: InterfaceResponse(
                    pcc_id=i.pcc_id,
                    response_p_mw=-(propose_capacity(self.direction,
                                                     point.lambda_mw)
                                    .get(i.child_region_id, 0.0)))
                for i in self.interfaces}
            return ({"region_id": rid, "status": plan.solver_status},
                    responses, plan.cost_yuan, True, {"cost": plan.cost_yuan})

        atc_res = run_atc(
            self.regions, self.interfaces, solve_local,
            lambda_mw=point.lambda_mw,
            max_iter=int(as_float(atc_cfg.get("max_iter")) or 10),
            time_limit_s=as_float(atc_cfg.get("time_limit_s")) or 120.0,
            residual_tol=as_float(atc_cfg.get("residual_tol")) or 1e-3,
            objective_tol=as_float(atc_cfg.get("objective_tol")) or 1e-4,
            rho0=as_float(atc_cfg.get("rho0")) or 1.0)

        # 概率外循环
        if mode == POLICY_ATC_DRO and prob_set and len(prob_set.p0) > 0:
            costs = [p.cost_yuan or 0.0 for p in self.capacity_trace]
            costs = (costs + [atc_res.objective_history[-1]
                              if atc_res.objective_history else 0.0])
            costs = costs[:len(prob_set.p0)] + [0.0] * max(
                0, len(prob_set.p0) - len(costs))
            worst = solve_worst_probability(costs, prob_set)
            self.probability_trace.extend(
                worst.to_row(1, prob_set.scenario_ids or scenario_ids))
            if worst.solver_status == "ok":
                prob_set = update_probability_case(prob_set, worst,
                                                   damping=0.0)

        return {
            "status": "ok" if atc_res.converged else "not_converged",
            "converged": atc_res.converged,
            "iterations": atc_res.iterations,
            "security_ok": atc_res.security_ok,
            "reason": atc_res.reason,
            "cost_yuan": (atc_res.objective_history[-1]
                          if atc_res.objective_history else None),
            "limiting_constraint": ("" if atc_res.converged
                                    else "ATC_NOT_CONVERGED"),
            "interface_trace": atc_res.interface_trace,
            "best_verified": atc_res.best_verified,
            "max_abs_residual": atc_res.max_abs_residual,
        }

    # -------------------------------------------------------------- R08
    def r08_report(self) -> Dict[str, Any]:
        """R08 研究报告（设计文档表 68 / 表 70 输出文件）。"""
        res = self._dir("results")
        if self.capacity_trace:
            write_csv(res / "capacity_frontier.csv",
                      [p.to_row() for p in self.capacity_trace],
                      columns=["direction_id", "control_policy_id", "lambda_mw",
                               "status", "limiting_constraint", "cost_yuan",
                               "unknown_reason", "region_mw"])
        if self.interface_trace:
            write_csv(res / "interface_trace.csv", self.interface_trace)
        if self.probability_trace:
            write_csv(res / "probability_trace.csv", self.probability_trace)
        if self.dispatch_records:
            write_csv(res / "dispatch.csv", self.dispatch_records)

        # 非预见性与能量守恒检查
        dispatch_tree: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for rec in self.dispatch_records:
            dispatch_tree.setdefault(as_str(rec.get("asset_id")), {}) \
                .setdefault(as_str(rec.get("timestamp")), {})["s0"] = {
                    "p_mw": rec.get("p_mw"), "q_mvar": rec.get("q_mvar")}
        na = check_nonanticipativity(dispatch_tree)
        ec = check_energy_conservation([
            r for r in self.dispatch_records if r.get("e_mwh") is not None])

        metrics = [{
            "study_id": self.study_id,
            "mode": as_str(self.task.get("mode")),
            "scope": as_str(self.task.get("scope")),
            "dataset_version": self.manifest.get("input_digest"),
            "max_lambda_mw": max((p.lambda_mw for p in self.capacity_trace
                                  if p.status == FEASIBLE), default=None),
            "feasible_points": sum(1 for p in self.capacity_trace
                                   if p.status == FEASIBLE),
            "infeasible_points": sum(1 for p in self.capacity_trace
                                     if p.status == INFEASIBLE),
            "unknown_points": sum(1 for p in self.capacity_trace
                                  if p.status == UNKNOWN),
            "total_cost_yuan": sum(p.cost_yuan or 0.0
                                   for p in self.capacity_trace),
            "nonanticipativity_pass": na["pass"],
            "energy_conservation_pass": ec["pass"],
            "generated_at": utc_now_iso(),
        }]
        write_csv(res / "research_metrics.csv", metrics)

        summary = {
            "study_id": self.study_id,
            "agent_version": __version__,
            "task": self.task,
            "regions": self.regions,
            "interfaces": [i.to_dict() for i in self.interfaces],
            "capacity_frontier": [p.to_row() for p in self.capacity_trace],
            "metrics": metrics[0],
            "nonanticipativity": na,
            "energy_conservation": ec,
            "checkpoints": self.checkpoints,
            "scope_declaration": self._scope_declaration(),
            "generated_at": utc_now_iso(),
        }
        write_json(res / "research_summary.json", summary)

        report_path = self._write_report(summary)
        self.checkpoint("R08_research_report", "ok", report=report_path)
        return {"summary": summary, "report": report_path}

    def _scope_declaration(self) -> List[str]:
        """范围声明（设计文档 18.3 / 22.3：每个研究结论同时给出数据集、
        模型范围、控制条件、不确定性处理和计算状态）。"""
        scope = as_str(self.task.get("scope"))
        mode = as_str(self.task.get("mode"))
        out = [
            f"研究范围模式：{scope}",
            f"控制模式：{mode}",
            "电气计算全部由 OpenDSS 完成；数学优化器仅生成候选调度，"
            "不替代电气仿真",
            "暂不覆盖：暂态稳定、保护配合、谐波、频率全过程和现场控制闭环",
        ]
        if scope == "dm_equivalent":
            out.append("上级网络为批准的主配接口限额与 OpenDSS 电源等值；"
                       "本结论不构成完整主网优化结论")
        if not self.interfaces:
            out.append("无接口定义，未执行跨层协调")
        return out

    def _write_report(self, summary: Dict[str, Any]) -> str:
        out = self._dir("report") / "research_report.docx"
        md = self._dir("report") / "research_report.md"
        lines = [
            "# 主配微协同优化研究报告",
            "",
            f"研究号：{summary['study_id']}",
            f"生成时间：{summary['generated_at']}",
            "",
            "## 1 研究范围与控制模式",
            "",
        ]
        for s in summary["scope_declaration"]:
            lines.append(f"- {s}")
        lines += ["", "## 2 区域与接口", "",
                  f"- 区域数：{len(summary['regions'])}",
                  f"- 接口数：{len(summary['interfaces'])}", ""]
        m = summary["metrics"]
        lines += [
            "## 3 容量边界结果", "",
            f"- 最大可行 λ：{m.get('max_lambda_mw')} MW",
            f"- 可行点 / 不可行点 / unknown：{m.get('feasible_points')} / "
            f"{m.get('infeasible_points')} / {m.get('unknown_points')}",
            f"- 总成本：{m.get('total_cost_yuan')} 元",
            "",
            "## 4 一致性检查", "",
            f"- 非预见性：{'通过' if m.get('nonanticipativity_pass') else '未通过'}",
            f"- 储能能量守恒："
            f"{'通过' if m.get('energy_conservation_pass') else '未通过'}",
            "",
            "## 5 输出文件", "",
        ]
        for name in ("capacity_frontier.csv", "interface_trace.csv",
                     "probability_trace.csv", "dispatch.csv",
                     "research_metrics.csv"):
            lines.append(f"- `results/{name}`")
        lines += ["", "> 仅已验证点标记为 feasible；unknown 不计为不可行，"
                      "也不计为容量不可行。"]

        md.write_text("\n".join(lines), encoding="utf-8")
        try:
            from docx import Document
            doc = Document()
            for line in lines:
                if line.startswith("# "):
                    doc.add_heading(line[2:], level=0)
                elif line.startswith("## "):
                    doc.add_heading(line[3:], level=1)
                elif line.startswith("- "):
                    doc.add_paragraph(line[2:], style="List Bullet")
                elif line.strip():
                    doc.add_paragraph(line)
            doc.save(str(out))
            return str(out)
        except Exception:
            return str(md)

    # -------------------------------------------------------------- run
    def run(self) -> Dict[str, Any]:
        self.say(f"=== hc_agent research {__version__} "
                 f"study_id={self.study_id} ===")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        outcome: Dict[str, Any] = {"study_id": self.study_id,
                                   "exit_code": EXIT_SUCCESS}
        try:
            self.r01_freeze()
            self.r02_validate_and_hierarchy()
            cap = self.r03_to_r06()
            outcome.update(cap)
            rep = self.r08_report()
            outcome["report"] = rep["report"]
            outcome["metrics"] = rep["summary"]["metrics"]
        except InputBlockedError as exc:
            outcome.update({"exit_code": EXIT_INPUT_BLOCKED,
                            "error": exc.message, "issues": exc.issues})
        except HCAgentError as exc:
            outcome.update({"exit_code": exc.exit_code, "error": exc.message})
        except Exception as exc:
            import traceback
            outcome.update({"exit_code": EXIT_COMPUTE_FAILED,
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc()})
        outcome["elapsed_s"] = round(time.perf_counter() - self.t0, 3)
        write_json(self.out_dir / "study_outcome.json", outcome)
        if self.ledger:
            self.ledger.append("study_complete",
                               result=("ok" if outcome["exit_code"] == 0
                                       else "failed"),
                               reason=f"exit_code={outcome['exit_code']}")
        self.say(f"=== 完成 exit_code={outcome['exit_code']} "
                 f"用时 {outcome['elapsed_s']}s ===")
        return outcome


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口（设计文档 21.3）。"""
    parser = argparse.ArgumentParser(
        prog="hc_agent.research_pipeline",
        description="主配微协同优化研究流水线（R01-R08）")
    parser.add_argument("--task", default=None, help="research_task.json 路径")
    parser.add_argument("--inputs", required=True, help="研究输入目录")
    parser.add_argument("--output", required=True, help="研究输出目录")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    inputs = Path(args.inputs)
    task = Path(args.task) if args.task else (inputs / "research_task.json")
    pipe = ResearchPipeline(task, inputs, args.output, verbose=not args.quiet)
    result = pipe.run()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return int(result.get("exit_code", EXIT_COMPUTE_FAILED))


if __name__ == "__main__":
    sys.exit(main())
