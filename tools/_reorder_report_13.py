# -*- coding: utf-8 -*-
"""按用户最终 9 部分格式，把报告重排为 13 章。

本轮只做「结构重排 + 已有数据可支撑的内容」，不新增算法：
  新增：6 制约因素分析（取自 constraint_summary 的 binding_constraints）
  改名：按 9 部分的叫法重命名各章
不做（下一轮）：第 5 章拆五维、第 13 章三张附表、敏感性分析、投资估算
"""

from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")

# ---------------------------------------------------------------- reporting.py
rp = ROOT / "hc_agent" / "reporting.py"
s = rp.read_text(encoding="utf-8")

i = s.index("REPORT_SECTIONS = (")
j = s.index(")\n\n\ndef build_report_context")
NEW_SECTIONS = '''REPORT_SECTIONS = (
    "1. 结论摘要：总体承载力、等级、双口径容量、首要制约因素与需关注项",
    "2. 基本信息与评估范围：评估对象、时间、数据截止、场景与水平年",
    "3. 基础数据与数据质量：网架、负荷、电源、来源与缺失处理",
    "4. 评估方法与约束条件：模型、五类约束、关键参数与假设",
    "5. 评估结果：可开放容量、等级、主导限制因素明细与接入校核",
    "6. 制约因素分析：首要制约因素、薄弱环节清单、敏感性分析",
    "7. 提升措施与建议：网架改造、技术手段、运行管理措施的优先级",
    "8. 智能体专项：计算证据与结论强度（复现三要素与可追溯性）",
    "9. 风险、不确定性与适用范围：数据/模型/场景缺口与结论有效期",
    "10. 独立验证：复算任务、输入一致性、差异和通过状态",
    "11. 人工审批：意见、签署、有效期和发布范围",
    "12. 证据索引：各类文件哈希、日志、结果和审计事件编号",
    "13. 附录与附表：参数表、标准依据、业务流覆盖与术语"
'''
s = s[:i] + NEW_SECTIONS + s[j:]

# ---- 新增第 6 章（插在「6 分级与建议」之前），并整体改名
ANCHOR6 = '    h("6 分级与建议")'
BLOCK6 = '''    h("6 制约因素分析")
    _csx = s.get("constraint_summary") or {}
    _bsx = _csx.get("binding_constraints") or []
    kv("基线约束统计",
       f"共 {_csx.get('total', 0)} 项：pass {_csx.get('pass', 0)}、"
       f"fail {_csx.get('fail', 0)}、unknown {_csx.get('unknown', 0)}")
    if _bsx:
        _first = _bsx[0]
        kv("首要制约因素",
           f"{as_str(_first.get('constraint_id'))} @ {as_str(_first.get('object_id'))}"
           f"（{as_str(_first.get('metric'))}={_fmt(_first.get('value'))}，"
           f"裕度 {_fmt(_first.get('margin'))}）")
        doc.add_paragraph("薄弱环节清单：")
        _t6 = doc.add_table(rows=1, cols=5)
        _t6.style = "Light Grid Accent 1"
        for _i, _n in enumerate(("约束", "对象", "指标", "计算值", "裕度")):
            _t6.rows[0].cells[_i].text = _n
        for _b in _bsx[:10]:
            _row = _t6.add_row().cells
            _row[0].text = as_str(_b.get("constraint_id"))
            _row[1].text = as_str(_b.get("object_id"))
            _row[2].text = as_str(_b.get("metric"))
            _row[3].text = _fmt(_b.get("value"))
            _row[4].text = _fmt(_b.get("margin"))
    else:
        kv("首要制约因素", "本次运行未记录绑定约束（基线无越限，或结果缺失）")
    kv("敏感性分析", "未实现：本版不做参数扰动扫描（负荷/出力±10% 逐次重算）。")
    kv("说明", "薄弱环节按约束类型与对象列出；全网设备级清单需逐级评估后汇总，"
               "本版以单算例为限。")

'''
assert ANCHOR6 in s
s = s.replace(ANCHOR6, BLOCK6 + '    h("7 提升措施与建议")', 1)

renum = [
    ('h("7 计算证据")', 'h("8 智能体专项：计算证据与结论强度")'),
    ('h("8 风险与缺口")', 'h("9 风险、不确定性与适用范围")'),
    ('h("9 独立验证")', 'h("10 独立验证")'),
    ('h("10 人工审批")', 'h("11 人工审批")'),
    ('h("11 证据索引")', 'h("12 证据索引")'),
    ('h("12 附录与附表：业务流覆盖（八段闭环）")',
     'h("13 附录与附表：业务流覆盖（八段闭环）")'),
    ('h("2 任务概况")', 'h("2 基本信息与评估范围")'),
    ('h("3 数据与口径")', 'h("3 基础数据与数据质量")'),
    ('h("4 计算过程")', 'h("4 评估方法与约束条件")'),
]
for a, b in renum:
    if a in s:
        s = s.replace(a, b, 1)
rp.write_text(s, encoding="utf-8")
print("reporting.py 已重排为 13 章")

# ---------------------------------------------------------------- pages.py
pg = ROOT / "hc_agent" / "desktop" / "pages.py"
t = pg.read_text(encoding="utf-8")
i = t.index("    CHAPTERS = (")
j = t.index("\n    )", i) + len("\n    )")
NEW_CH = '''    CHAPTERS = (
        (1, "结论摘要", "现在能接多少？结论是什么？",
         "总体承载力、等级、双口径容量、首要制约因素与需关注项"),
        (2, "基本信息与评估范围", "评估的是什么？", "对象、时间、数据截止、场景与水平年"),
        (3, "基础数据与数据质量", "数据可靠吗？", "网架、负荷、电源、来源与缺失处理"),
        (4, "评估方法与约束条件", "怎么算的？按什么判？", "模型、五类约束、关键参数与假设"),
        (5, "评估结果", "还能接多少？为什么是这个值？",
         "可开放容量、等级、主导限制因素明细、接入校核"),
        (6, "制约因素分析", "卡在哪？哪里最薄弱？", "首要制约因素、薄弱环节清单、敏感性分析"),
        (7, "提升措施与建议", "该怎么改？先改哪个？",
         "网架改造、技术手段、运行管理措施的优先级"),
        (8, "智能体专项：证据与结论强度", "这个数从哪来？能复现吗？",
         "复现三要素、结论强度、可追溯性"),
        (9, "风险、不确定性与适用范围", "有什么风险？能用到什么程度？",
         "数据/模型/场景缺口与结论有效期"),
        (10, "独立验证", "复算通过了吗？", "复算任务、输入一致性、差异与通过状态"),
        (11, "人工审批", "发布门禁过了吗？", "意见、签署、有效期与发布范围"),
        (12, "证据索引", "证据索引在哪？", "文件哈希、日志、结果与审计事件编号"),
        (13, "附录与附表", "完整明细在哪？", "参数表、标准依据、业务流覆盖与术语"),
    )'''
t = t[:i] + NEW_CH + t[j:]
pg.write_text(t, encoding="utf-8")
print("pages.py CHAPTERS 已改为 13 章")

# ---------------------------------------------------------------- tests
tp = ROOT / "tests" / "verify_report_chapters.py"
u = tp.read_text(encoding="utf-8")
u = u.replace("报告清单有 12 章", "报告清单有 13 章").replace("== 12", "== 13")
u = u.replace("界面章节表也是 12 章", "界面章节表也是 13 章").replace("() ) == 12", "() ) == 13")
u = u.replace(") == 12)", ") == 13)")
u = u.replace("报告 12 章", "报告 13 章")
tp.write_text(u, encoding="utf-8")
print("测试断言已改为 13 章")
