# -*- coding: utf-8 -*-
"""把承载力结果报告扩成 12 章，并插入界面与问答。

改四处：
  1. reporting.py  REPORT_SECTIONS 9 → 12，docx 正文加三章并重排编号
  2. pages.py      CHAPTERS 9 → 12（界面「结果与证据 → 报告章节」）
  3. qa.py         新增 chapter_summary / chapter_grade 两个章节意图
  4. tests/verify_report_chapters.py  断言 9 → 12（两处）

新增三章：1 结论摘要、6 分级与建议、7 计算证据。
原「接入校核」并入 5 评估结果，作为 5.1，避免章节数对不上。
"""

from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")

# ============================================================ 1) reporting.py
rp = ROOT / "hc_agent" / "reporting.py"
s = rp.read_text(encoding="utf-8")

OLD_SECTIONS = s[s.index("REPORT_SECTIONS = ("):s.index(")\n\n\ndef build_report_context")]
NEW_SECTIONS = '''REPORT_SECTIONS = (
    "1. 结论摘要：等级、物理/管理双口径容量、主导限制因素、突出发现与需关注项",
    "2. 任务概况：场景、范围、时间窗、发起与复核信息",
    "3. 数据与口径：快照、质量、规则、假设、模型/技能/引擎版本",
    "4. 计算过程：场景、方法、收敛、约束校验和开放容量搜索记录",
    "5. 评估结果：可开放容量、红黄绿等级、主导限制因素与接入校核",
    "6. 分级与建议：分级结论、主导约束与按约束类型的整改方向",
    "7. 计算证据：复现所需的引擎、快照、规则包与输入指纹",
    "8. 风险与缺口：数据、模型、算法、场景和未解决问题",
    "9. 独立验证：复算任务、输入一致性、差异和通过状态",
    "10. 人工审批：意见、签署、有效期和发布范围",
    "11. 证据索引：各类文件哈希、日志、结果和审计事件编号",
    "12. 附录与附表：约束明细、业务流覆盖、术语与口径说明"
'''
s = s.replace(OLD_SECTIONS, NEW_SECTIONS, 1)

# ---- 1.1 在「任务概况」前插入「结论摘要」，并把任务概况改第 2 章
ANCHOR_TASK = '    h("1 任务概况")'
SUMMARY_BLOCK = '''    h("1 结论摘要")
    _zh = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}
    _lvl = _zh.get(str(s.get("level")), str(s.get("level") or "—"))
    _cs0 = s.get("constraint_summary") or {}
    kv("一句话结论",
       f"本次评估等级为{_lvl}，物理可开放容量 {_fmt(s.get('capacity_physical_kw'))} kW，"
       f"主导限制因素为 {s.get('binding_constraint') or '—'}")
    kv("可开放容量 (kW)",
       f"物理 {_fmt(s.get('capacity_physical_kw'))} / 管理 {_fmt(s.get('capacity_managed_kw'))}")
    kv("红黄绿等级", _lvl)
    kv("主导限制因素", s.get("binding_constraint") or "—")
    kv("基线约束校核",
       f"共 {_cs0.get('total', 0)} 项：pass {_cs0.get('pass', 0)}、"
       f"fail {_cs0.get('fail', 0)}、unknown {_cs0.get('unknown', 0)}")
    _bs0 = _cs0.get("binding_constraints") or []
    if _bs0:
        _b0 = _bs0[0]
        kv("突出发现",
           f"{as_str(_b0.get('constraint_id'))} 在 {as_str(_b0.get('object_id'))} 上 "
           f"{as_str(_b0.get('metric'))}={_fmt(_b0.get('value'))}，"
           f"限值 [{_fmt(_b0.get('limit_lower'))}, {_fmt(_b0.get('limit_upper'))}]，"
           f"裕度 {_fmt(_b0.get('margin'))}")
    kv("需关注项",
       "结论可用于内部评估与答复支撑；对外发布的区域等级须按省公司口径另行生成。")

'''
assert ANCHOR_TASK in s
s = s.replace(ANCHOR_TASK, SUMMARY_BLOCK + '    h("2 任务概况")', 1)

# ---- 1.2 其余章节重排编号
renum = [
    ('h("2 数据与口径")', 'h("3 数据与口径")'),
    ('h("3 计算过程")', 'h("4 计算过程")'),
    ('h("3.1 本次求解口径声明")', 'h("4.1 本次求解口径声明")'),
    ('h("4 评估结果")', 'h("5 评估结果")'),
    ('h("5 接入校核")', 'h("5.1 接入校核")'),
    ('h("6 风险与缺口")', 'h("8 风险与缺口")'),
    ('h("7 独立验证")', 'h("9 独立验证")'),
    ('h("8 人工审批")', 'h("10 人工审批")'),
    ('h("9 证据索引")', 'h("11 证据索引")'),
    ('h("附录 A 业务流覆盖（八段闭环）")', 'h("12 附录与附表：业务流覆盖（八段闭环）")'),
]
for a, b in renum:
    assert a in s, a
    s = s.replace(a, b, 1)

# ---- 1.3 在「8 风险与缺口」前插入第 6、7 章
ANCHOR_RISK = '    h("8 风险与缺口")'
NEW_BLOCKS = '''    h("6 分级与建议")
    kv("等级", _lvl)
    kv("分级依据", s.get("level_reason") or "—")
    kv("主导约束", s.get("binding_constraint") or "—")
    kv("整改方向",
       "按主导约束对症：电压越限优先调压（无功补偿或有载调压配变）；"
       "热稳越限优先增容或负荷分流；反送越限配置储能或采用出力限制策略；"
       "不平衡越限调整接入相别；短路越限校核开关遮断能力并限制单点接入容量。")
    kv("使用说明",
       "上述建议指向约束类型，具体方案的容量与投资需另行测算；"
       "本报告不对未实施改造的效果作任何承诺。")

    h("7 计算证据")
    _tr = ctx.get("traceability") or {}
    _lk = _tr.get("links") or {}
    kv("复现三要素", "同一份输入快照 + 同一版规则包 + 同一版引擎")
    for _k, _label in (("snapshot_id", "输入快照"),
                       ("rule_package", "规则包"),
                       ("input_digest", "输入指纹")):
        if _lk.get(_k):
            kv(_label, _fmt(_lk.get(_k)))
    _fp = _lk.get("engine_fingerprint") or {}
    if _fp:
        kv("引擎", f"{_fp.get('engine_id', '')} / {_fp.get('interface', '')} "
                   f"{_fp.get('interface_version', '')}")
    kv("可追溯性", "完整（关键链接齐全）" if _tr.get("complete")
       else "不完整，缺少：" + "、".join(_tr.get("missing") or []))

'''
assert ANCHOR_RISK in s
s = s.replace(ANCHOR_RISK, NEW_BLOCKS + ANCHOR_RISK, 1)
rp.write_text(s, encoding="utf-8")
print("reporting.py 已扩为 12 章")

# ============================================================ 2) pages.py
pg = ROOT / "hc_agent" / "desktop" / "pages.py"
t = pg.read_text(encoding="utf-8")
i = t.index("    CHAPTERS = (")
j = t.index("      )", i) + len("      )")
NEW_CHAPTERS = '''    CHAPTERS = (
        (1, "结论摘要", "现在能接多少？结论是什么？",
         "等级、物理/管理双口径容量、主导限制因素、突出发现与需关注项"),
        (2, "任务概况", "任务概况是什么？",
         "场景、范围、时间窗、发起与复核信息"),
        (3, "数据与口径", "用了哪版规则？数据与口径是什么？",
         "快照、质量、规则、假设、模型/技能/引擎版本"),
        (4, "计算过程", "计算过程怎么样？",
         "场景、方法、收敛、约束校验与搜索记录"),
        (5, "评估结果", "还能接多少？为什么是这个值？",
         "可开放容量、红黄绿等级、主导限制因素明细、接入校核"),
        (6, "分级与建议", "这个等级意味着什么？该怎么改？",
         "分级依据、主导约束、按约束类型的整改方向"),
        (7, "计算证据", "这个数从哪来？能复现吗？",
         "引擎版本、输入快照、规则包、输入指纹与可追溯性"),
        (8, "风险与缺口", "有什么风险？",
         "数据、模型、算法、场景与未解决问题"),
        (9, "独立验证", "复算通过了吗？",
         "复算任务、输入一致性、差异与通过状态"),
        (10, "人工审批", "发布门禁过了吗？",
         "意见、签署、有效期与发布范围"),
        (11, "证据索引", "证据索引在哪？",
         "文件哈希、日志、结果与审计事件编号"),
        (12, "附录与附表", "完整明细在哪？",
         "约束明细、业务流覆盖、术语与口径说明"),
      )'''
t = t[:i] + NEW_CHAPTERS + t[j:]
pg.write_text(t, encoding="utf-8")
print("pages.py CHAPTERS 已改为 12 章")

# ============================================================ 3) qa.py
qa = ROOT / "hc_agent" / "qa.py"
q = qa.read_text(encoding="utf-8")

RULE_ANCHOR = '    (r"(任务概况|任务的基本情况|'
if '"chapter_summary"' not in q:
    new_rules = ('    (r"(结论摘要|报告摘要|一句话结论|摘要是什么|摘要)", "chapter_summary"),\n'
                 '    (r"(分级与建议|一台一策|红区.{0,6}建议|按等级.{0,6}建议|该怎么改)", "chapter_grade"),\n')
    assert RULE_ANCHOR in q
    q = q.replace(RULE_ANCHOR, new_rules + RULE_ANCHOR, 1)

HANDLER_ANCHOR = "    def _a_chapter_data(self, it: Intent, r: RunFacts) -> Answer:"
if "_a_chapter_summary" not in q:
    handlers = '''    def _a_chapter_summary(self, it: Intent, r: RunFacts) -> Answer:
        """报告第 1 章：结论摘要（只看决策者要立刻知道的信息）。"""
        if not r.ok:
            return self._no_run()
        sm = r.summary
        zh = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}
        lvl = zh.get(str(sm.get("level")), str(sm.get("level") or "—"))
        lines = self._chapter_head(r, "结论摘要", 1)
        lines.append(f"  · 一句话结论：等级 **{lvl}**，物理可开放容量 "
                     f"{sm.get('capacity_physical_kw')} kW，主导限制 "
                     f"{sm.get('binding_constraint') or '—'}")
        lines.append(f"  · 可开放容量：物理 {sm.get('capacity_physical_kw')} kW / "
                     f"管理 {sm.get('capacity_managed_kw')} kW")
        cs = sm.get("constraint_summary") or {}
        lines.append(f"  · 基线约束：共 {cs.get('total', 0)} 项，"
                     f"fail {cs.get('fail', 0)}、unknown {cs.get('unknown', 0)}")
        binding = (cs.get("binding_constraints") or [])
        if binding:
            b = binding[0]
            lines.append(f"  · 突出发现：{b.get('constraint_id')} 在 {b.get('object_id')} 上 "
                         f"{b.get('metric')}={b.get('value')}，裕度 {b.get('margin')}")
        lines.append("")
        lines.append("需关注项：结论可用于内部评估与答复支撑；"
                     "对外发布的区域等级须按省公司口径另行生成。")
        return Answer("\\n".join(lines), source=self._src(r, "run_summary.json"))

    def _a_chapter_grade(self, it: Intent, r: RunFacts) -> Answer:
        """报告第 6 章：分级与建议（建议挂在主导约束上）。"""
        if not r.ok:
            return self._no_run()
        sm = r.summary
        zh = {"green": "绿", "yellow": "黄", "red": "红", "grey": "灰"}
        lvl = zh.get(str(sm.get("level")), str(sm.get("level") or "—"))
        lines = self._chapter_head(r, "分级与建议", 6)
        lines.append(f"  · 等级：**{lvl}**")
        lines.append(f"  · 分级依据：{sm.get('level_reason') or '—'}")
        lines.append(f"  · 主导约束：{sm.get('binding_constraint') or '—'}")
        lines.append("")
        lines.append("整改方向（按约束对症，具体方案需另行测算）：")
        lines.append("  · 电压越限 → 无功补偿或有载调压配变（调压优先于增容）")
        lines.append("  · 热稳越限 → 增容或负荷分流")
        lines.append("  · 反送越限 → 配置储能或采用出力限制策略")
        lines.append("  · 不平衡越限 → 调整接入相别")
        lines.append("  · 短路越限 → 校核开关遮断能力，限制单点接入容量")
        return Answer("\\n".join(lines), source=self._src(r, "run_summary.json"))

'''
    assert HANDLER_ANCHOR in q
    q = q.replace(HANDLER_ANCHOR, handlers + HANDLER_ANCHOR, 1)
qa.write_text(q, encoding="utf-8")
print("qa.py 已加两个章节意图与处理")

# ============================================================ 4) tests
tp = ROOT / "tests" / "verify_report_chapters.py"
u = tp.read_text(encoding="utf-8")
u = u.replace('check("报告清单有 9 章", len(sections) == 9', 'check("报告清单有 12 章", len(sections) == 12')
u = u.replace('check("界面章节表也是 9 章",', 'check("界面章节表也是 12 章",')
u = u.replace('"CHAPTERS", ())) == 9)', '"CHAPTERS", ())) == 12)')
u = u.replace("报告 9 章", "报告 12 章")
tp.write_text(u, encoding="utf-8")
print("测试断言已改为 12 章")
