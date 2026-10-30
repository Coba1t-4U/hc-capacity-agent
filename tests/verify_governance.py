"""Verify 从"市场力监测平台"借鉴的三项能力。

借鉴的是**架构做法**，不是业务内容（电力现货市场力与配网承载力是两个主题）：

  1. 哈希链审计 —— 承载力结论要能证明"没被改过"
  2. 权限分级 + 动作白名单 —— 跑/出候选/签发分级，且永久禁用设备写操作
  3. 可执行技能契约 —— skill.json 可校验、可加载、受权限管
并配一个新的应用场景：**整改建议候选**（越限了能怎么办）。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 74)
print("借鉴能力验证：审计链 / 权限分级 / 技能契约 / 整改建议")
print("=" * 74)

tmp = Path(tempfile.mkdtemp(prefix="hc-borrow-"))

try:
    # ============================================================ 1. 审计链
    print("\n1) 哈希链审计（audit_chain）")
    from hc_agent.audit_chain import AuditChain, chain_for_run, seal_run

    cpath = tmp / "chain.jsonl"
    c = AuditChain(cpath)
    for i in range(5):
        c.append(action=f"step:S0{i+1}", status="ok", run_id="RUN-T",
                 detail={"i": i})
    v = c.verify()
    check("正常链校验通过", v["ok"] is True, str(v))
    check("条数正确", v["count"] == 5, str(v["count"]))
    check("首条的 previous_hash 为空",
          c.read_all()[0]["previous_hash"] == "")
    check("后续每条指向上一条的哈希",
          all(c.read_all()[i]["previous_hash"]
              == c.read_all()[i - 1]["event_hash"]
              for i in range(1, 5)))

    # 篡改内容
    lines = cpath.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[2])
    rec["detail"]["i"] = 999
    lines[2] = json.dumps(rec, ensure_ascii=False)
    cpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v2 = c.verify()
    check("改内容能被发现", v2["ok"] is False, str(v2))
    check("断点定位到第 3 条", v2["broken_at"] == 3, str(v2.get("broken_at")))
    check("原因说明内容被改", "内容被改过" in v2["reason"], v2["reason"])

    # 删掉中间一条
    c2p = tmp / "chain2.jsonl"
    c2 = AuditChain(c2p)
    for i in range(5):
        c2.append(action=f"a{i}", status="ok")
    ls = c2p.read_text(encoding="utf-8").splitlines()
    c2p.write_text("\n".join(ls[:2] + ls[3:]) + "\n", encoding="utf-8")
    v3 = c2.verify()
    check("删中间一条能被发现", v3["ok"] is False, str(v3))
    check("删行判定为 previous_hash 不匹配",
          "previous_hash" in v3["reason"], v3["reason"])

    # 坏 JSON
    c3p = tmp / "chain3.jsonl"
    c3 = AuditChain(c3p)
    c3.append(action="a", status="ok")
    with open(c3p, "a", encoding="utf-8") as fh:
        fh.write("{坏行\n")
    v4 = c3.verify()
    check("坏 JSON 行被识别", v4["ok"] is False and "JSON" in v4["reason"],
          v4["reason"])

    # 空链不算失败
    v5 = AuditChain(tmp / "empty.jsonl").verify()
    check("空链判定为通过（空≠篡改）", v5["ok"] is True and v5["count"] == 0)

    # 真实运行补建
    from hc_agent.desktop import runs as runs_mod
    real = runs_mod.resolve_run_dir("FD002")
    if real is not None:
        r = seal_run(real)
        check("能给真实运行补建链", r.get("ok"), str(r))
        rr = chain_for_run(real).verify()
        check("补建后校验通过", rr["ok"] is True, str(rr))
        check("补建后条数 > 0", rr["count"] > 0, str(rr["count"]))
        # 幂等：再补一次不重复写
        n1 = chain_for_run(real).verify()["count"]
        seal_run(real)
        n2 = chain_for_run(real).verify()["count"]
        check("重复补建是幂等的", n1 == n2, f"{n1} -> {n2}")
    else:
        check("找到 FD002 运行", False)

    # ============================================================ 2. 权限分级
    print("\n2) 权限分级与动作白名单（policy）")
    from hc_agent.policy import (FORBIDDEN_ACTIONS, Policy, PermissionLevel,
                                 PolicyViolation, RuleGuard, default_policy,
                                 policy_from_meta)

    p0 = Policy(level=PermissionLevel.READ_ONLY)
    p1 = default_policy()
    p2 = Policy(level=PermissionLevel.CANDIDATE_GENERATION)
    p3 = Policy(level=PermissionLevel.APPROVED_EXECUTION)

    check("默认级别是离线仿真", p1.level == PermissionLevel.OFFLINE_SIMULATION)
    check("L0 能读", p0.allowed("read_run"))
    check("L0 不能跑仿真", p0.allowed("analyze_opendss_case") is False)
    check("L1 能跑仿真", p1.allowed("analyze_opendss_case"))
    check("L1 不能出整改候选", p1.allowed("propose_remediation") is False)
    check("L2 能出整改候选", p2.allowed("propose_remediation"))
    check("L2 不能签发", p2.allowed("approve_capacity") is False)
    check("L3 能签发", p3.allowed("approve_capacity"))
    check("任何级别都不能写设备",
          all(p.allowed("write_device") is False for p in (p0, p1, p2, p3)))
    check("禁用动作清单里有设备写操作",
          "write_device" in FORBIDDEN_ACTIONS
          and "bypass_human_approval" in FORBIDDEN_ACTIONS)
    check("未登记动作默认拒绝", p3.allowed("something_new") is False)

    g = RuleGuard(p1)
    raised = False
    try:
        g.check("propose_remediation")
    except PolicyViolation:
        raised = True
    check("越权抛异常（不是静默返回 False）", raised)

    check("元数据 permission_level=2 解析正确",
          policy_from_meta({"permission_level": 2}).level
          == PermissionLevel.CANDIDATE_GENERATION)
    check("元数据非法值回落默认（不静默提权）",
          policy_from_meta({"permission_level": "garbage"}).level
          == PermissionLevel.OFFLINE_SIMULATION)
    check("元数据越界回落默认",
          policy_from_meta({"permission_level": 99}).level
          == PermissionLevel.OFFLINE_SIMULATION)

    # ============================================================ 3. 技能契约
    print("\n3) 可执行技能契约（skill_runtime）")
    from hc_agent.skill_runtime import (SkillLoadError, SkillRegistry,
                                        SkillRuntime, default_registry)

    reg = default_registry()
    # 现在共 5 个：3 个业务技能（整改×2 + 审计）+ 2 个开发文档新增
    # （口径绑定 HC.CALIBER.BINDING、验收矩阵 HC.ACCEPTANCE.MATRIX）
    check("加载到 5 个契约", len(reg.contracts) == 5,
          str(sorted(reg.contracts)))
    check("整改与审计三个契约仍在",
          all(k in reg.contracts for k in
              ("HC.REMEDIATION.PROPOSE", "HC.REMEDIATION.FROM_RUN",
               "HC.AUDIT.VERIFY")))
    for cid in ("HC.REMEDIATION.PROPOSE", "HC.REMEDIATION.FROM_RUN",
                "HC.AUDIT.VERIFY"):
        check(f"契约 {cid} 在", cid in reg.contracts)
    check("审计技能是只读",
          reg.get("HC.AUDIT.VERIFY").permission == "read_only")
    check("整改技能要求候选生成权限",
          reg.get("HC.REMEDIATION.PROPOSE").permission
          == "candidate_generation")
    check("整改技能不允许自动触发",
          reg.get("HC.REMEDIATION.PROPOSE").auto_enabled is False)
    check("每个动作都能反查到技能",
          all(reg.for_action(a) is not None for a in
              ("propose_remediation", "propose_remediation_from_run",
               "verify_audit_chain")))

    # 契约校验：坏输入必须报错，不能静默跳过
    bad_dir = tmp / "bad_skills" / "BAD"
    bad_dir.mkdir(parents=True, exist_ok=True)
    (bad_dir / "skill.json").write_text(
        json.dumps({"id": "BAD", "version": "1"}), encoding="utf-8")
    raised = False
    try:
        SkillRegistry(tmp / "bad_skills")
    except SkillLoadError:
        raised = True
    check("缺字段的契约加载时报错", raised)

    bad2 = tmp / "bad_skills2" / "BAD2"
    bad2.mkdir(parents=True, exist_ok=True)
    (bad2 / "skill.json").write_text(json.dumps({
        "id": "BAD2", "version": "1.0.0", "display_name": "x",
        "actions": ["no_such_action"], "handler": "hc_agent.remediation:propose",
        "permission": "read_only", "auto_enabled": False}), encoding="utf-8")
    raised = False
    try:
        SkillRegistry(tmp / "bad_skills2")
    except SkillLoadError as exc:
        raised = "未在 policy" in str(exc)
    check("动作未登记时报错", raised)

    bad3 = tmp / "bad_skills3" / "BAD3"
    bad3.mkdir(parents=True, exist_ok=True)
    (bad3 / "skill.json").write_text(json.dumps({
        "id": "BAD3", "version": "1.0.0", "display_name": "x",
        "actions": ["propose_remediation"],
        "handler": "hc_agent.no_such_module:fn",
        "permission": "candidate_generation", "auto_enabled": False}),
        encoding="utf-8")
    raised = False
    try:
        SkillRegistry(tmp / "bad_skills3")
    except SkillLoadError:
        raised = True
    check("handler 不存在时报错", raised)

    # 运行时：权限闸门生效
    rt_l1 = SkillRuntime(reg, p1)
    raised = False
    try:
        rt_l1.execute("propose_remediation", binding_constraint="VOLTAGE_LIMIT")
    except PolicyViolation:
        raised = True
    check("L1 执行整改技能被拒", raised)

    rt_l2 = SkillRuntime(reg, p2)
    r = rt_l2.execute("propose_remediation", binding_constraint="VOLTAGE_LIMIT")
    check("L2 执行整改技能成功", r.get("ok"), str(r))
    check("结果带技能标识", r.get("skill") == "HC.REMEDIATION.PROPOSE")
    check("返回了候选", len(r["result"]["candidates"]) >= 1)

    r_missing = rt_l2.execute("propose_remediation")
    check("缺必需输入时明确报错",
          r_missing.get("ok") is False and "缺少必需输入" in str(r_missing),
          str(r_missing))

    rt_l0 = SkillRuntime(reg, p0)
    if real is not None:
        rv = rt_l0.execute("verify_audit_chain", run_dir=str(real))
        check("L0 可执行只读审计校验", rv.get("ok"), str(rv))

    # ============================================================ 4. 整改建议
    print("\n4) 整改建议候选（新应用场景）")
    from hc_agent import remediation as rm

    rv1 = rm.propose(binding_constraint="VOLTAGE_LIMIT",
                     voltage_violations=["lv_t2_n2"])
    ids = [x["id"] for x in rv1["candidates"]]
    check("电压越限给电压类候选",
          all(i.startswith("RM-V") for i in ids) and ids, str(ids))
    check("每条候选都有四要素",
          all(all(k in c for k in ("why", "effect", "limits", "basis"))
              for c in rv1["candidates"]))
    check("带免责声明", "候选方向" in rv1["disclaimer"])

    rv2 = rm.propose(binding_constraint="THERMAL_LIMIT",
                     overloaded=["t2"])
    ids2 = [x["id"] for x in rv2["candidates"]]
    check("过载给载流类候选",
          all(i.startswith("RM-T") for i in ids2) and ids2, str(ids2))

    rv3 = rm.propose(binding_constraint="", )
    check("没有越限就不给候选", rv3["count"] == 0, str(rv3["count"]))

    rv4 = rm.propose(binding_constraint="VOLTAGE_LIMIT",
                     voltage_violations=["a"], overloaded=["b"])
    ids4 = [x["id"] for x in rv4["candidates"]]
    check("多约束同时给候选",
          any(i.startswith("RM-V") for i in ids4)
          and any(i.startswith("RM-T") for i in ids4), str(ids4))

    if real is not None:
        from_run_res, _ = rm.from_run(real)
        check("能从真实运行出候选", from_run_res.get("ok"), str(from_run_res))
        check("绑定约束读对了",
              from_run_res.get("binding_constraint") == "VOLTAGE_LIMIT",
              str(from_run_res.get("binding_constraint")))
    txt = rm.format_proposals(rv1)
    for k in ("为什么", "效果", "局限", "依据", "候选方向"):
        check(f"文本含「{k}」", k in txt)

    # ============================================================ 5. 问答接线
    print("\n5) 问答接线")
    from hc_agent.qa import Facts, Responder, intent
    for q, want in (
            ("电压越限了怎么办", "remediation"),
            ("能怎么整改", "remediation"),
            ("有什么办法提升承载力", "remediation"),
            ("这次运行有没有被改过", "audit_check"),
            ("哈希对得上吗", "audit_check"),
    ):
        got = intent(q).name
        check(f"意图 {q[:14]}… → {want}", got == want, f"实际 {got}")
    for q, expect in (
            ("算一下桌面上的算例", "dss_analyze"),
            ("还能接多少光伏", "capacity"),
            ("最低电压是多少", "min_voltage"),
            ("FD002 复算通过了吗", "verification"),
    ):
        got = intent(q).name
        check(f"「{q}」仍是 {expect}", got == expect, f"实际 {got}")

    resp = Responder(Facts())
    if real is not None:
        a1 = resp.answer("FD002 电压越限了怎么办")
        check("整改回答含候选编号", "RM-V-" in a1.text, a1.text[:80])
        check("整改回答含免责声明", "候选方向" in a1.text)
        a2 = resp.answer("FD002 这次运行有没有被改过")
        check("审计回答给出通过/未通过", ("通过" in a2.text
                                        or "未通过" in a2.text), a2.text[:80])
        check("审计回答说明只校验记录完整性",
              "不代表结论" in a2.text or "记录完整性" in a2.text,
              a2.text[-120:])

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
