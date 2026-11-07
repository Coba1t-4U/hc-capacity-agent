# -*- coding: utf-8 -*-
"""把「算例计算」并入框架版报告：新增第 4 章，原 4–7 章顺延为 5–8。"""

from pathlib import Path
import re

p = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project\tools\build_framework_report.py")
s = p.read_text(encoding="utf-8")
if "算例计算" in s:
    print("已包含算例章，跳过")
    raise SystemExit

# 1) 原来的 4–7 章顺延为 5–8（先大后小，避免连锁）
for n in (7, 6, 5, 4):
    s = s.replace(f'h(d, "{n} ', f'h(d, "{n + 1} ')
    s = s.replace(f'h(d, "{n}.', f'h(d, "{n + 1}.')
    s = s.replace(f'par(d, f"{n}.', f'par(d, f"{n + 1}.')
    s = s.replace(f'par(d, "{n}.', f'par(d, "{n + 1}.')

# 2) 读三个算例，做对照
helper = '''
def run_summary(name):
    return j(ROOT / "runs" / "ACCEPT" / name / "results" / "run_summary.json")


def case_line(name):
    x = run_summary(name)
    if not x:
        return f"{name}：无运行产物"
    bd = x.get("binding_detail") or {}
    return (f"{name}：物理 {x.get('capacity_physical_kw')} kW / 管理 "
            f"{x.get('capacity_managed_kw')} kW；等级 "
            f"{LV.get(str(x.get('level')), x.get('level') or '—')}；瓶颈 "
            f"{x.get('binding_constraint') or '—'}"
            + (f" @ {bd.get('object_id')}（{bd.get('metric')}={bd.get('value')}）"
               if bd else ""))


'''
s = s.replace("def main():", helper + "def main():", 1)

# 3) 在「5 承载力评估结果」前插入第 4 章
anchor = '    h(d, "5 承载力评估结果")'
block = '''    h(d, "4 算例计算与分析")
    h(d, "4.1 主算例概况", 2)
    par(d, f"算例 FD002：5 母线 / 3 线路 / 1 配变 / 2 负荷 / 2 组光伏；"
           f"场景 {S.get('scenario_count') or '—'} 个、时间片 {S.get('slice_count') or '—'} 个、"
           f"约束 {CS.get('total', 0)} 条。输入包按字节冻结，快照 {M.get('snapshot_id') or '—'}。")
    h(d, "4.2 计算设置", 2)
    par(d, "模型：OpenDSS 三相不平衡潮流（主算）；pandapower 正序平衡（交叉校核）。"
           "搜索：0–3000 kW、精度 10 kW、最多 25 次迭代、取最不利相与最不利断面。")
    h(d, "4.3 计算结果", 2)
    par(d, f"基线：{CS.get('total', 0)} 条约束 pass {CS.get('pass', 0)} / "
           f"fail {CS.get('fail', 0)} / unknown {CS.get('unknown', 0)}。")
    par(d, f"容量结论：物理 {PHYS} kW、管理 {MGD} kW、等级 {LVL}；"
           f"瓶颈 {BIND}" + (f" @ {BD.get('object_id')}" if BD else "") + "。")
    if BD:
        par(d, f"瓶颈数值：{BD.get('metric')} = {BD.get('value')}，"
               f"限值 [{BD.get('limit_lower')}, {BD.get('limit_upper')}]，"
               f"裕度 {BD.get('margin')}。")
    par(d, "复算：换全新目录与进程重跑，逐项比对容量、绑定约束、母线电压与网损；"
           "结论见附录 D。")
    h(d, "4.4 对照算例", 2)
    for _n in ("FD002", "FD001", "FD016"):
        par(d, "· " + case_line(_n))
    par(d, "三者用途不同：FD002 验证正常可估与瓶颈定位；FD001 验证基线越限时给 0 而不给乐观值；"
           "FD016 验证多场景批量与最不利断面取法。")

'''
assert anchor in s
s = s.replace(anchor, block + anchor, 1)
p.write_text(s, encoding="utf-8")
print("已插入第 4 章「算例计算与分析」，原 4–7 章顺延为 5–8")
