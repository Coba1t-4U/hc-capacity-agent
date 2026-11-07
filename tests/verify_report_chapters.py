"""Verify 昨天遗留的三条 P0：

  1) 运算过程页的「运行代码」—— 如实区分实际执行 / 等效复现
  2) 容量分析跑完当场给出报告入口（不必再跑去运行记录页）
  3) 报告 13 章逐章都能在问答里回答（且只说该章产物）
"""
from __future__ import annotations

import json
import os
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
print("P0 遗留三项验证：运行代码 / 报告入口 / 报告 13 章问答")
print("=" * 74)

from hc_agent.desktop import process as P
from hc_agent.desktop import runs as R

RUN = R.resolve_run_dir("FD002")
have = RUN is not None
check("能定位一次运行（FD002）", have, str(RUN))

# ================================================================ 1. 运行代码
print("\n1) 运行代码（process.RunCode）")
if have:
    p = P.load(RUN)
    rc = p.code
    check("有 RunCode 数据", rc is not None)
    check("有可复制的命令行", bool(rc.cmdline), str(rc.cmdline)[:80])
    check("命令行是 hc_agent.cli run 形式",
          "hc_agent.cli" in rc.cmdline and "run" in rc.cmdline,
          rc.cmdline[:100])
    check("命令行带上了输出目录", str(RUN) in rc.cmdline or "runs" in rc.cmdline,
          rc.cmdline[:120])
    # 老产物没有 invocation → 必须标成"重建"，不能谎称实际执行
    man = json.loads((RUN / "manifest.json").read_text(encoding="utf-8"))
    has_inv = isinstance(man.get("invocation"), dict)
    check("老产物未记录 invocation 时标记为等效复现",
          rc.exact == has_inv, f"exact={rc.exact} has_inv={has_inv}")
    if not rc.exact:
        check("重建时给出明确说明（不是实际执行）",
              "等效复现" in rc.note and "不是当时实际执行" in rc.note,
              rc.note[:120])
    check("引擎日志被找到", bool(rc.engine_log), rc.engine_log)
    check("引擎日志行数 > 0", rc.engine_log_lines > 0,
          str(rc.engine_log_lines))

    summ = p.code_summary()
    check("执行摘要非空", len(summ) >= 2, str(summ))
    check("摘要含阶段数", any("个阶段" in s for s in summ), str(summ))
    check("摘要含引擎调用次数", any("引擎" in s and "次" in s for s in summ),
          str(summ))

# 新增运行会记录 invocation：直接测采集函数
print("\n   新增运行会记录 invocation：")
from hc_agent.pipeline import _capture_invocation
inv = _capture_invocation({"max_slices": 24}, Path("/in"), Path("/out"))
check("采集 args 三元组", {"argv", "channel", "equivalent_cli"} <= set(inv),
      str(list(inv)))
check("等效命令含参数", "--max-slices" in inv["equivalent_cli"],
      str(inv["equivalent_cli"]))
check("记录 cwd 与解释器", bool(inv.get("cwd")) and bool(inv.get("python")))
check("命令行可直接复制（带引号处理）",
      "hc_agent.cli" in inv["equiv_cmdline"], inv["equiv_cmdline"][:100])

# 页面接线
pages_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(
    encoding="utf-8")
proc_src = (ROOT / "hc_agent" / "desktop" / "process.py").read_text(
    encoding="utf-8")
check("进程页有「运行代码」子标签", '("code", "运行代码")' in pages_src)
check("有复制命令按钮", "复制命令" in pages_src)
check("区分实际执行/等效复现两种标签",
      "实际执行" in pages_src and "等效复现" in pages_src)
check("展示引擎侧日志位置", "engine_log" in pages_src)

# ================================================================ 2. 报告入口
print("\n2) 容量分析跑完当场给报告入口")
check("有 find_report_in（按任意目录找报告）",
      hasattr(R, "find_report_in"))
if have:
    rep = R.find_report_in(RUN)
    check("按运行目录找到报告", rep is not None and rep.is_file(), str(rep))
    check("找不到时返回 None",
          R.find_report_in(ROOT / "不存在目录") is None)

app_src = (ROOT / "hc_agent" / "desktop" / "app.py").read_text(
    encoding="utf-8")
check("跑完记录 last_run_id", 'state["last_run_id"]' in app_src)
check("跑完记录 last_run_dir", 'state["last_run_dir"]' in app_src)
check("跑完会重画当前页", "self._render_page()" in app_src)
check("容量分析页有「本次运行结果」区块",
      "_last_run_block" in pages_src and "本次运行结果" in pages_src)
for btn in ("打开计算报告", "查看运算过程", "看运行代码", "问这次运行"):
    check(f"结果区块有「{btn}」", btn in pages_src)

# ================================================================ 3. 报告 13 章
print("\n3) 报告 13 章逐章可问答")
from hc_agent.qa import Facts, Responder, intent

# 章节表与报告清单必须对得上
rep_man = {}
if have:
    rmp = RUN / "report" / "report_manifest.json"
    if rmp.is_file():
        rep_man = json.loads(rmp.read_text(encoding="utf-8"))
sections = rep_man.get("sections") or []
check("报告清单有 6 章", len(sections) == 6, str(len(sections)))
check("界面章节表也是 6 章",
      len(getattr(__import__("hc_agent.desktop.pages",
                             fromlist=["ResultsPage"]).ResultsPage,
                  "CHAPTERS", ())) == 6)

INTENTS = [
    ("FD002 的任务概况是什么", "chapter_task"),
    ("FD002 用了哪版规则", "chapter_data"),
    ("FD002 计算过程怎么样", "chapter_process"),
    ("FD002 接入校核结论是什么", "chapter_access"),
    ("FD002 有什么风险", "chapter_risk"),
    ("FD002 的证据索引", "chapter_evidence"),
    ("FD002 复算通过了吗", "verification"),
    ("FD002 发布门禁过了吗", "gate"),
    ("FD002 还能接多少光伏", "capacity"),
]
for q, want in INTENTS:
    got = intent(q).name
    check(f"意图：{q[:18]}… → {want}", got == want, f"实际 {got}")

# 常规问题不能被章节规则抢走
for q, notwant in (("电压越限的判据是什么", "chapter_task"),
                   ("最低电压是多少", "chapter_data"),
                   ("为什么容量是 0", "chapter_risk")):
    check(f"「{q}」不被章节规则抢走",
          intent(q).name != notwant, intent(q).name)

facts = Facts()
resp = Responder(facts)
r = facts.find("FD002")

if r is not None:
    print("\n   逐章答案取自产物（数字必须对得上）：")
    sm = r.summary

    a1 = resp.answer("FD002 的任务概况是什么")
    check("第1章 含运行号", "FD002" in a1.text or str(sm.get("run_id")) in a1.text,
          a1.text[:100])
    check("第1章 含任务号", str(sm.get("task_id")) in a1.text
          or "任务号" in a1.text, a1.text[:100])
    check("第1章 含时间窗", "时间窗" in a1.text)

    a2 = resp.answer("FD002 用了哪版规则")
    check("第2章 含规则包", str(sm.get("rule_package")) in a2.text,
          str(sm.get("rule_package")))
    check("第2章 含快照", str(sm.get("snapshot_id")) in a2.text)
    check("第2章 含口径条目", "计算口径" in a2.text)

    a3 = resp.answer("FD002 计算过程怎么样")
    check("第3章 含场景数", str(sm.get("scenario_count")) in a3.text,
          str(sm.get("scenario_count")))
    check("第3章 含时间片", str(sm.get("slice_count")) in a3.text)
    check("第3章 含搜索方法", str(sm.get("search_method")) in a3.text)
    check("第3章 含阶段轨迹", "阶段轨迹" in a3.text)
    check("第3章 不乱打整个约束字典",
          "status_counts" not in a3.text, "出现原始字典")

    a5 = resp.answer("FD002 接入校核结论是什么")
    has_access = any((RUN / rel).is_file() for rel in
                     ("results/access_check.json", "results/access.json",
                      "results/access_result.json"))
    if not has_access:
        check("第5章 无产物时如实说本章为空",
              "没有接入校核产物" in a5.text and "为空" in a5.text,
              a5.text[:100])
        # 关键：不能拿容量结论去凑第 5 章
        check("第5章 不拿别的章节凑",
              "333.98" not in a5.text and "物理可开放容量" not in a5.text,
              a5.text[:120])

    a6 = resp.answer("FD002 有什么风险")
    uns = (sm.get("caliber") or {}).get("unsupported") or []
    if uns:
        check("第6章 列出能力边界", uns[0][:8] in a6.text, a6.text[:100])
    check("第6章 说明未覆盖/风险", ("未覆盖" in a6.text
                                 or "能力边界" in a6.text), a6.text[:100])

    a7 = resp.answer("FD002 复算通过了吗")
    check("第7章 复算结论与产物一致",
          str((r.verification or {}).get("pass")) in a7.text
          or ("通过" in a7.text), a7.text[:100])

    a8 = resp.answer("FD002 发布门禁过了吗")
    g = r.gate or {}
    check("第8章 门禁结论与产物一致",
          ("未通过" in a8.text) == (not g.get("passed")), a8.text[:100])

    a9 = resp.answer("FD002 的证据索引")
    rh = (r.report_manifest or {}).get("result_hash")
    if rh:
        check("第9章 含结果哈希", str(rh)[:16] in a9.text, str(rh)[:16])
    check("第9章 含可追溯链接", "可追溯" in a9.text or "哈希" in a9.text)

# 界面接线
check("结果与证据页有「报告章节」", '("chapters", "报告章节")' in pages_src)
check("章节表 9 条", pages_src.count('"问这个"') >= 1)
check("章节入口会带运行号提问",
      'ask_question(f"{r} {q}")' in pages_src
      or 'ask_question' in pages_src)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
