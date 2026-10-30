"""Verify 开发文档带来的五项增量。

  1. DL/T 2041 口径（知识库 + 问答）
  2. 口径绑定凭证 B={F,v_calc,Θ,γ} 与自动失效
  3. 质量治理产物标准化（quality_issues.csv 落点）
  4. 验收指标矩阵（九条，四态判定）
  5. 可观测性汇总
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
print("开发文档增量验证：DL/T 2041 / 口径绑定 / 质量产物 / 验收矩阵 / 可观测性")
print("=" * 74)

tmp = Path(tempfile.mkdtemp(prefix="hc-dev-doc-"))
try:
    from hc_agent.desktop import runs as runs_mod
    REAL = runs_mod.resolve_run_dir("FD002")
    check("能找到 FD002 作为样本", REAL is not None, str(REAL))

    # ============================================================ 1. DL/T 2041
    print("\n1) DL/T 2041（知识库 + 问答）")
    kdir = ROOT / "hc_agent" / "knowledge" / "builtin"
    doc = kdir / "31_DL-T-2041承载力评估导则.md"
    check("知识库有第 31 篇", doc.is_file(), str(doc))
    if doc.is_file():
        t = doc.read_text(encoding="utf-8")
        for kw in ("2041-2019", "2041-2025", "系统级", "设备级", "反向负载率",
                   "80%", "2026-06-18", "下级服从上级", "春秋"):
            check(f"文档含「{kw}」", kw in t)
    idx = (kdir / "15_参考文献索引.md").read_text(encoding="utf-8")
    check("参考文献索引含 DL/T 2041", "DL/T 2041" in idx)

    from hc_agent.qa import Facts, Responder, intent
    for q, want in (("DL/T 2041 怎么说", "standard_2041"),
                    ("系统级承载力和设备级区别", "standard_2041")):
        got = intent(q).name
        check(f"意图 {q[:16]}… → {want}", got == want, f"实际 {got}")
    # 普通"口径"问题不能被抢走
    check("「口径是什么」仍是 caliber", intent("口径是什么").name == "caliber",
          intent("口径是什么").name)

    # ============================================================ 2. 口径绑定
    print("\n2) 口径绑定凭证 B={F, v_calc, Θ, γ}")
    from hc_agent import caliber as cal

    if REAL is not None:
        b = cal.from_run(REAL)
        check("能从真实运行重建凭证", b is not None)
        if b is not None:
            d = b.to_dict()
            check("含四分量", all(k in d for k in
                              ("data_fingerprint", "calc_version",
                               "caliber", "params")))
            check("数据指纹非空", bool(d["data_fingerprint"]),
                  str(d["data_fingerprint"])[:20])
            check("计算版本含引擎", bool(d["calc_version"].get("engine_id")),
                  str(d["calc_version"]))
            check("口径含规则包", bool(d["caliber"].get("rule_package")),
                  str(d["caliber"]))
            check("凭证 digest 为 32 位", len(b.digest()) == 32, b.digest())
            check("同一运行两次重建 digest 一致",
                  cal.from_run(REAL).digest() == b.digest())

        r = cal.check(REAL)
        check("原样运行校验通过", r.get("ok") is True, str(r.get("reason")))
        check("通过时无差异", not r.get("diffs"), str(r.get("diffs")))

        # 复制一份改规则包 → 必须失效
        cp = tmp / "FD002_copy"
        shutil.copytree(REAL, cp)
        rf = cp / "config" / "rules.yaml"
        check("副本里能找到 rules.yaml", rf.is_file())
        if rf.is_file():
            rf.write_text(rf.read_text(encoding="utf-8") + "\n# 改动\n",
                          encoding="utf-8")
            r2 = cal.check(cp)
            check("改规则包后自动失效", r2.get("ok") is False, str(r2.get("reason")))
            comps = [x["component"] for x in (r2.get("diffs") or [])]
            check("差异定位到参数分量", "params" in comps, str(comps))
            txt = cal.format_check(r2)
            check("可读文本标出失效", "失效" in txt, txt[:60])
            check("可读文本给出前后值", "→" in txt, txt[:120])

        # 不存在的目录
        r3 = cal.check(tmp / "no_such_run")
        check("不存在目录返回 available=false",
              r3.get("available") is False, str(r3))

    # ============================================================ 3. 质量产物
    print("\n3) 质量治理产物标准化")
    from hc_agent.io import quality as Q

    check("定义了十列", Q.COLUMNS == ["issue_id", "file", "sheet", "row",
                                    "field", "object_id", "severity", "code",
                                    "message", "blocking"], str(Q.COLUMNS))
    check("落点文件名正确", Q.FILENAME == "quality_issues.csv")

    if REAL is not None:
        p_root = Path(REAL) / "quality_issues.csv"
        check("真实运行根目录有质量文件", p_root.is_file(), str(p_root))
        res = Q.read(REAL)
        check("能读取", res.get("exists") is True, str(res.get("note")))
        check("零问题判定为通过", res.get("ok") is True and res.get("count") == 0,
              f"count={res.get('count')} blocking={res.get('blocking')}")

    # 写一个带缺陷的样本，验证统计与阻断判定
    from hc_agent.io.schema import IssueCollector
    fake_run = tmp / "run_quality"
    fake_run.mkdir(parents=True, exist_ok=True)
    col = IssueCollector()
    col.add(file="loads.csv", sheet="Sheet1", row=3, field="kw",
            object_id="LD01", code="SCHEMA_TYPE_MISMATCH",
            message="kw 应为数值", severity="error", blocking=True)
    col.add(file="profiles.csv", field="timestamp", code="TIME_GAP",
            message="存在时间缺口", severity="warning", blocking=False)
    Q.write(fake_run, col)
    rq = Q.read(fake_run)
    check("写出后可读", rq.get("exists") is True)
    check("统计条数正确", rq.get("count") == 2, str(rq.get("count")))
    check("识别出 1 项阻断", rq.get("blocking") == 1, str(rq.get("blocking")))
    check("有阻断则判定不通过", rq.get("ok") is False)
    check("按严重度分组", rq.get("by_severity", {}).get("error") == 1,
          str(rq.get("by_severity")))
    ftxt = Q.format_report(rq)
    check("文本标出未通过", "未通过" in ftxt, ftxt[:60])
    check("文本列出阻断项", "🚫" in ftxt)
    # 没有文件 → 必须说"没查过"而不是"通过"
    empty = tmp / "run_no_quality"
    empty.mkdir(parents=True, exist_ok=True)
    rn = Q.read(empty)
    check("缺文件时 exists=false", rn.get("exists") is False)
    check("缺文件时不判为通过", rn.get("ok") is False)
    check("缺文件时说明是没查过", "没查过" in str(rn.get("note")),
          str(rn.get("note")))

    # ============================================================ 4. 验收矩阵
    print("\n4) 验收指标矩阵（九条，四态）")
    from hc_agent import acceptance as A

    check("定义了 9 条指标", len(A.MATRIX) == 9, str(len(A.MATRIX)))
    check("四态齐全",
          set(A.STATUS_LABEL) == {"pass", "fail", "unknown", "unavailable"},
          str(sorted(A.STATUS_LABEL)))

    if REAL is not None:
        acc = A.single_run(REAL)
        items = {x["key"]: x for x in acc["items"]}
        check("九条都算了", len(items) == 9, str(len(items)))
        check("门槛来自定义（不是空）",
              all(items[k].get("target") for k in items),
              str([items[k].get("target") for k in items][:3]))
        # 已验证的事实
        check("评估覆盖 5 类约束",
              items["constraint_coverage"]["value"] == 5,
              str(items["constraint_coverage"]))
        check("评估覆盖判定达标", items["constraint_coverage"]["status"] == "pass")
        check("独立复算判定达标",
              items["verification_pass"]["status"] == "pass",
              str(items["verification_pass"]))
        check("数据保护判定达标",
              items["data_protection"]["status"] == "pass",
              str(items["data_protection"]))
        check("可追溯判定达标",
              items["traceability"]["status"] == "pass",
              str(items["traceability"]))
        # 接入 pandapower 之后引擎数是 2，这一项应当达标；
        # 若引擎掉到 1 个，这里会失败并打印明细，不会静默通过。
        check("适配引擎数按注册表计（接入 pandapower 后为 2）",
              items["engine_count"]["value"] == 2,
              str(items["engine_count"]))
        check("适配引擎数判为达标",
              items["engine_count"]["status"] == "pass",
              str(items["engine_count"]))
        check("需要专家标签的项标为不适用",
              items["level_agreement"]["status"] == "unavailable",
              str(items["level_agreement"]))
        check("不适用项给出原因",
              "专家" in str(items["level_agreement"].get("detail")),
              str(items["level_agreement"].get("detail")))
        # 关键：无数据不得冒充达标
        check("无数据项不会被记为达标",
              all(x["status"] != "pass" for x in acc["items"]
                  if x.get("value") is None),
              str([(x["key"], x["status"]) for x in acc["items"]
                   if x.get("value") is None]))
        mtxt = A.format_matrix(acc)
        for kw in ("汇总", "门槛"):
            check(f"文本含「{kw}」", kw in mtxt)

    # 缺产物的运行
    bare = tmp / "run_bare"
    (bare / "results").mkdir(parents=True, exist_ok=True)
    (bare / "results" / "run_summary.json").write_text("{}", encoding="utf-8")
    ab = A.single_run(bare)
    check("缺产物时九条仍输出", len(ab["items"]) == 9, str(len(ab["items"])))
    # 「适配引擎数」是**产品级**能力（问的是注册表里有几个引擎能算），
    # 不依赖某一次运行的产物，所以它可以在空运行上达标；
    # 其余八条都从产物取数，缺产物时必须不是 pass。
    _run_derived = [x for x in ab["items"] if x["key"] != "engine_count"]
    check("缺产物时运行类指标不判出达标",
          sum(1 for x in _run_derived if x["status"] == "pass") == 0,
          str([(x["key"], x["status"]) for x in _run_derived]))

    # ============================================================ 5. 可观测性
    print("\n5) 可观测性汇总")
    dirs = [d for d in runs_mod.list_run_dirs()][:20]
    ov = A.overview(dirs)
    check("给出总数", ov["total"] == len(dirs), str(ov["total"]))
    check("给出成功率",
          isinstance(ov.get("success_rate"), (int, float)),
          str(ov.get("success_rate")))
    check("给出等级分布", isinstance(ov.get("level_dist"), dict),
          str(ov.get("level_dist")))
    check("给出规则版本分布", isinstance(ov.get("rule_dist"), dict),
          str(ov.get("rule_dist")))
    check("统计审计链异常数", "chain_broken" in ov, str(ov))
    check("统计不收敛运行数", "convergence_fail" in ov)
    check("统计 unknown 约束项", "unknown_constraints" in ov)
    one = A.overview([])
    check("空列表不崩", one["total"] == 0 and one["success_rate"] == 0.0,
          str(one))

    # ============================================================ 6. 技能契约
    print("\n6) 技能契约与问答接线")
    from hc_agent.policy import PermissionLevel, Policy
    from hc_agent.skill_runtime import SkillRuntime, default_registry

    reg = default_registry()
    check("技能契约增加到 5 个", len(reg.contracts) == 5,
          str(sorted(reg.contracts)))
    for cid in ("HC.CALIBER.BINDING", "HC.ACCEPTANCE.MATRIX"):
        check(f"契约 {cid} 在", cid in reg.contracts)
    check("两个新技能都是只读",
          reg.get("HC.CALIBER.BINDING").permission == "read_only"
          and reg.get("HC.ACCEPTANCE.MATRIX").permission == "read_only")

    rt = SkillRuntime(reg, Policy(level=PermissionLevel.READ_ONLY))
    if REAL is not None:
        r1 = rt.execute("verify_caliber_binding", run_dir=str(REAL))
        check("L0 可执行口径校验", r1.get("ok"), str(r1))
        r2 = rt.execute("evaluate_acceptance", run_dir=str(REAL))
        check("L0 可执行验收矩阵", r2.get("ok"), str(r2))
        check("验收返回 9 项",
              len((r2.get("result") or {}).get("items") or []) == 9)

    # 问答
    resp = Responder(Facts())
    for q, want in (("口径还有效吗", "caliber_check"),
                    ("这次结论的口径绑定变没变", "caliber_check"),
                    ("数据质量怎么样", "quality_check"),
                    ("验收指标怎么样", "acceptance")):
        got = intent(q).name
        check(f"意图 {q[:14]}… → {want}", got == want, f"实际 {got}")
    for q, expect in (("算一下桌面上的算例", "dss_analyze"),
                      ("还能接多少光伏", "capacity"),
                      ("口径是什么", "caliber"),
                      ("FD002 复算通过了吗", "verification")):
        got = intent(q).name
        check(f"「{q}」仍是 {expect}", got == expect, f"实际 {got}")

    if REAL is not None:
        a1 = resp.answer("FD002 口径还有效吗")
        check("口径回答给出结论", ("有效" in a1.text or "失效" in a1.text),
              a1.text[:60])
        a2 = resp.answer("FD002 数据质量怎么样")
        check("质量回答给出判定", ("通过" in a2.text or "未通过" in a2.text),
              a2.text[:60])
        a3 = resp.answer("FD002 验收指标怎么样")
        check("验收回答含九条之一", "评估覆盖" in a3.text, a3.text[:60])

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
