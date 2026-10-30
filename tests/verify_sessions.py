"""Verify 问答会话管理：新会话、历史会话切换与回放、持久化。

重点验证"回放要还原当时的完整样子"：
引擎徽标、出处、报告按钮都必须还在，否则历史消息看起来像被删了内容。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
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
print("问答会话管理验证")
print("=" * 74)

from hc_agent.chat_history import ChatHistory

tmp = Path(tempfile.mkdtemp(prefix="hc-chat-"))
_old = os.environ.get("HC_DATA_DIR")

try:
    # ---------------------------------------------------------------- 1. 存储层
    print("\n1) 会话存储（独立 SQLite，不混进知识库）")
    db = tmp / "chat.sqlite"
    h = ChatHistory(db)
    check("库文件建在指定位置", db.is_file(), str(db))
    check("与知识库库名不同（chat.sqlite）",
          db.name == "chat.sqlite", db.name)
    check("初始无会话", h.list_sessions() == [], str(h.list_sessions()))
    check("初始统计为 0",
          h.overview() == {"sessions": 0, "messages": 0}, str(h.overview()))

    s1 = h.create_session()
    check("能建会话", bool(s1.get("id")), str(s1))
    check("新会话无消息", h.message_count(s1["id"]) == 0)

    # 自动标题
    h.add_message(s1["id"], "user", "FD002 还能接多少光伏？")
    got = h.get_session(s1["id"]) or {}
    check("首条提问自动成为标题",
          (got.get("title") or "").startswith("FD002"), str(got.get("title")))
    h.add_message(s1["id"], "assistant", "物理 333.98 kW，管理 270.53 kW。",
                  engine="deepseek:deepseek-chat", source="runs/FD002")
    check("消息数正确", h.message_count(s1["id"]) == 2,
          str(h.message_count(s1["id"])))
    msgs = h.list_messages(s1["id"])
    check("角色顺序正确",
          [m["role"] for m in msgs] == ["user", "assistant"],
          str([m["role"] for m in msgs]))
    check("引擎被保留",
          msgs[1]["engine"] == "deepseek:deepseek-chat", msgs[1]["engine"])
    check("出处被保留", "FD002" in (msgs[1]["source"] or ""),
          msgs[1]["source"])

    # 报告路径也要存（否则回放时按钮消失）
    h.add_message(s1["id"], "user", "算一下算例")
    h.add_message(s1["id"], "assistant", "算完了",
                  engine="rule", report_path="C:/x/report.docx",
                  report_label="打开计算报告")
    msgs = h.list_messages(s1["id"])
    check("报告路径被保留",
          (msgs[-1].get("report_path") or "").endswith("report.docx"),
          str(msgs[-1].get("report_path")))
    check("报告按钮文案被保留",
          msgs[-1].get("report_label") == "打开计算报告",
          str(msgs[-1].get("report_label")))

    # 第二个会话
    s2 = h.create_session()
    h.add_message(s2["id"], "user", "电压越限的判据是什么？")
    check("两个会话独立", h.message_count(s2["id"]) == 1,
          str(h.message_count(s2["id"])))
    check("会话统计正确",
          h.overview()["sessions"] == 2 and h.overview()["messages"] == 5,
          str(h.overview()))

    sessions = h.list_sessions()
    check("列表按更新时间倒序（最新的在前）",
          sessions[0]["id"] == s2["id"], str([s["id"][:8] for s in sessions]))
    check("列表带消息条数",
          all("msg_count" in s for s in sessions), str(sessions[0]))

    # ensure_session
    got = h.ensure_session()
    check("ensure_session 复用最近会话", got["id"] == s2["id"],
          str(got.get("id")))

    # 改名
    check("能改名", h.rename(s2["id"], "判据疑问"), "rename 失败")
    check("改名生效",
          (h.get_session(s2["id"]) or {}).get("title") == "判据疑问")

    # 删除
    check("能删除会话", h.delete(s2["id"]))
    check("删除后会话数减少", h.overview()["sessions"] == 1,
          str(h.overview()))
    check("删除后消息一并清掉",
          h.overview()["messages"] == 4, str(h.overview()))
    check("删除不存在的会话返回 False", h.delete("s_not_exist") is False)

    # 清空消息但保留会话
    n = h.clear_messages(s1["id"])
    check("能清空消息", n == 4, str(n))
    check("会话还在", h.get_session(s1["id"]) is not None)
    h.close()

    # ---------------------------------------------------------------- 2. 持久化
    print("\n2) 重启后仍在（换一个实例读同一库）")
    h2 = ChatHistory(db)
    h2.add_message(s1["id"], "user", "重启后的新问题")
    h2.close()
    h3 = ChatHistory(db)
    msgs = h3.list_messages(s1["id"])
    check("重开能读到历史", len(msgs) == 1, str(len(msgs)))
    check("重开后会话仍在", len(h3.list_sessions()) == 1,
          str(len(h3.list_sessions())))
    h3.close()

    # ---------------------------------------------------------------- 2.5 排序
    print("\n2.5) 排序不依赖系统时钟（回归：Windows 时钟粒度 15.6ms）")
    import time as _t
    h4 = ChatHistory(tmp / "order.sqlite")
    made = []
    for i in range(12):
        ss = h4.create_session()
        h4.add_message(ss["id"], "user", f"第 {i} 个会话")
        made.append(ss["id"])
    # 不打任何 sleep：刻意让操作落在同一时间量子内
    listed = [x["id"] for x in h4.list_sessions()]
    check("连续快速建会话后顺序正确（最新在最前）",
          listed == list(reversed(made)),
          f"期望 {[m[:6] for m in reversed(made)]} 实际 {[x[:6] for x in listed]}")
    seqs = [x.get("seq") for x in h4.list_sessions()]
    check("会话带 seq 字段", all(isinstance(v, int) for v in seqs), str(seqs))
    check("seq 严格递减（无并列）",
          all(seqs[i] > seqs[i + 1] for i in range(len(seqs) - 1)),
          str(seqs))
    check("ensure_session 选中最新那个",
          h4.ensure_session()["id"] == made[-1],
          str(h4.ensure_session()["id"][:8]))
    # 旧会话再发消息 → 应重新排到最前
    h4.add_message(made[0], "user", "回到最早那个再问一句")
    check("活跃的旧会话会浮到最前",
          h4.list_sessions()[0]["id"] == made[0],
          str(h4.list_sessions()[0]["id"][:8]))
    h4.close()

    # 老库迁移：没有 seq 列的库打开后应自动补列并可正确排序
    legacy = tmp / "legacy.sqlite"
    import sqlite3 as _sq
    lc = _sq.connect(str(legacy))
    lc.executescript("""
        CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE message (id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, engine TEXT DEFAULT '',
            source TEXT DEFAULT '', report_path TEXT DEFAULT '',
            report_label TEXT DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        INSERT INTO session VALUES('s_old1','旧会话一','2026-01-01','2026-01-01');
        INSERT INTO session VALUES('s_old2','旧会话二','2026-01-02','2026-01-02');
    """)
    lc.commit()
    lc.close()
    h5 = ChatHistory(legacy)
    got = [x["id"] for x in h5.list_sessions()]
    check("老库打开后能读会话", len(got) == 2, str(got))
    check("老库自动补 seq 并排序（rowid 大的在前）",
          got[0] == "s_old2", str(got))
    h5.add_message("s_old1", "user", "旧会话也能写")
    check("老库里新增消息后该会话浮到最前",
          h5.list_sessions()[0]["id"] == "s_old1",
          str(h5.list_sessions()[0]["id"]))
    h5.close()

    # ---------------------------------------------------------------- 3. 接线
    print("\n3) 界面接线")
    chat_src = (ROOT / "hc_agent" / "desktop" / "chat.py").read_text(
        encoding="utf-8")
    pages_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(
        encoding="utf-8")

    check("有「＋ 新会话」按钮", "＋ 新会话" in pages_src)
    check("有历史会话列表", "历史会话" in pages_src)
    check("有会话切换", "switch_session" in pages_src)
    check("有会话删除", "delete_session" in pages_src)
    check("有消息回放", "def replay" in chat_src)
    check("回放会还原因擎与报告",
          "engine=m.get(\"engine\")" in chat_src.replace("'", '"')
          or 'engine=m.get("engine")' in chat_src)
    check("提问落库", "_save(\"user\"" in chat_src)
    check("回答落库", "_save(\n                        \"assistant\"" in chat_src
          or '"assistant", payload.get("text"' in chat_src)
    # 回归：不能再用 self._history 存数据库（会被输入历史覆盖）
    check("持久化用独立属性 self._db（避免命名冲突）",
          "self._db = history" in chat_src
          and "self._history.add_message" not in chat_src)

    # ---------------------------------------------------------------- 4. 运行时
    print("\n4) 运行时：新建 / 切回 / 回放气泡")
    # 用**全新的**数据目录：前面段落已经往 tmp 里写过会话，
    # 复用会让 ensure_session 选中旧会话，导致断言全部错位。
    run_tmp = Path(tempfile.mkdtemp(prefix='hc-chat-run-'))
    os.environ["HC_DATA_DIR"] = str(run_tmp)
    import subprocess
    probe = """
import sys, os, time
sys.path.insert(0, r'%s')
os.environ['HC_DATA_DIR'] = r'%s'
from hc_agent.desktop.app import App
app = App(); app.update(); time.sleep(0.9); app.update()
app.show('chat'); app.update(); time.sleep(0.5); app.update()
pg = app.page; v = pg.view

def wait(t=90):
    t0 = time.time()
    _rt = app.chat_runtime()
    while time.time() - t0 < t:
        app.update(); time.sleep(0.12)
        # 计算在 ChatRuntime 里，_busy 不再表示"在算"；
        # 必须等运行时把该会话的活干完，否则断言会在答案落库前执行。
        if not _rt.busy_sessions() and not v._queue:
            return

sid1 = pg.session_id
v.ask('为什么容量是 0？'); wait()
n1 = pg.history.message_count(sid1)
pg.new_session(); app.update(); time.sleep(0.3)
sid2 = pg.session_id
v.ask('电压越限的判据是什么？'); wait()
n2 = pg.history.message_count(sid2)
bubbles_new = len(v._bubbles)
pg.switch_session(sid1); app.update(); time.sleep(0.7); app.update()
b1 = len(v._bubbles)
back = (pg.session_id == sid1)
pg.switch_session(sid2); app.update(); time.sleep(0.7); app.update()
b2 = len(v._bubbles)
print('RESULT', sid1 != sid2, n1, n2, bubbles_new, b1, b2, back,
      len(pg.history.list_sessions()))
app.destroy()
""" % (str(ROOT), str(run_tmp))
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=300)
    line = next((l for l in r.stdout.splitlines()
                 if l.startswith("RESULT")), "")
    if not line:
        check("运行时探针可执行", False,
              (r.stderr or r.stdout)[-400:])
    else:
        p = line.split()
        distinct, n1, n2 = p[1] == "True", int(p[2]), int(p[3])
        bubbles_new, b1, b2 = int(p[4]), int(p[5]), int(p[6])
        back = p[7] == "True"
        total = int(p[8])
        check("两个会话是不同 id", distinct, line)
        check("会话1 落库 2 条（问+答）", n1 == 2, f"{n1}")
        check("会话2 落库 2 条（问+答）", n2 == 2, f"{n2}")
        # runtime 模式下不再有独立的"思考中"气泡（计算在页面之外进行），
        # 气泡 = 问候 + 提问，答案随后由 runtime 投递进来
        check("新会话后气泡数 = 问候+提问 = 2", bubbles_new == 2,
              f"{bubbles_new}")
        check("切回会话1 回放出全部历史气泡", b1 >= 2, f"{b1}")
        check("切到会话2 回放出全部历史气泡", b2 >= 2, f"{b2}")
        check("切换后当前会话正确", back, line)
        check("会话都进了列表（2 个有内容 + 新建的空会话）", total >= 2, f"{total}")

finally:
    if _old is None:
        os.environ.pop("HC_DATA_DIR", None)
    else:
        os.environ["HC_DATA_DIR"] = _old
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.rmtree(run_tmp, ignore_errors=True)
    except Exception:
        pass

# ---------------------------------------------------------------- 5. 右键菜单
print("\n5) 会话右键菜单：星标 / 改名 / 删除")
print_src = (ROOT / "hc_agent" / "desktop" / "pages.py").read_text(
    encoding="utf-8")
check("会话行绑定了右键事件",
      'w.bind("<Button-3>"' in print_src, "未绑定 Button-3")
check("有右键菜单构造函数", "def _session_menu" in print_src)
check("菜单含重命名", "重命名…" in print_src)
check("菜单含星标", "标记（加星标）" in print_src)
check("菜单含删除", "删除该会话" in print_src)
check("星标会话排在前面（排序用 starred）",
      "s.starred DESC" in (
          ROOT / "hc_agent" / "chat_history.py").read_text(encoding="utf-8"))
check("存储层支持星标",
      "def toggle_starred" in (
          ROOT / "hc_agent" / "chat_history.py").read_text(encoding="utf-8"))

# 运行时探针：真的点一遍三项操作
menu_tmp = Path(tempfile.mkdtemp(prefix="hc-chat-menu-"))
probe_menu = """
import sys, os, time
sys.path.insert(0, r'%s')
os.environ['HC_DATA_DIR'] = r'%s'
import tkinter as tk
from tkinter import simpledialog, messagebox
from hc_agent.desktop.app import App

app = App(); app.update(); time.sleep(0.9); app.update()
app.show('chat'); app.update(); time.sleep(0.5); app.update()
pg = app.page; v = pg.view

def wait(t=90):
    t0 = time.time()
    _rt = app.chat_runtime()
    while time.time() - t0 < t:
        app.update(); time.sleep(0.12)
        # 计算在 ChatRuntime 里，_busy 不再表示"在算"；
        # 必须等运行时把该会话的活干完，否则断言会在答案落库前执行。
        if not _rt.busy_sessions() and not v._queue:
            return

v.ask('为什么容量是 0？'); wait()
pg.new_session(); app.update()
v.ask('电压越限的判据是什么？'); wait()
pg.new_session(); app.update()
v.ask('最低电压是多少？'); wait()

ses = pg.history.list_sessions()
oldest = ses[-1]
# 星标
pg.toggle_star(oldest['id']); app.update(); time.sleep(0.3); app.update()
ses2 = pg.history.list_sessions()
starred_first = (ses2[0]['id'] == oldest['id'])
star_flag = bool(ses2[0]['starred'])
# 菜单可构造（拦截弹窗避免阻塞）
labels = []
def fake_popup(self, x, y):
    for i in range(self.index('end') + 1):
        try:
            t = self.entrycget(i, 'label')
            if t: labels.append(t)
        except Exception:
            pass
tk.Menu.tk_popup = fake_popup
tk.Menu.grab_release = lambda self: None
class E: x_root, y_root = 100, 100
pg._session_menu(E(), oldest['id'])
# 改名
simpledialog.askstring = lambda *a, **k: '改名后的会话'
pg.rename_session(oldest['id']); app.update(); time.sleep(0.2); app.update()
new_title = (pg.history.get_session(oldest['id']) or {}).get('title')
# 删除
messagebox.askyesno = lambda *a, **k: True
target = ses2[1]['id']
before = len(pg.history.list_sessions())
pg.delete_session(target); app.update(); time.sleep(0.2); app.update()
after = len(pg.history.list_sessions())
gone = pg.history.get_session(target) is None
print('MENU', starred_first, star_flag, len(labels), '|'.join(labels),
      new_title == '改名后的会话', before - after, gone)
app.destroy()
""" % (str(ROOT), str(menu_tmp))
r3 = subprocess.run([sys.executable, "-c", probe_menu], cwd=str(ROOT),
                    capture_output=True, text=True, timeout=300)
line3 = next((l for l in r3.stdout.splitlines() if l.startswith("MENU")), "")
if not line3:
    check("右键菜单运行时可用", False, (r3.stderr or r3.stdout)[-400:])
else:
    parts = line3.split(None, 5)
    starred_first = parts[1] == "True"
    star_flag = parts[2] == "True"
    n_items = int(parts[3])
    items = parts[4]
    renamed = parts[5].split()[0] == "True" if len(parts) > 5 else False
    check("星标后该会话排到最前", starred_first, line3)
    check("星标状态被持久化", star_flag, line3)
    check("菜单有 3 个操作项", n_items == 3, items)
    check("菜单项分别是重命名/星标/删除",
          "重命名" in items and "星标" in items and "删除" in items, items)
    check("改名生效", renamed, line3)
    check("删除后少一条",
          line3.split()[-2] == "1", line3)
    check("删除后被删会话真的不存在",
          line3.split()[-1] == "True", line3)
shutil.rmtree(menu_tmp, ignore_errors=True)

print("\n" + "=" * 74)
print(f"结果：{ok} 通过 / {fail} 失败 / {ok + fail} 总计")
print("=" * 74)
sys.exit(1 if fail else 0)
