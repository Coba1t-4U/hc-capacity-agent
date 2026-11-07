# -*- coding: utf-8 -*-
"""把框架版报告改成用户给定模板的结构（1–7 章 + 术语 + 两张表），数据只填真实的。"""
from pathlib import Path
p = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project\tools\build_framework_report.py")
s = p.read_text(encoding="utf-8")

# 章标题改成模板叫法
ren = [
    ('h(d, "2 基础数据与输入条件（智能体感知输入集）")', 'h(d, "2 基础数据收集与校验")'),
    ('h(d, "3 承载力智能体评估方法与流程")', 'h(d, "3 评估方法与流程")'),
    ('h(d, "5 承载力评估结果")', 'h(d, "5 承载力等级划分与可开放容量")'),
    ('h(d, "6 结果讨论与有效性校验")', 'h(d, "6 问题诊断与提升措施建议")'),
    ('h(d, "7 承载力提升对策与实施建议")', 'h(d, "7 结论与附录")'),
    ('h(d, "8 结论与展望")', 'h(d, "8 讨论与展望")'),
]
for a, b in ren:
    if a in s:
        s = s.replace(a, b, 1)

# 1.3 术语定义（在 1.4 之前）
anchor = '    h(d, "1.4 引用标准与基础文件", 2)'
block = '''    h(d, "1.3 术语定义", 2)
    par(d, "系统级承载力：调度独立控制区域范围内，配电网能够消纳的分布式电源最大装机规模。")
    par(d, "设备级承载力：单台变压器、单条线路等单个设备允许接入的分布式电源最大容量。")
    par(d, "可开放容量：不突破设备运行限值、不影响电能质量前提下，剩余可接入容量空间。")
    par(d, "红黄绿分区：按反向负载率 λ 对供电区域划分的管控等级（红 >100%、黄 80–100%、绿 0–80%）。")

'''
assert anchor in s
s = s.replace(anchor, block + anchor, 1)

# 第 5 章加分级汇总表（单算例，如实注明）
anchor5 = '    h(d, "6 问题诊断与提升措施建议")'
tbl5 = '''    par(d, "分级汇总（本表为单算例结果，非全市域汇总）：")
    tbl(d, [["区域等级", "对象数量", "可接入新增容量", "管控要求"],
            [LVL, "1", f"物理 {PHYS} kW", "按主导约束整改后放开"]], [80, 70, 130, 160])
    par(d, "说明：多对象分级汇总需逐级评估后生成，本版不具备；不在此处填估算值。", color=RED)

'''
assert anchor5 in s
s = s.replace(anchor5, tbl5 + anchor5, 1)
p.write_text(s, encoding="utf-8")
print("已按模板调整结构")
