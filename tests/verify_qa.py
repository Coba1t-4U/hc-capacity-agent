"""Verify the Q&A agent (rule engine + native chat UI).

The point of this suite is honesty: every answer must trace to a real artifact,
and questions outside its competence must be refused rather than guessed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hc_agent.qa import (CONSTRAINT_ZH, Facts, Responder, SUGGESTIONS,
                         _extract_number, _extract_run_hint, intent)

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
print("智能体问答验证")
print("=" * 74)

facts = Facts()
print(f"\n读到 {len(facts.runs)} 次运行：" +
      "、".join(r.run_id for r in facts.runs))

# ---------------------------------------------------------------- 1. 意图识别
print("\n1) 意图识别")
CASES = [
    ("FD002 还能接多少光伏？", "capacity"),
    ("FD001 是什么情况？", "summary"),
    ("为什么接不了更多？", "why"),
    ("为什么只能接这么点", "why"),
    ("最低电压是多少？", "min_voltage"),
    ("哪些约束没通过？", "violations"),
    ("有没有违规", "violations"),
    ("复算通过了吗？", "verification"),
    ("发布门禁过了吗？", "gate"),
    ("能接 500 kW 吗？", "can_absorb"),
    ("还能接 300 千瓦吗", "can_absorb"),
    ("还有多少余量？", "headroom"),
    ("有哪些运行？", "list_runs"),
    ("有什么没覆盖？", "limits"),
    ("你好", "help"),
]
for q, want in CASES:
    got = intent(q).name
    check(f"「{q}」→ {want}", got == want, f"实际 {got}")

# ---------------------------------------------------------------- 2. 参数抽取
print("\n2) 参数抽取（编号与容量不能混）")
check("FD002 → FD002", _extract_run_hint("FD002 还能接多少") == "FD002",
      _extract_run_hint("FD002 还能接多少"))
check("APP001 → APP001", _extract_run_hint("APP001 怎么样") == "APP001")
check("保留前导零（不能变 FD2）",
      _extract_run_hint("FD002") != "FD2")
n, u = _extract_number("能接 500 kW 吗")
check("500 kW → 500", n == 500.0, str(n))
n2, _ = _extract_number("FD002 还能接多少")
check("FD002 里的 002 不能当成容量", n2 is None, str(n2))
n3, _ = _extract_number("能接 1.2 MW 吗")
check("1.2 MW → 1200 kW", n3 == 1200.0, str(n3))
n4, _ = _extract_number("3 个场景")
check("「3 个场景」不算容量", n4 is None, str(n4))

# ---------------------------------------------------------------- 3. 事实准确性
print("\n3) 答案必须与产物一致（不编造）")
resp = Responder(facts)
runs_by_id = {r.run_id: r for r in facts.runs}
has = len(runs_by_id) > 0
check("至少读到一次运行", has, "runs 为空")

if has:
    # 找一个有结论的运行
    target = next((r for r in facts.runs if r.ok), None)
    if target is not None:
        rid = target.run_id
        a = resp.answer(f"{rid} 能接多少")
        cap = target.cap_physical
        # 回答里的数字必须能在产物里找到
        nums = [float(x.replace(",", "")) for x in
                re.findall(r"\d[\d,]*\.?\d*", a.text)]
        # cap 可能为 None（该运行没有容量结论，例如只有基线越限）。
        # 之前没判空会 TypeError，把整份测试打挂（实测踩过）。
        if cap is None:
            check(f"{rid} 无容量结论时如实说明",
                  "没有" in a.text or "未获取" in a.text
                  or "不可" in a.text or "0" in a.text,
                  a.text[:80])
        else:
            check(f"{rid} 容量出现在回答里",
                  any(abs(x - cap) < 0.01 for x in nums), f"cap={cap}")
        check("回答带出处", "来源" in a.source or "runs/" in a.source,
              a.source)

        # FD001 这类受限算例：0 kW 必须解释成"基线越限"
        zero = next((r for r in facts.runs
                     if r.cap_physical is not None and r.cap_physical == 0), None)
        if zero is not None:
            a0 = resp.answer(f"{zero.run_id} 为什么接不了")
            check("0 kW 说明基线越限",
                  "基线" in a0.text and ("越限" in a0.text or "0" in a0.text),
                  a0.text[:120])

    # 越限数必须与 summary 完全一致
    withfail = next((r for r in facts.runs
                     if (r.summary.get("constraint_summary") or {}).get("fail")), None)
    if withfail is not None:
        cs = withfail.summary["constraint_summary"]
        av = resp.answer(f"{withfail.run_id} 哪些约束没通过")
        check("越限数与 summary 一致",
              str(cs["fail"]) in av.text, f"fail={cs['fail']}")

    # 「能不能接 X」的判定必须与会话一致
    tgt = next((r for r in facts.runs if r.ok and r.cap_physical), None)
    if tgt is not None:
        cap = tgt.cap_physical
        low = int(cap * 0.5)
        high = int(cap * 2)
        a_low = resp.answer(f"{tgt.run_id} 能接 {low} kW 吗")
        a_high = resp.answer(f"{tgt.run_id} 能接 {high} kW 吗")
        check(f"接 {low} kW（< {cap:.0f}）判为可以", "可以" in a_low.text
              and "不可以" not in a_low.text, a_low.text[:80])
        check(f"接 {high} kW（> {cap:.0f}）判为不可以", "不可以" in a_high.text,
              a_high.text[:80])

# ---------------------------------------------------------------- 4. 拒答
print("\n4) 不懂就问，不硬编（拒答能力）")
ua = resp.answer("帮我写一首关于母线的诗")
check("超范围问题不编答案", "没理解" in ua.text or "没读懂" in ua.text
      or "没听懂" in ua.text, ua.text[:80])
check("拒答时给出可问范围", any(s in ua.text for s in SUGGESTIONS[:3]))

# ---------------------------------------------------------------- 5. 无数据时
print("\n5) 无数据时不伪造")
empty = Facts.__new__(Facts)
empty.runs = []
empty.current = None
a_empty = Responder(empty).answer("能接多少")
check("无运行时不编数字", "没有任何运行结果" in a_empty.text
      or "没有可回答" in a_empty.text, a_empty.text[:80])
check("无运行时不出现 kW 数字",
      not re.search(r"\d+(\.\d+)?\s*kW", a_empty.text),
      a_empty.text[:80])

# ---------------------------------------------------------------- 6. 原生 UI
print("\n6) 原生对话界面（Tk，非网页）")
chat_src = (ROOT / "hc_agent" / "desktop" / "chat.py").read_text(encoding="utf-8")
pages_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(encoding="utf-8")
app_src = (ROOT / "hc_agent" / "desktop" / "app.py").read_text(encoding="utf-8")
for needle, why in (("<html", "HTML"), ("<div", "HTML 标签"),
                    ("WebView", "内嵌浏览器"), ("loadURL", "浏览器加载")):
    check(f"对话界面不含 {why}", needle not in chat_src)
check("问答页已注册", '"chat": ChatPage' in pages_src)
check("问答在导航里", '("chat", "问答")' in app_src)
# 计算已从 App 提到 ChatRuntime（这样切页/切会话都不会打断），
# 断言随之从 app.py 移到 chat_runtime.py
rt_src = (ROOT / "hc_agent" / "desktop" / "chat_runtime.py").read_text(
    encoding="utf-8")
check("回答在后台线程计算",
      "threading.Thread" in rt_src and "qa-" in rt_src)
check("提问交给运行时（不在界面线程里算）",
      "chat_runtime()" in app_src and "rt.submit" in app_src)
check("主线程轮询回投（Tk 非线程安全）",
      "def _drain" in chat_src and "_pending" in chat_src)
check("每条回答显示出处", "出处" in chat_src)
check("支持建议问题", "rebuild_chips" in chat_src)

# ---------------------------------------------------------------- 7. 渲染
print("\n7) 对话界面能构建出控件")
probe = """
import sys, os
sys.path.insert(0, r'%s')
os.environ.setdefault('HC_WATCHDOG_SUPERVISOR', '1')
from hc_agent.desktop.app import App
app = App()
app.withdraw()
app.show('chat')
app.update_idletasks()
v = getattr(app, 'chat_view', None)
n = 0
if v is not None:
    stack = [v]
    while stack:
        w = stack.pop(); n += 1
        stack.extend(w.winfo_children())
print('CHATWIDGETS', n)
# 直接塞一条回答（不经过线程），验证气泡渲染
from hc_agent.qa import Facts, Responder
ans = Responder(Facts()).answer('能接多少')
v.deliver(ans.to_dict())
for _ in range(20):
    app.update(); app.update_idletasks()
import time; time.sleep(0.4); app.update()
print('BUBBLES', len(v._bubbles))
app.destroy()
""" % str(ROOT)
r = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                   capture_output=True, text=True, timeout=180)
line = next((l for l in r.stdout.splitlines() if l.startswith("CHATWIDGETS")), "")
bub = next((l for l in r.stdout.splitlines() if l.startswith("BUBBLES")), "")
if not line:
    check("对话界面可构建", False, (r.stderr or r.stdout)[-400:])
else:
    nw = int(line.split()[1])
    check("对话界面构建出控件", nw > 10, f"{nw} 个")
    check("回答能在气泡里渲染", bub and int(bub.split()[1]) >= 1, bub)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
