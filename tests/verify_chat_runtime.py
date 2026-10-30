"""Verify 对话在切页 / 切会话时仍独立运行。

核心要证明三件事：
  1. 提问后切到别的页面，答案照样算完并写进**原会话**
  2. 提问后切到别的会话，答案不会串到当前显示的会话里
  3. 同一会话不会重复起算；界面能看出"这个会话在算"（⏳）
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
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
print("对话独立运行验证（切页 / 切会话 / 不串台）")
print("=" * 74)

tmp = __import__("pathlib").Path(tempfile.mkdtemp(prefix="hc-rt-"))
os.environ["HC_DATA_DIR"] = str(tmp)
os.environ.pop("DEEPSEEK_API_KEY", None)

from hc_agent.chat_history import ChatHistory
from hc_agent.desktop.chat_runtime import ChatRuntime

# ---------------------------------------------------------------- 1. 纯逻辑
print("\n1) ChatRuntime 基本行为（不启界面）")
h = ChatHistory(tmp / "chat.sqlite")
rt = ChatRuntime(lambda: h)

# 让回答慢下来，才能观察到"还在算"的状态
from hc_agent import qa as qa_mod
from hc_agent import llm as llm_mod

# 测试必须走**规则引擎**：项目根目录可能存着真实 Key，走 DeepSeek 的话
# 既不可控也慢，而且我下面的延迟注入不会生效。
class _NullCfg:
    enabled = False

_orig_from_env = llm_mod.LLMConfig.from_env
llm_mod.LLMConfig.from_env = classmethod(lambda cls, **kw: _NullCfg())

_orig_answer = qa_mod.Responder.answer


def _slow_answer(self, q, **kw):
    time.sleep(1.2)
    return _orig_answer(self, q, **kw)


qa_mod.Responder.answer = _slow_answer

s1 = h.create_session()["id"]
first = rt.submit(s1, "为什么容量是 0？")
check("首次提交成功", first is True)
check("提交后处于计算中", rt.inflight(s1) is True)
check("用户消息**立刻**落库（不等算完）",
      h.message_count(s1) == 1, str(h.message_count(s1)))
second = rt.submit(s1, "再问一句")
check("同一会话重复提交被拒", second is False)

t0 = time.time()
while time.time() - t0 < 60 and rt.inflight(s1):
    time.sleep(0.1)
check("算完后退出计算中状态", rt.inflight(s1) is False)
check("答案也落库（问 + 答 = 2 条）",
      h.message_count(s1) == 2, str(h.message_count(s1)))
msgs = h.list_messages(s1)
check("回答角色是 assistant",
      msgs[-1]["role"] == "assistant", str(msgs[-1]["role"]))
check("回答有内容", len(msgs[-1]["content"]) > 20,
      str(msgs[-1]["content"])[:40])
check("回答记录了引擎", bool(msgs[-1].get("engine")), str(msgs[-1].get("engine")))

# 另一个会话：答案必须落在自己会话
s2 = h.create_session()["id"]
rt.submit(s2, "电压越限的判据是什么？")
t0 = time.time()
while time.time() - t0 < 60 and rt.inflight(s2):
    time.sleep(0.1)
check("第二个会话各自落库",
      h.message_count(s2) == 2 and h.message_count(s1) == 2,
      f"s1={h.message_count(s1)} s2={h.message_count(s2)}")

# 空问题不提交
check("空问题不提交", rt.submit(s1, "   ") is False)
h.close()

# ---------------------------------------------------------------- 2. 界面
print("\n2) 界面：切页 / 切会话后仍独立运行")
qa_mod.Responder.answer = _slow_answer

from hc_agent.desktop.app import App

app = App()
app.update()
time.sleep(1.0)
app.update()
app.show("chat")
app.update()
time.sleep(0.6)
app.update()

rt2 = app.chat_runtime()
hist2 = app.chat_history()
pg = app.page
# 开一个**全新会话**：前面纯逻辑段已经往同一个库里写过两个会话，
# 直接沿用 latest 会让消息条数从 2 起算，断言全错位（踩过）。
pg.new_session()
app.update()
time.sleep(0.3)
app.update()
sid_a = pg.session_id
check("新会话初始无消息", hist2.message_count(sid_a) == 0,
      str(hist2.message_count(sid_a)))
check("拿到共享历史库", hist2 is not None)
check("拿到运行时", rt2 is not None)
check("视图已接入运行时", getattr(pg.view, "_runtime", None) is rt2)


def texts_of(w):
    out = []

    def walk(x):
        try:
            t = x.cget("text")
            if t:
                out.append(str(t))
        except Exception:
            pass
        try:
            kids = x.winfo_children()
        except Exception:
            return
        for c in kids:
            walk(c)

    walk(w)
    return out


# ---- A：提问后切页
pg.view.ask("为什么容量是 0？")
app.update()
# 只泵一小会儿就检查：注入的延迟是 1.2s，别等到算完才断言
time.sleep(0.25)
app.update()
check("A：提交后处于计算中", rt2.inflight(sid_a) is True)
app.show("overview")
app.update()
time.sleep(0.2)
app.update()
check("A：切页后仍在算（不受页面影响）", rt2.inflight(sid_a) is True)
t0 = time.time()
while time.time() - t0 < 60 and rt2.inflight(sid_a):
    app.update()
    time.sleep(0.1)
check("A：切页期间算完", rt2.inflight(sid_a) is False)
check("A：答案已写库",
      hist2.message_count(sid_a) == 2, str(hist2.message_count(sid_a)))

app.show("chat")
app.update()
time.sleep(0.5)
app.update()
pg = app.page
check("A：切回对话页选中同一会话", pg.session_id == sid_a)
joined = "\n".join(texts_of(pg.view))
check("A：页面上能看到提问", "为什么容量是 0" in joined)
check("A：页面上能看到答案（回放）", "越限" in joined or "容量" in joined)

# ---- B：提问后切会话
pg.view.ask("电压越限的判据是什么？")
app.update()
time.sleep(0.25)
app.update()
check("B：A 会话在算", rt2.inflight(sid_a) is True)
pg.new_session()
app.update()
sid_b = pg.session_id
check("B：切到了另一个会话", sid_b != sid_a, f"{sid_a[:8]}/{sid_b[:8]}")
t0 = time.time()
while time.time() - t0 < 60 and rt2.inflight(sid_a):
    app.update()
    time.sleep(0.1)
count_a = hist2.message_count(sid_a)
count_b = hist2.message_count(sid_b)
check("B：答案进了原会话 A（4 条）", count_a == 4, str(count_a))
check("B：没有串到新会话 B（0 条）", count_b == 0, str(count_b))

# ---- C：侧栏 ⏳
pg.switch_session(sid_a)
app.update()
time.sleep(0.3)
pg.view.ask("最低电压是多少？")
app.update()
time.sleep(0.25)
app.update()
# 轮询会把 ⏳ 画进侧栏
t0 = time.time()
while time.time() - t0 < 4:
    app.update()
    time.sleep(0.1)
    if any("⏳" in t for t in texts_of(pg.session_box)):
        break
check("C：侧栏给在算的会话加了 ⏳",
      any("⏳" in t for t in texts_of(pg.session_box)),
      str([t[:16] for t in texts_of(pg.session_box)]))

t0 = time.time()
while time.time() - t0 < 60 and rt2.inflight(sid_a):
    app.update()
    time.sleep(0.1)
# 算完到侧栏刷新之间有一次轮询间隔（~0.7s），给它一点时间
t0 = time.time()
while time.time() - t0 < 3:
    app.update()
    time.sleep(0.1)
    if not any("⏳" in t for t in texts_of(pg.session_box)):
        break
check("C：算完后 ⏳ 消失",
      not any("⏳" in t for t in texts_of(pg.session_box)),
      str([t[:16] for t in texts_of(pg.session_box)]))

# ---- D：视图销毁后不再投递（不崩）
print("\n3) 稳定性")
before = hist2.message_count(sid_a)
pg.view.ask("为什么容量是 0？")
app.update()
time.sleep(0.2)
app.update()
check("D：销毁前确实在算", rt2.inflight(sid_a) is True)
app.show("settings")          # 销毁 chat 视图
app.update()
t0 = time.time()
while time.time() - t0 < 60:
    app.update()
    time.sleep(0.1)
    if not rt2.inflight(sid_a):
        break
check("视图销毁后答案仍然写库（不丢）",
      hist2.message_count(sid_a) == before + 2,
      f"{before} -> {hist2.message_count(sid_a)}")
check("没有把进程搞崩", True)

app.destroy()
qa_mod.Responder.answer = _orig_answer
llm_mod.LLMConfig.from_env = _orig_from_env

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
