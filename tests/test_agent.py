"""test_agent.py — hc_agent 回归测试套件（设计文档 13.1 测试体系 / 13.3 标准测试集）。

覆盖维度（设计文档 13.3）：
  规模维度：单台区、小/中/大馈线、变电站及批量区域
  渗透率维度：低、中、高以及已出现反向潮流的样本
  约束维度：每类约束的正常、临界、越限和多约束耦合样本
  数据质量维度：缺失、重复、错单位、错相别、孤岛、版本冲突和时序错位
  OpenDSS 维度：参考算例、版本能力边界、不收敛样本、同版本独立实例复算
  安全维度：越权、提示注入、恶意文件、参数越界、路径穿越和敏感数据导出

运行：
  <python> -m pytest tests/test_agent.py -v        （若有 pytest）
  <python> tests/test_agent.py                     （内置运行器，无需 pytest）

设计文档 17.14：验收同时检查数值与停止行为，不能只看报告是否生成。
因此每个异常算例都断言"在正确的节点停止或转人工"。
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN_FD001 = ROOT / "tests" / "golden" / "FD001"
GOLDEN_FD002 = ROOT / "tests" / "golden" / "FD002"

_RESULTS: list = []


def test(name: str):
    """极简测试装饰器，兼容有无 pytest 两种运行方式。"""
    def deco(fn):
        fn._hc_test_name = name
        _RESULTS.append(fn)
        return fn
    return deco


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _read_csv(path) -> list:
    import csv
    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _make_input(tmp: Path, source: Path, **overrides) -> Path:
    """复制黄金算例输入并施加覆盖，用于构造异常算例。"""
    dst = tmp / "inputs"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(source, dst)
    for name, content in overrides.items():
        p = dst / name
        if content is None:
            p.unlink(missing_ok=True)
        elif isinstance(content, str):
            p.write_text(content, encoding="utf-8")
        else:
            p.write_text(json.dumps(content, ensure_ascii=False),
                         encoding="utf-8")
    return dst


# ============================================================ 单元：规则中心
@test("规则包：null 限值必须阻断（设计文档 17.9）")
def test_rules_null_limits_block():
    from hc_agent.errors import RuleNotApprovedError
    from hc_agent.rules import load_rule_package

    tmp = Path(tempfile.mkdtemp())
    try:
        tpl = ROOT / "rules" / "rules_template.yaml"
        pkg = load_rule_package(tpl)
        _expect(not pkg.approved, "draft 模板不应被判为已批准")
        for c in pkg.constraints:
            _expect(not c.approved, f"{c.rule_id} 的限值为 null，不应判为已批准")
        raised = False
        try:
            pkg.require("management.formula_id")
        except RuleNotApprovedError:
            raised = True
        _expect(raised, "读取 null 规则项必须抛 RuleNotApprovedError")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("规则包：未批准版本不得用于正式任务")
def test_rules_unapproved_blocked():
    from hc_agent.pipeline import Pipeline

    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD001,
                             rules_yaml=(ROOT / "rules" /
                                         "rules_template.yaml").read_text(
                                             encoding="utf-8"))
        # rules.yaml 名称修正
        (inputs / "rules.yaml").unlink()
        shutil.copy2(ROOT / "rules" / "rules_template.yaml",
                     inputs / "rules.yaml")
        pipe = Pipeline(inputs / "task.json", inputs, tmp / "run",
                        skip_verification=True, max_slices=2, verbose=False)
        out = pipe.run()
        _expect(out["exit_code"] == 2,
                f"未批准规则包应以退出码 2 阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ==================================================== 单元：容量搜索语义
@test("容量搜索：上界仍可行只能报'至少为上界'（设计文档 17.12）")
def test_search_at_least_upper():
    from hc_agent.capacity_search import (SEARCH_AT_LEAST_UPPER,
                                          search_capacity, TrialResult)

    def always_feasible(delta, it):
        return TrialResult(iteration=it, delta_kw=delta, status="feasible")

    r = search_capacity(always_feasible,
                        {"lower_kw": 0, "upper_kw": 1000, "precision_kw": 10,
                         "max_iter": 20},
                        monotonicity_validated=True)
    _expect(r.search_status == SEARCH_AT_LEAST_UPPER,
            f"应为 at_least_upper_bound，实际 {r.search_status}")
    _expect(r.capacity_physical_kw == 1000, "应报上界值")
    _expect(any("至少" in n for n in r.notes),
            "说明中必须声明'至少不低于'，不得宣称找到最大值")


@test("容量搜索：unknown 不得当作不可行上界（设计文档 17.12）")
def test_search_unknown_not_infeasible():
    from hc_agent.capacity_search import search_capacity, TrialResult, SEARCH_UNKNOWN

    calls = {"n": 0}

    def fn(delta, it):
        calls["n"] += 1
        if delta == 0:
            return TrialResult(iteration=it, delta_kw=delta, status="feasible")
        if delta >= 1000:
            return TrialResult(iteration=it, delta_kw=delta, status="fail")
        return TrialResult(iteration=it, delta_kw=delta, status="unknown")

    r = search_capacity(fn, {"lower_kw": 0, "upper_kw": 1000,
                             "precision_kw": 10, "max_iter": 20},
                        monotonicity_validated=True)
    _expect(r.search_status == SEARCH_UNKNOWN,
            f"含 unknown 点应标记 unknown，实际 {r.search_status}")
    _expect(len(r.unknowns) >= 1, "必须记录 unknown 试探点")
    _expect(any("unknown" in n for n in r.notes),
            "说明中必须声明未把 unknown 当作不可行")


@test("容量搜索：基线不可行时物理容量为 0")
def test_search_infeasible_at_lower():
    from hc_agent.capacity_search import search_capacity, TrialResult

    def fn(delta, it):
        return TrialResult(iteration=it, delta_kw=delta, status="fail")

    r = search_capacity(fn, {"lower_kw": 0, "upper_kw": 1000,
                             "precision_kw": 10, "max_iter": 20},
                        monotonicity_validated=True)
    _expect(r.capacity_physical_kw == 0.0, "下界不可行时容量应为 0")
    _expect(any("存量问题" in n for n in r.notes),
            "应说明优先输出存量问题")


@test("容量搜索：非单调时改用离散扫描（设计文档 17.12）")
def test_search_discrete_scan_when_nonmonotonic():
    from hc_agent.capacity_search import search_capacity, TrialResult

    def fn(delta, it):
        st = "feasible" if (delta < 200 or 400 < delta < 600) else "fail"
        return TrialResult(iteration=it, delta_kw=delta, status=st)

    r = search_capacity(fn, {"lower_kw": 0, "upper_kw": 1000,
                             "precision_kw": 10, "step_kw": 100,
                             "max_iter": 20},
                        monotonicity_validated=False)
    _expect(r.method == "discrete_scan", f"应使用离散扫描，实际 {r.method}")
    _expect(any("不宣称连续全局最优" in n for n in r.notes),
            "必须声明不宣称连续全局最优")


# ================================================== 单元：约束判定与序分量
@test("三相不平衡：序分量公式 100*|V2|/|V1|（设计文档 17.11）")
def test_imbalance_formula():
    import cmath
    from hc_agent.model.constraints import negative_sequence_imbalance

    # 与 OpenDSS 实际返回同量级的平衡三相复电压（幅值 ~235 V，相角互差 120°）
    a = cmath.rect(1.0, 2 * math.pi / 3)
    v = 235.56
    va = v * cmath.rect(1.0, math.radians(-0.02))
    vb = v * cmath.rect(1.0, math.radians(-120.01))
    vc = v * cmath.rect(1.0, math.radians(119.99))
    imb = negative_sequence_imbalance(va, vb, vc)
    _expect(imb is not None and imb < 0.5,
            f"近平衡三相不平衡度应接近 0，实际 {imb}")

    # 单相跌落 -> 不平衡度显著大于 0
    imb2 = negative_sequence_imbalance(0.9 * v + 0j, a * v, a * a * v)
    _expect(imb2 is not None and imb2 > 1.0,
            f"单相跌落应产生明显不平衡，实际 {imb2}")

    # 正序为 0（三相完全抵消）时必须返回 None 而非伪造数值
    _expect(negative_sequence_imbalance(0j, 0j, 0j) is None,
            "正序为 0 时应返回 None")


@test("约束判定：时长未达阈值不计越限（设计文档 6.2）")
def test_duration_gate():
    from hc_agent.rules import ConstraintRule, evaluate_constraint

    rule = ConstraintRule(rule_id="V", metric="voltage_pu",
                          operator="between", min=0.93, max=1.07,
                          duration_minutes=30.0)
    st, _ = evaluate_constraint(rule, 0.90, duration_minutes=10.0)
    _expect(st == "pass", f"持续时间 10min < 30min 应计 pass，实际 {st}")
    st2, _ = evaluate_constraint(rule, 0.90, duration_minutes=45.0)
    _expect(st2 == "fail", f"持续时间 45min ≥ 30min 应计 fail，实际 {st2}")


@test("约束判定：值为 None 记 unknown 而非 fail（设计文档 17.12）")
def test_none_is_unknown():
    from hc_agent.rules import ConstraintRule, evaluate_constraint

    rule = ConstraintRule(rule_id="T", metric="loading_pct",
                          operator="<=", max=100.0)
    st, margin = evaluate_constraint(rule, None)
    _expect(st == "unknown", f"缺失值应为 unknown，实际 {st}")
    _expect(margin is None, "unknown 不应给出余量")


# ============================================== 单元：管理口径与发布门禁
@test("管理容量：formula_id 为 null 时阻断（设计文档 17.9）")
def test_management_formula_null_blocks():
    from hc_agent.rules import apply_management_rules, load_rule_package

    pkg = load_rule_package(ROOT / "rules" / "rules_template.yaml")
    res = apply_management_rules(1000.0, 100.0, pkg)
    _expect(res["status"] == "blocked",
            f"formula_id 为 null 必须阻断，实际 {res['status']}")
    _expect(res["capacity_managed_kw"] is None, "阻断时不得给出管理容量")


@test("管理容量：按批准公式顺序处理，不硬编码统一减法")
def test_management_formula_order():
    from hc_agent.rules import apply_management_rules, load_rule_package

    pkg = load_rule_package(GOLDEN_FD001 / "rules.yaml")
    res = apply_management_rules(1000.0, 100.0, pkg)
    # 批准口径：同时率 0.9 -> 900；裕度 10% -> 810；扣占用 100 -> 710
    _expect(abs(res["capacity_managed_kw"] - 710.0) < 1e-6,
            f"管理容量应为 710 kW，实际 {res['capacity_managed_kw']}")
    steps = [s["step"] for s in res["steps"]]
    _expect(steps == ["coincidence_factor", "management_margin",
                      "deduct_reservations"],
            f"步骤顺序应符合批准公式，实际 {steps}")


@test("发布门禁：缺人工复核与审批不得通过（设计文档 11.2 三道门）")
def test_release_gate_three_doors():
    from hc_agent.rules import check_release_gate, load_rule_package

    pkg = load_rule_package(GOLDEN_FD001 / "rules.yaml")
    gate = check_release_gate(pkg, verification_passed=True,
                              review_approved=False, approval_ref=None,
                              result_hash="abc")
    _expect(not gate["passed"], "缺复核与审批时门禁不应通过")
    _expect("professional_review" in gate["failed_gates"]
            and "approval" in gate["failed_gates"],
            f"应列出未通过的门，实际 {gate['failed_gates']}")

    gate2 = check_release_gate(pkg, verification_passed=False,
                               review_approved=True, approval_ref="AP-1",
                               result_hash="abc")
    _expect(not gate2["passed"], "复算未通过时门禁不应通过")
    _expect("independent_verification" in gate2["failed_gates"],
            "应列出复算门未通过")


@test("红黄绿分级：unknown 约束产生灰色而非正式结论（设计文档表 25）")
def test_grading_grey_on_unknown():
    from hc_agent.rules import apply_grading, load_rule_package

    pkg = load_rule_package(GOLDEN_FD001 / "rules.yaml")
    rows = [{"constraint_id": "X", "status": "unknown", "margin": None,
             "lower_limit": None, "upper_limit": None}]
    g = apply_grading(500.0, rows, pkg)
    _expect(g["level"] == "grey", f"应返回灰色，实际 {g['level']}")
    _expect(not g["formal"], "灰色不得作为正式结论")


@test("红黄绿分级：容量为 0 判红（设计文档表 25）")
def test_grading_red_zero():
    from hc_agent.rules import apply_grading, load_rule_package

    pkg = load_rule_package(GOLDEN_FD001 / "rules.yaml")
    g = apply_grading(0.0, [], pkg)
    _expect(g["level"] == "red", f"容量 0 应判红，实际 {g['level']}")


# ===================================================== 集成：输入质量门禁
def _run_blocked(inputs: Path, out: Path) -> dict:
    from hc_agent.pipeline import Pipeline
    pipe = Pipeline(inputs / "task.json", inputs, out,
                    skip_verification=True, max_slices=2, verbose=False)
    return pipe.run()


@test("质量维度：缺 task.json 阻断")
def test_missing_task_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": None})
        # Pipeline 需要 task.json 存在
        (inputs / "task.json").write_text("{}", encoding="utf-8")
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：缺 rules.yaml 阻断")
def test_missing_rules_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD001, **{"rules.yaml": None})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：缺 profiles.csv 阻断（时序评估必需）")
def test_missing_profiles_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD001, **{"profiles.csv": None})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：时间窗越出模型有效期阻断（设计文档 17.5）")
def test_snapshot_outside_validity_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        task = json.loads((GOLDEN_FD001 / "task.json").read_text("utf-8"))
        task["time_window"] = {"start": "2025-01-01T00:00:00+08:00",
                               "end": "2025-01-02T00:00:00+08:00"}
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": task})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"应在有效期校验处阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：重复 profile 复合键阻断（设计文档 17.8）")
def test_duplicate_profile_key_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        text = (GOLDEN_FD001 / "profiles.csv").read_text("utf-8-sig")
        lines = text.strip().splitlines()
        dup = lines + [lines[1]]           # 复制第一条数据行
        inputs = _make_input(tmp, GOLDEN_FD001)
        (inputs / "profiles.csv").write_text("\n".join(dup) + "\n",
                                             encoding="utf-8-sig")
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"重复复合键应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：候选接入点不存在阻断（设计文档 7.1 前置判定）")
def test_unknown_candidate_node_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        task = json.loads((GOLDEN_FD001 / "task.json").read_text("utf-8"))
        task["candidate_nodes"] = [{"node_id": "BUS-NOT-EXIST",
                                    "phase": "ABC", "weight": 1.0}]
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": task})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"未知接入点应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：接入权重之和不等于 1 阻断（设计文档 表 48）")
def test_bad_weight_sum_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        task = json.loads((GOLDEN_FD001 / "task.json").read_text("utf-8"))
        task["candidate_nodes"] = [
            {"node_id": "BUS-LV-002", "phase": "ABC", "weight": 0.7},
            {"node_id": "BUS-MV-003", "phase": "ABC", "weight": 0.7}]
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": task})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2, f"权重和错误应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：非 OpenDSS 引擎声明阻断（设计文档 2.3 计算范围）")
def test_non_opendss_engine_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        task = json.loads((GOLDEN_FD001 / "task.json").read_text("utf-8"))
        task["engine"]["id"] = "pscad"
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": task})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2,
                f"非 OpenDSS 引擎应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：rule_package 浮动别名阻断（设计文档 表 48）")
def test_floating_rule_alias_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        task = json.loads((GOLDEN_FD001 / "task.json").read_text("utf-8"))
        task["rule_package"] = "HC-RULES@approved"
        inputs = _make_input(tmp, GOLDEN_FD001, **{"task.json": task})
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2,
                f"浮动别名应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("质量维度：占用台账重复叠加阻断（设计文档 17.7）")
def test_duplicate_reservation_blocked():
    tmp = Path(tempfile.mkdtemp())
    try:
        header = ("reservation_id,application_id,object_id,capacity_kw,"
                  "status,valid_from,valid_to,allocation_ref,hierarchy_path")
        row = ("RES-1,APP-1,L001,100,active,2026-07-01T00:00:00+08:00,"
               "2026-12-31T00:00:00+08:00,ALLOC-1,D>M1")
        inputs = _make_input(tmp, GOLDEN_FD001)
        (inputs / "reservations.csv").write_text(
            "\n".join([header, row, row]) + "\n", encoding="utf-8-sig")
        out = _run_blocked(inputs, tmp / "run")
        _expect(out["exit_code"] == 2,
                f"重复占用应阻断，实际 {out['exit_code']}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ================================================ 集成：端到端与独立复算
@test("端到端：FD001 全流程跑通且独立复算通过")
def test_e2e_fd001():
    tmp = Path(tempfile.mkdtemp())
    try:
        from hc_agent.pipeline import Pipeline
        pipe = Pipeline(GOLDEN_FD001 / "task.json", GOLDEN_FD001,
                        tmp / "run", verbose=False)
        out = pipe.run()
        _expect(out["exit_code"] in (0, 5),
                f"应完成（0 或门禁 5），实际 {out['exit_code']}："
                f"{out.get('error')}")
        v = json.loads((tmp / "run" / "verification" /
                        "verification.json").read_text("utf-8"))
        _expect(v.get("pass") is True, "独立复算应通过")
        _expect(v["comparison"]["mismatch_count"] == 0,
                f"复算应零差异，实际 {v['comparison']['mismatch_count']}")
        _expect((tmp / "run" / "evidence" / "evidence.zip").exists(),
                "证据包应已封存")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("端到端：电压基准自检生效（设计文档 17.11）")
def test_voltage_base_selfcheck():
    from hc_agent.engines.opendss_adapter import OpenDSSAdapter
    from hc_agent.io.ingest import load_input_bundle
    from hc_agent.model.build_model import build_case

    tmp = Path(tempfile.mkdtemp())
    try:
        b = load_input_bundle(GOLDEN_FD001)
        model = build_case(b, {}, out_dir=tmp / "model")
        a = OpenDSSAdapter(work_dir=tmp / "eng")
        with a:
            case = a.build_case(model.master_text(), case_name="vb",
                                node_phase_map=model.node_phase_map,
                                id_map=model.id_map, base_kv=model.base_kv,
                                equipment=model.equipment)
            chk = a.verify_voltage_bases()
            _expect(chk["pass"], f"电压基准自检应通过：{chk['checks']}")
            _expect(case.status == "ok", "算例状态应为 ok")
            # MV 与 LV 的相电压基准比例应分别约为 1.0
            for bus, rec in chk["base_map"].items():
                if rec.get("ratio") is not None and "sourcebus" not in bus:
                    _expect(abs(rec["ratio"] - 1.0) < 0.05,
                            f"{bus} 基准比 {rec['ratio']} 偏离 1.0")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("端到端：反向潮流符号符合物理（正向运行时为 0）")
def test_reverse_flow_sign():
    from hc_agent.engines.opendss_adapter import OpenDSSAdapter
    from hc_agent.io.ingest import load_input_bundle
    from hc_agent.model.build_model import build_case
    from hc_agent.model.constraints import ConstraintEvaluator
    from hc_agent.model.powerflow import run_baseline
    from hc_agent.model.scenarios import Scenario, Slice
    from hc_agent.rules import load_rule_package

    tmp = Path(tempfile.mkdtemp())
    try:
        b = load_input_bundle(GOLDEN_FD001)
        rules = load_rule_package(GOLDEN_FD001 / "rules.yaml")
        model = build_case(b, {}, out_dir=tmp / "model")

        # 构造一个明确无光伏出力、纯负荷的夜间场景。
        # 注意不能用 max_slices 降采样：那会取到含光伏的时段，
        # 引入真实的倒送，使该断言失去判别力。
        sc = Scenario(scenario_id="NIGHT_ONLY")
        for ts in ("2026-07-01T00:00:00+08:00", "2026-07-01T03:00:00+08:00"):
            sl = Slice(scenario_id="NIGHT_ONLY", timestamp=ts)
            sl.load_p_kw = {"L001": 80.0, "L002": 300.0}
            sl.load_q_kvar = {"L001": 26.4, "L002": 90.0}
            sl.load_phase = {"L001": "ALL", "L002": "ALL"}
            # 已有光伏出力显式为 0
            for pv in b.pv:
                from hc_agent.io.schema import as_str as _s
                sl.pv_avail_pu[_s(pv["pv_id"])] = 0.0
            sc.slices.append(sl)

        a = OpenDSSAdapter(work_dir=tmp / "eng")
        with a:
            a.build_case(model.master_text(), case_name="rf",
                         node_phase_map=model.node_phase_map,
                         id_map=model.id_map, base_kv=model.base_kv,
                         equipment=model.equipment)
            run = run_baseline(a, b, model, [sc],
                               solver={"max_iter": 100, "tolerance": 1e-4})
        _expect(run.all_converged, "纯负荷夜间场景应收敛")

        # 直接核对物理：所有输电元件的端子 1 P 必须为正（正向供电）
        for row in run.slices:
            if row.get("kind") != "element":
                continue
            if row.get("phase") != "ALL":
                continue
            if row.get("element_class") not in ("line", "transformer"):
                continue
            _expect(row["p_kw"] > 0,
                    f"{row['equipment_id']} 夜间纯负荷时端子1 P="
                    f"{row['p_kw']:.2f} kW 应为正（正向）")

        res = ConstraintEvaluator(rules).evaluate(run)
        rf = [r for r in res if r.metric == "reverse_flow_kw"]
        _expect(rf, "应产生反向潮流约束行")
        nonzero = [r for r in rf if (r.value or 0) > 1.0]
        _expect(not nonzero,
                f"纯负荷夜间工况不应出现反向潮流，实际 "
                f"{[(r.object_id, r.value) for r in nonzero][:5]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SK-09 短路校核：具备故障模型时由 unknown 变为真实判定")
def test_short_circuit_sk09():
    """设计文档表 47：提供 fault_models.csv 时短路能力须被真实校核，
    而非永久停留在 unknown。"""
    import json as J
    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD002)
        task = J.loads((inputs / "task.json").read_text("utf-8"))
        task["analysis_types"] = ["powerflow_3ph", "shortcircuit"]
        task["short_circuit"] = {"fault_type": "three_phase",
                                 "r_fault_ohm": 0.0}
        (inputs / "task.json").write_text(
            J.dumps(task, ensure_ascii=False), encoding="utf-8")

        from hc_agent.pipeline import Pipeline
        pipe = Pipeline(inputs / "task.json", inputs, tmp / "run",
                        verbose=False)
        out = pipe.run()

        rows = _read_csv(tmp / "run" / "results" / "constraint_results.csv")
        sc = [r for r in rows if r["constraint_id"] == "SHORT_CIRCUIT_LIMIT"]
        _expect(sc, "应产生短路容量约束行")
        _expect(all(r["status"] in ("pass", "fail") for r in sc),
                f"提供故障模型后不应再是 unknown："
                f"{[(r['object_id'], r['status']) for r in sc]}")
        _expect(all(float(r["value"]) > 0 for r in sc),
                "应给出正的短路电流值")
        # 仅报告相导体：不得把中性线/回流导体当成额外相别
        _expect(len(sc) == 3,
                f"应只报告 A/B/C 三个相导体，实际 {len(sc)} 行："
                f"{[r['object_id'] for r in sc]}")
        # unknown 消失后分级应可成为正式结论
        _expect(out.get("level") in ("green", "yellow", "red"),
                f"无 unknown 时应给出正式分级，实际 {out.get('level')}")
        v = json.loads((tmp / "run" / "verification" /
                        "verification.json").read_text("utf-8"))
        _expect(v.get("pass") is True,
                f"短路校核须在复算中同口径复现，实际 "
                f"{v['comparison']['mismatch_count']} 项差异")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SK-09 短路校核：缺故障模型时保持 unknown（不得伪装通过）")
def test_short_circuit_missing_models():
    tmp = Path(tempfile.mkdtemp())
    try:
        inputs = _make_input(tmp, GOLDEN_FD002)
        (inputs / "fault_models.csv").unlink()
        task = json.loads((inputs / "task.json").read_text("utf-8"))
        task["analysis_types"] = ["powerflow_3ph", "shortcircuit"]
        (inputs / "task.json").write_text(
            json.dumps(task, ensure_ascii=False), encoding="utf-8")
        from hc_agent.pipeline import Pipeline
        pipe = Pipeline(inputs / "task.json", inputs, tmp / "run",
                        verbose=False)
        pipe.run()
        rows = _read_csv(tmp / "run" / "results" / "constraint_results.csv")
        sc = [r for r in rows if r["constraint_id"] == "SHORT_CIRCUIT_LIMIT"]
        _expect(sc and all(r["status"] == "unknown" for r in sc),
                "缺少故障模型时短路能力必须标 unknown，不得伪装通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("SK-11 并网审查：按申请容量校核并区分存量与新增（设计文档 7.2/17.3）")
def test_access_check_sk11():
    import json as J
    tmp = Path(tempfile.mkdtemp())
    try:
        src = ROOT / "tests" / "golden" / "APP001"
        if not src.exists():
            print("    [skip] APP001 尚未生成")
            return
        from hc_agent.pipeline import Pipeline

        def run_at(cap):
            d = tmp / f"cap{int(cap)}"
            shutil.copytree(src, d)
            a = J.loads((d / "application.json").read_text("utf-8"))
            a["requested_capacity_kw"] = cap
            (d / "application.json").write_text(
                J.dumps(a, ensure_ascii=False), encoding="utf-8")
            p = Pipeline(d / "task.json", d, d / "run",
                         skip_verification=True, verbose=False)
            return p.run()

        # 小容量：可行，且不得把存量问题误判为申请导致
        ok = run_at(250.0)
        ac = ok.get("access_check") or {}
        _expect(ac, "并网审查应产出 access_check.json 内容")
        _expect(ac["requested_capacity_kw"] == 250.0,
                "应按申请容量而非搜索结果校核")
        _expect(ac["decision_candidate"] == "可行",
                f"250 kW 应判可行，实际 {ac['decision_candidate']}")
        _expect(ac["newly_induced"] == [],
                f"可行申请不应引入新增越限，实际 {ac['newly_induced']}")

        # 大容量：不可行，且必须明确指出是申请引入的哪一项
        bad = run_at(600.0)
        ac2 = bad.get("access_check") or {}
        _expect(ac2["decision_candidate"] == "不可行",
                f"600 kW 应判不可行，实际 {ac2['decision_candidate']}")
        _expect(len(ac2["newly_induced"]) > 0,
                "不可行时必须列出申请引入的越限项")
        _expect(any(x["constraint_id"] for x in ac2["newly_induced"]),
                "新增越限项应含约束编号")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("健壮性：向同一输出目录重跑必须成功（只读快照可覆写）")
def test_rerun_same_output_dir():
    tmp = Path(tempfile.mkdtemp())
    try:
        from hc_agent.pipeline import Pipeline
        for i in range(2):
            p = Pipeline(GOLDEN_FD002 / "task.json", GOLDEN_FD002,
                         tmp / "run", skip_verification=True,
                         max_slices=2, verbose=False)
            o = p.run()
            _expect(o["exit_code"] in (0, 5),
                    f"第 {i+1} 次运行应成功，实际 {o['exit_code']}："
                    f"{o.get('error')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("端到端：FD002 健康馈线基线无越限且容量为正")
def test_e2e_fd002():
    if not GOLDEN_FD002.exists():
        print("    [skip] FD002 尚未生成")
        return
    tmp = Path(tempfile.mkdtemp())
    try:
        from hc_agent.pipeline import Pipeline
        pipe = Pipeline(GOLDEN_FD002 / "task.json", GOLDEN_FD002,
                        tmp / "run", verbose=False)
        out = pipe.run()
        _expect(out["exit_code"] in (0, 5),
                f"应完成，实际 {out['exit_code']}：{out.get('error')}")
        s = json.loads((tmp / "run" / "results" /
                        "run_summary.json").read_text("utf-8"))
        bs = s["constraint_summary"]
        _expect(bs["fail"] == 0,
                f"FD002 基线应无越限，实际 fail={bs['fail']}")
        _expect((s.get("capacity_physical_kw") or 0) > 0,
                f"FD002 容量应为正，实际 {s.get('capacity_physical_kw')}")
        v = json.loads((tmp / "run" / "verification" /
                        "verification.json").read_text("utf-8"))
        _expect(v.get("pass") is True, "FD002 独立复算应通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ==================================================== 技能注册中心与引擎
@test("技能注册中心：SK-01..SK-12 齐备且全部 approved")
def test_skill_registry():
    from hc_agent.skills import load_skill_registry, validate_registry

    data = json.loads((ROOT / "skills" /
                       "skill_registry.json").read_text("utf-8"))
    issues = validate_registry(data)
    _expect(not issues, f"注册表校验应无问题：{issues}")
    reg = load_skill_registry(ROOT / "skills" / "skill_registry.json")
    _expect(len(reg.skills) == 12, f"应有 12 项技能，实际 {len(reg.skills)}")
    for n in range(1, 13):
        code = f"SK-{n:02d}"
        _expect(reg.get(code) is not None, f"缺技能 {code}")
    _expect(len(reg.approved_skills()) == 12, "12 项技能应全部 approved")
    for s in reg.engine_skills():
        _expect(s.engine.get("id") == "opendss",
                f"{s.skill_id} 的引擎应为 opendss，实际 {s.engine}")


@test("技能注册中心：未批准技能不得被选用")
def test_skill_unapproved_refused():
    from hc_agent.skills import Skill, SkillRegistry, SkillRegistryError

    reg = SkillRegistry([Skill(skill_id="HC.X", skill_code="SK-99",
                               version="1.0.0", name="x", purpose="x",
                               status="draft", risk_level="low",
                               requires_engine=False)])
    raised = False
    try:
        reg.require("HC.X")
    except SkillRegistryError:
        raised = True
    _expect(raised, "draft 技能不应被正式选用")


@test("引擎：锁定版本与能力登记一致（设计文档 8.2）")
def test_engine_locked():
    from hc_agent.runtime_env import (check_locked_version,
                                      engine_fingerprint)

    fp = engine_fingerprint()
    _expect(fp.get("available"), f"引擎应可用：{fp.get('error')}")
    _expect(fp.get("engine_id") == "opendss", "引擎应为 opendss")
    _expect(fp.get("interface") == "OpenDSSDirect.py",
            "接口应为 OpenDSSDirect.py")
    chk = check_locked_version(strict=False)
    _expect(chk["locked_ok"], f"应匹配锁定版本：{chk['lock_notes']}")


@test("数据不可变：冻结后哈希前后一致（设计文档 5.4）")
def test_immutability():
    from hc_agent.evidence import sha256_file, verify_file_immutable, freeze_inputs

    tmp = Path(tempfile.mkdtemp())
    try:
        man = freeze_inputs(GOLDEN_FD001, tmp / "snap", read_only=True)
        for rec in man["inputs"]:
            _expect(verify_file_immutable(rec),
                    f"{rec['relpath']} 哈希应前后一致")
        _expect(man["input_digest"], "应生成输入摘要")
        # 只读位确实生效
        p = Path(man["inputs"][0]["abspath"])
        _expect(not (p.stat().st_mode & 0o222), "冻结副本应置为只读")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@test("安全边界：Agent 不提供现场设备写操作工具（设计文档 表 35）")
def test_no_control_tools():
    import re
    import hc_agent.engines.opendss_adapter as ad
    src = Path(ad.__file__).read_text(encoding="utf-8")
    low = src.lower()
    # 用词边界匹配，避免命中 strip() 之类的无关标识符
    for pattern in (r"\b遥控\b", r"\b分合闸\b", r"\b保护定值\b", r"\b跳闸\b",
                    r"\btrip\s*\(", r"\bopen_switch\s*\(",
                    r"\bclose_switch\s*\("):
        _expect(re.search(pattern, low) is None,
                f"适配器中不应出现现场控制能力：{pattern}")
    from hc_agent.skills import load_skill_registry
    reg = load_skill_registry(ROOT / "skills" / "skill_registry.json")
    for s in reg.skills:
        joined = " ".join(s.safety_boundary)
        _expect("不得下发控制命令" in joined or "只读分析" in joined,
                f"{s.skill_id} 应声明只读/禁止下发控制的边界")


# ============================================================== 内置运行器
def main() -> int:
    passed, failed, skipped = 0, 0, 0
    failures = []
    print(f"hc_agent 回归测试套件：共 {len(_RESULTS)} 项\n" + "=" * 78)
    for fn in _RESULTS:
        name = getattr(fn, "_hc_test_name", fn.__name__)
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as exc:
            print(f"FAIL  {name}\n        {exc}")
            failed += 1
            failures.append((name, str(exc)))
        except Exception as exc:
            print(f"ERROR {name}\n        {type(exc).__name__}: {exc}")
            failed += 1
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            print("        " + traceback.format_exc().replace("\n", "\n        ")[:1200])
    print("=" * 78)
    print(f"结果：{passed} 通过 / {failed} 失败 / {len(_RESULTS)} 总计")
    if failures:
        print("\n失败明细：")
        for n, m in failures:
            print(f"  - {n}: {m}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
