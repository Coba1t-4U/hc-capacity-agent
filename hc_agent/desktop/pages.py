"""pages.py — 原生 Tk 页面。

三个页面与网页版同构（运行总览 / 容量分析 / 结果与证据），但**全部由 Tk 控件
绘制**：没有 HTML、没有 CSS、没有浏览器引擎。

每页只做两件事：`render()` 构建控件，`refresh()` 取数据后重绘。
数据一律来自 `backend` 调用，取不到就显示「未获取」——不用 0 或估数填充。
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional

from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W

NA = "未获取"


def fmt(v: Any, digits: int = 4) -> str:
    """数值格式化。取不到/非有限值一律返回「未获取」，绝不显示 0 冒充。"""
    if v is None or v == "":
        return NA
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n != n or n in (float("inf"), float("-inf")):
        return NA
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}"
    return f"{n:,.{digits}f}".rstrip("0").rstrip(".")


def fmt_unit(v: Any, unit: str, digits: int = 4) -> str:
    """带单位的数值。取不到时只显示「未获取」，**不再拼单位** ——
    "未获取 kW" 读起来像单位缺失，而不是数值缺失（实测观感问题）。"""
    s = fmt(v, digits)
    return NA if s == NA else f"{s} {unit}"


def level_tag(level: Any) -> tuple:
    return T.level_style(str(level or ""))


class Page:
    """页面基类。"""

    title = ""
    crumbs = ""
    #: 页面自己持有状态（对话记录、分析结果、选中项）时为 True。
    #: 置 True 后，后端数据回调**不会**整页重建它，避免内容被清空。
    STATEFUL = False

    def __init__(self, app) -> None:
        self.app = app
        self.sub = ""
        self.children: List[tk.Widget] = []

    # 子类实现
    def render(self, parent: tk.Frame) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def refresh(self) -> None:  # pragma: no cover - 抽象
        pass

    # 助手
    def clear(self) -> None:
        for w in self.children:
            try:
                w.destroy()
            except Exception:
                pass
        self.children = []

    def diag(self) -> Dict[str, Any]:
        return self.app.state.get("diagnostics") or {}

    def detail(self) -> Dict[str, Any]:
        return self.app.state.get("detail") or {}

    def runs(self) -> List[Dict[str, Any]]:
        return self.app.state.get("runs") or []


# ============================================================ 页面 1：运行总览
class ChatPage(Page):
    """问答页：左侧会话列表 + 右侧对话。

    交互对齐常见聊天应用：
      * 「＋ 新会话」开一段干净的对话
      * 点左侧任意一条历史会话，把当时的**消息与徽标一起回放**
      * 首条提问自动成为会话标题，不必先起名

    本页 **不整页重绘**（STATEFUL）—— 重绘会清空对话。
    """

    title = "问答"
    SUBS: list = []
    FULLSCREEN = True
    STATEFUL = True

    #: 侧栏宽度
    SIDE_W = 232

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = None
        self.history = None
        self.session_id = ""

    # ------------------------------------------------------------ 渲染
    def render(self, parent: tk.Frame) -> None:
        from hc_agent.desktop.chat import ChatView
        from hc_agent.qa import SUGGESTIONS

        self.clear()
        # 历史库用 App 的**同一份**：运行时按会话 id 写记录，
        # 视图再读同一份库，才不会"算完了但页面上看不到"。
        try:
            self.history = (self.app.chat_history()
                            if hasattr(self.app, "chat_history")
                            else None)
        except Exception:
            self.history = None

        holder = tk.Frame(parent, bg=T.BG)
        holder.pack(fill="both", expand=True)

        # ---- 左侧会话栏 ----
        side = tk.Frame(holder, bg=T.SUBNAV_BG, width=self.SIDE_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # 「新会话」已在左侧一级导航的药丸按钮上，这里不再重复放一个大按钮，
        # 只留一行标题（参考图的会话区就是纯列表）。
        W.label(side, "会话", bg=T.SUBNAV_BG, fg=T.MUTED,
                font=T.F.get("small")).pack(anchor="w", padx=16, pady=(14, 6))

        self.session_box = tk.Frame(side, bg=T.SUBNAV_BG)
        self.session_box.pack(fill="both", expand=True)

        ops = tk.Frame(side, bg=T.SUBNAV_BG)
        ops.pack(fill="x", padx=16, pady=10)
        dele = tk.Label(ops, text="删除该会话", bg=T.SUBNAV_BG, fg=T.FAINT,
                        font=T.F.get("small"), anchor="w", cursor="hand2")
        dele.pack(fill="x")
        dele.bind("<Button-1>", lambda e: self.delete_session())
        dele.bind("<Enter>", lambda e: dele.configure(fg=T.DANGER))
        dele.bind("<Leave>", lambda e: dele.configure(fg=T.FAINT))

        # ---- 右侧对话 ----
        right = tk.Frame(holder, bg=T.BG)
        right.pack(side="left", fill="both", expand=True)
        self.view = ChatView(
            right, on_ask=self.app.ask_question,
            suggestions=list(SUGGESTIONS),
            on_open_report=getattr(self.app, "open_report_path", None),
            history=self.history, session_id="",
            on_message_saved=self.refresh_sessions,
            on_drop=self.handle_drop,
            runtime=(self.app.chat_runtime()
                     if hasattr(self.app, "chat_runtime") else None))
        self.view.pack(fill="both", expand=True)

        # 打开时：沿用最近会话；没有就新建
        if self.history is not None:
            try:
                sess = self.history.ensure_session()
                self.session_id = sess["id"]
                self.view.set_session(self.session_id)
                msgs = self.history.list_messages(self.session_id)
                if msgs:
                    self.view.replay(msgs)
                else:
                    self._greeting()
            except Exception:
                self._greeting()
        else:
            self._greeting()

        # 如果这个会话正在后台算，回来时补一句提示（答案稍后会自己出现）
        try:
            rt = self.app.chat_runtime() if hasattr(self.app, "chat_runtime") else None
            if rt is not None and rt.inflight(self.session_id):
                self.view.add_system("这个会话正在计算，答案算完会自动出现在这里"
                                     "（期间可以切到别的页面或会话）。")
        except Exception:
            pass

        self.refresh_sessions()
        self.children.append(holder)
        self.app.chat_view = self.view
        self._busy_seen = set()
        self._poll_runtime()
        # 确保系统拖放已挂（失败会走「选择文件…」后备，不阻塞）
        try:
            if hasattr(self.app, "_attach_drop"):
                self.app._attach_drop()
        except Exception:
            pass          # 供后台线程算完后回投

    # ------------------------------------------------------------ 运行时状态
    def _poll_runtime(self) -> None:
        """主线程轮询"哪些会话正在算"，有变化就刷新侧栏。

        为什么用轮询而不是回调：运行时在**工作线程**里完成，
        直接回调过来碰 Tk 会抛 "main thread is not in main loop"。
        """
        try:
            if self.view is None or not self.view.winfo_exists():
                return
        except Exception:
            return
        try:
            rt = (self.app.chat_runtime()
                  if hasattr(self.app, "chat_runtime") else None)
            cur = rt.busy_sessions() if rt is not None else set()
            if cur != getattr(self, "_busy_seen", set()):
                self._busy_seen = cur
                self.refresh_sessions()
        except Exception:
            pass
        try:
            self.view.after(700, self._poll_runtime)
        except Exception:
            pass

    # ------------------------------------------------------------ 拖入处理
    def handle_drop(self, paths) -> None:
        """处理拖进来的文件/压缩包/算例。

        在**后台线程**里做（解压 + 抽正文 + 建索引可能要几秒），
        做完用 ChatView.post_drop 把结果投回主线程渲染 ——
        不能在工作线程里直接碰 Tk 控件。
        """
        if self.view is None:
            return
        paths = [str(x) for x in (paths or []) if str(x).strip()]
        if not paths:
            return
        # 先给个即时反馈，避免"点了没反应"
        names = "、".join(Path(p).name for p in paths[:3])
        more = f" 等 {len(paths)} 项" if len(paths) > 3 else ""
        self.view.add_system(f"正在读取：{names}{more} …")

        import queue as _q
        self._drop_q = _q.Queue()
        box = self._drop_q

        def work() -> None:
            try:
                from hc_agent.io import dropin
                from hc_agent import paths as _paths
                dest = _paths.data_root_safe() / "dss_dropped"
                res = dropin.handle(paths, case_dest=dest)
            except Exception as exc:
                res = {"ok": False, "message": f"处理失败：{exc}",
                       "items": [], "errors": [f"{type(exc).__name__}: {exc}"],
                       "imported_count": 0, "case_dirs": []}
            try:
                box.put(res)          # 只碰队列，不碰 Tk
            except Exception:
                pass

        def poll() -> None:
            """主线程轮询：拿到结果就渲染，否则继续等。"""
            try:
                if self.view is None or not self.view.winfo_exists():
                    return
            except Exception:
                return
            got = None
            try:
                got = box.get_nowait()
            except _q.Empty:
                got = None
            except Exception:
                got = None
            if got is None:
                try:
                    self.view.after(250, poll)
                except Exception:
                    pass
                return
            try:
                self.view._render_drop(got)
            except Exception as exc:
                try:
                    self.view.add_system(f"展示导入结果失败：{exc}")
                except Exception:
                    pass

        try:
            import threading
            threading.Thread(target=work, daemon=True).start()
            # 必须**在启动线程之后、主线程里**调度轮询
            self.view.after(250, poll)
        except Exception as exc:
            self.view.add_system(f"无法启动导入线程：{exc}")

    def _greeting(self) -> None:
        if self.view is None:
            return
        self.view.add_system(
            "可以直接用大白话问。所有数字都来自已完成的评估产物，"
            "查不到我会直说，不会估算或编造。")

    # ------------------------------------------------------------ 会话操作
    def refresh_sessions(self) -> None:
        """重画左侧会话列表（标题、条数、当前高亮）。"""
        box = getattr(self, "session_box", None)
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
        except Exception:
            return
        for w in box.winfo_children():
            w.destroy()
        sessions = []
        if self.history is not None:
            try:
                sessions = self.history.list_sessions(limit=60)
            except Exception:
                sessions = []
        if not sessions:
            W.label(box, "还没有历史会话", bg=T.SUBNAV_BG, fg=T.FAINT,
                    font=T.F.get("small")).pack(anchor="w", padx=14, pady=6)
            return
        for s in sessions:
            active = (s["id"] == self.session_id)
            starred = bool(s.get("starred"))
            row = tk.Frame(box, bg=T.PRIMARY_WEAK if active else T.SUBNAV_BG)
            row.pack(fill="x", padx=6, pady=1)
            # 还没有消息的会话显示"新会话"，比"（未命名）"自然
            title = s.get("title") or "新会话"
            busy = False
            try:
                rt = (self.app.chat_runtime()
                      if hasattr(self.app, "chat_runtime") else None)
                busy = bool(rt is not None and rt.inflight(s["id"]))
            except Exception:
                busy = False
            if busy:
                title = "⏳ " + title
            if starred:
                title = "★ " + title
            lb = W.label(row, title, bg=row["bg"],
                         fg=T.PRIMARY if active else T.TEXT,
                         font=T.F.get("bold") if active else T.F.get("base"),
                         anchor="w", wrap=self.SIDE_W - 60)
            lb.pack(side="left", padx=6, pady=5)
            W.label(row, str(s.get("msg_count") or 0), bg=row["bg"],
                    fg=T.FAINT, font=T.F.get("tiny")).pack(side="right",
                                                          padx=6)
            # 左右键都绑：左键切换，右键出菜单
            for w in (row, lb):
                w.bind("<Button-1>", lambda e, sid=s["id"]:
                       self.switch_session(sid))
                w.bind("<Button-3>", lambda e, sid=s["id"]:
                       self._session_menu(e, sid))
                # macOS / 部分触控板右键是 Button-2
                w.bind("<Button-2>", lambda e, sid=s["id"]:
                       self._session_menu(e, sid))
                try:
                    w.configure(cursor="hand2")
                except Exception:
                    pass

    # ------------------------------------------------------------ 右键菜单
    def _session_menu(self, event, sid: str) -> None:
        """在鼠标位置弹出会话操作菜单。

        为什么用右键菜单而不是每行加按钮：侧栏很窄，加三个按钮会把标题挤没；
        右键菜单不占地方，而且"删除/改名"本来就该是低频操作、需要一层确认。
        """
        if self.history is None:
            return
        sess = None
        try:
            sess = self.history.get_session(sid)
        except Exception:
            sess = None
        if sess is None:
            return
        starred = bool(sess.get("starred"))
        # 菜单的 master 必须是**真实 Tk 控件**：ChatPage 是普通 Python 对象
        # （页面不是控件容器），用它当 master 会报 no attribute 'tk'。
        master = self.view if self.view is not None else self.app
        menu = tk.Menu(master, tearoff=0, font=T.F.get("base"))
        menu.add_command(label="重命名…",
                         command=lambda: self.rename_session(sid))
        menu.add_command(
            label=("取消星标" if starred else "★ 标记（加星标）"),
            command=lambda: self.toggle_star(sid))
        menu.add_separator()
        menu.add_command(label="删除该会话",
                         command=lambda: self.delete_session(sid))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def rename_session(self, sid: str = "") -> None:
        """重命名会话（右键菜单入口）。"""
        if self.history is None:
            return
        sid = sid or self.session_id
        if not sid:
            return
        cur = ""
        try:
            cur = (self.history.get_session(sid) or {}).get("title") or ""
        except Exception:
            cur = ""
        new = simpledialog.askstring("重命名会话", "新的名称：",
                                     initialvalue=cur, parent=self)
        if new is None:
            return
        new = new.strip()
        if not new:
            return
        try:
            self.history.rename(sid, new)
        except Exception:
            return
        self.refresh_sessions()

    def toggle_star(self, sid: str = "") -> None:
        """切换星标（右键菜单入口）。星标会话会排到列表最上面。"""
        if self.history is None:
            return
        sid = sid or self.session_id
        if not sid:
            return
        try:
            self.history.toggle_starred(sid)
        except Exception:
            return
        self.refresh_sessions()

    def new_session(self) -> None:
        if self.history is None or self.view is None:
            return
        try:
            sess = self.history.create_session()
        except Exception:
            return
        self.session_id = sess["id"]
        self.view.set_session(self.session_id)
        self.view.clear_stream()
        self._greeting()
        self.refresh_sessions()

    def switch_session(self, sid: str) -> None:
        if self.history is None or self.view is None or not sid:
            return
        if sid == self.session_id:
            return
        self.session_id = sid
        self.view.set_session(sid)
        try:
            msgs = self.history.list_messages(sid)
        except Exception:
            msgs = []
        self.view.replay(msgs)
        if not msgs:
            self._greeting()
        self.refresh_sessions()

    def delete_session(self, sid: str = "") -> None:
        """删除会话。`sid` 为空时删当前会话（右键菜单会传具体 id）。"""
        sid = sid or self.session_id
        if self.history is None or not sid:
            return
        if not messagebox.askyesno(
                "删除会话", "删除当前会话及其全部消息？此操作不可恢复。",
                parent=self):
            return
        try:
            self.history.delete(sid)
        except Exception:
            pass
        sess = None
        try:
            sess = self.history.ensure_session()
        except Exception:
            pass
        self.session_id = (sess or {}).get("id", "")
        if self.view is not None:
            self.view.set_session(self.session_id)
            self.view.clear_stream()
            try:
                msgs = (self.history.list_messages(self.session_id)
                        if self.history else [])
            except Exception:
                msgs = []
            if msgs:
                self.view.replay(msgs)
            else:
                self._greeting()
        self.refresh_sessions()


class OverviewPage(Page):
    title = "运行总览"

    SUBS = [("overview", "环境与就绪度"), ("reliability", "可靠性设施"),
            ("channels", "引擎通道"), ("runs", "运行记录")]

    def render(self, parent: tk.Frame) -> None:
        self.clear()
        self.sub = self.sub or "overview"
        build = {"overview": self._overview, "reliability": self._reliability,
                 "channels": self._channels, "runs": self._runs}[self.sub]
        build(parent)

    # ---- 环境与就绪度
    def _overview(self, p: tk.Frame) -> None:
        d = self.diag()
        W.label(p, "环境与就绪度", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))

        wd = d.get("watchdog") or {}
        cl = d.get("crashlog") or {}
        chans = (self.app.state.get("channels") or {}).get("channels") or {}
        avail = sum(1 for c in chans.values() if c and c.get("available"))
        runs = self.runs()

        row = tk.Frame(p, bg=T.BG)
        row.pack(fill="x")
        cards = [
            W.kpi(row, "计算后端", "在线" if d else NA,
                  tag_text="已连接" if d else "未连接",
                  tag_kind="ok" if d else "err",
                  foot=f"PID {d.get('pid', '—')}"),
            W.kpi(row, "OpenDSS 引擎通道", f"{avail} / 3",
                  tag_text="可用" if avail else "不可用",
                  tag_kind="ok" if avail else "err",
                  foot=str((self.app.state.get("channels") or {}).get("selected") or "")),
            W.kpi(row, "引擎看门狗",
                  "运行中" if wd.get("started") else ("未启动" if wd.get("enabled") else "已禁用"),
                  tag_text="已启动" if wd.get("started") else "未启动",
                  tag_kind="ok" if wd.get("started") else "warn",
                  foot=f"复检 {fmt(wd.get('probes'), 0)} 次"),
            W.kpi(row, "崩溃可观测",
                  "已安装" if cl.get("installed") else "未安装",
                  tag_text="已安装" if cl.get("installed") else "未安装",
                  tag_kind="ok" if cl.get("installed") else "gray",
                  foot=f"已记录 {fmt(cl.get('records_written'), 0)} 条"),
        ]
        for c in cards:
            c.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.children.extend(cards)
        self.children.append(row)

        W.section_title(p, "最近一次运行", bg=T.BG)
        oc = d.get("run_outcome") or {}
        if not oc and runs:
            oc = self.app.state.get("latest_outcome") or {}
        if oc and oc.get("exit_code") is not None:
            box = W.card(p)
            box.pack(fill="x")
            head = tk.Frame(box.inner, bg=T.CARD)
            head.pack(fill="x")
            W.label(head, str(oc.get("run_id", "")), bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("h2")).pack(side="left")
            code = oc.get("exit_code")
            kind = "ok" if code == 0 else ("warn" if code in (5, 6) else "err")
            W.tag(head, f"退出码 {code}", kind, bg=T.CARD, dot=True).pack(side="right")
            trio = tk.Frame(box.inner, bg=T.CARD)
            trio.pack(fill="x", pady=(12, 0))
            for lab, val, lv in (("物理可开放容量", fmt_unit(oc.get("capacity_physical_kw"), "kW"), None),
                                 ("管理可开放容量", fmt_unit(oc.get("capacity_managed_kw"), "kW"), None),
                                 ("红黄绿等级", "", oc.get("level"))):
                if lv is not None:
                    txt, k = level_tag(lv)
                    c = W.kpi(trio, lab, txt, tag_text=txt, tag_kind=k)
                else:
                    c = W.kpi(trio, lab, val)
                c.pack(side="left", fill="both", expand=True, padx=(0, 10))
                self.children.append(c)
            if oc.get("error"):
                W.notice(box.inner, str(oc["error"]), "err")
            if oc.get("note"):
                W.notice(box.inner, str(oc["note"]), "info")
            self.children.append(box)
        else:
            box = W.card(p)
            box.pack(fill="x")
            W.label(box.inner, "还没有跑过任务", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("h2")).pack(pady=(10, 4))
            W.label(box.inner, "到「容量分析」页选择输入包并点击开始评估。",
                    bg=T.CARD, fg=T.MUTED).pack(pady=(0, 12))
            self.children.append(box)

    # ---- 可靠性设施
    def _reliability(self, p: tk.Frame) -> None:
        d = self.diag()
        wd = d.get("watchdog") or {}
        cl = d.get("crashlog") or {}
        st = d.get("state") or {}

        W.label(p, "可靠性设施", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(p, bg=T.BG)
        row.pack(fill="x")
        for c in (W.kpi(row, "看门狗状态",
                        "运行中" if wd.get("started") else "未启动",
                        tag_text="已启动" if wd.get("started") else "未启动",
                        tag_kind="ok" if wd.get("started") else "warn",
                        foot=str(wd.get("last_reason") or "")),
                  W.kpi(row, "熔断器", "已打开" if wd.get("breaker_open") else "闭合",
                        tag_text="熔断" if wd.get("breaker_open") else "正常",
                        tag_kind="warn" if wd.get("breaker_open") else "ok",
                        foot=f"窗口内重启 {fmt(wd.get('restarts_in_window'), 0)} 次"),
                  W.kpi(row, "崩溃日志",
                        "已安装" if cl.get("installed") else "未安装",
                        tag_text="已安装" if cl.get("installed") else "未安装",
                        tag_kind="ok" if cl.get("installed") else "gray",
                        foot=f"写盘失败 {fmt(cl.get('write_errors'), 0)} 次")):
            c.pack(side="left", fill="both", expand=True, padx=(0, 10))
            self.children.append(c)
        self.children.append(row)

        W.section_title(p, "看门狗参数与计数", bg=T.BG)
        box = W.card(p)
        box.pack(fill="x")
        left = tk.Frame(box.inner, bg=T.CARD)
        left.pack(side="left", fill="both", expand=True, padx=(0, 20))
        right = tk.Frame(box.inner, bg=T.CARD)
        right.pack(side="left", fill="both", expand=True)
        for k, v in (("复检次数", fmt(wd.get("probes"), 0)),
                     ("复检失败", fmt(wd.get("probe_failures"), 0)),
                     ("触发重启", fmt(wd.get("triggers"), 0)),
                     ("熔断拦截", fmt(wd.get("breaker_blocked"), 0))):
            W.kv_row(left, k, v)
        pr = wd.get("params") or {}
        for k, v in (("探测间隔", f"{pr.get('interval_s', '—')} s"),
                     ("复检超时", f"{pr.get('probe_timeout_s', '—')} s"),
                     ("卡死阈值", f"{pr.get('task_stuck_s', '—')} s"),
                     ("看门狗退出码", str(pr.get("exit_code", "—"))),
                     ("熔断上限", f"{pr.get('breaker_max', '—')} 次 / {pr.get('breaker_window_s', '—')} s")):
            W.kv_row(right, k, v)
        W.notice(box.inner,
                 "看门狗用「再问引擎一句 Version」区分算例慢与引擎中毒：秒回则继续"
                 "运行，超时才判中毒并以退出码 70 退出，交由桌面端重启后端。"
                 "没有外层重启者时它按设计拒绝启用，以免任务静默消失。", "info")
        self.children.append(box)

        W.section_title(p, "状态遗言", bg=T.BG)
        box2 = W.card(p)
        box2.pack(fill="x")
        W.kv_row(box2.inner, "最后阶段", str(d.get("last_stage") or "(无痕迹)"), mono=True)
        W.kv_row(box2.inner, "状态", str(st.get("status") or "—"))
        W.kv_row(box2.inner, "写入时间", str(st.get("_persisted_at_str") or "—"))
        W.notice(box2.inner,
                 "interrupted 表示「没来得及判定」，与「判定为失败」是两种语义："
                 "前者查崩溃日志与最后落盘阶段，后者查错误码。", "info")
        if cl.get("limitation"):
            W.notice(box2.inner, str(cl["limitation"]), "warn")
        self.children.append(box2)

    # ---- 引擎通道
    def _channels(self, p: tk.Frame) -> None:
        data = self.app.state.get("channels") or {}
        W.label(p, "引擎通道", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        if data.get("error"):
            box = W.card(p)
            box.pack(fill="x")
            W.notice(box.inner, str(data["error"]), "err")
            self.children.append(box)
            return
        box = W.card(p)
        box.pack(fill="both", expand=True)
        head = tk.Frame(box.inner, bg=T.CARD)
        head.pack(fill="x", pady=(0, 10))
        W.label(head, "三条通道与能力声明", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("h2")).pack(side="left")
        sel = data.get("selected")
        W.tag(head, f"当前：{sel}" if sel else "无可用通道",
              "info" if sel else "err", bg=T.CARD, dot=True).pack(side="right")

        cols = [{"key": "ch", "text": "通道", "width": 150},
                {"key": "st", "text": "状态", "width": 90},
                {"key": "iso", "text": "隔离性", "width": 150},
                {"key": "dig", "text": "有效位", "width": 90, "anchor": "e"},
                {"key": "tol", "text": "复算容差下限", "width": 130, "anchor": "e"},
                {"key": "note", "text": "说明", "width": 320}]
        tb = W.Table(box.inner, cols, height=5)
        rows = []
        for name, info in (data.get("channels") or {}).items():
            cap = (info or {}).get("capability") or {}
            rows.append((
                name + ("  ← 本次" if name == sel else ""),
                "可用" if (info or {}).get("available") else "不可用",
                "独立进程（可强杀）" if cap.get("killable_on_hang") else "进程内",
                "双精度" if cap.get("significant_digits") is None
                else f"{cap.get('significant_digits')} 位",
                str(cap.get("min_reliable_tolerance", "—")),
                str((info or {}).get("detail") or ""),
            ))
        tb.set_rows(rows)
        tb.pack(fill="both", expand=True)
        W.notice(box.inner,
                 "能力差异是显式声明的：CLI 通道经 CSV 导出，有效位约 6 位，"
                 "不可用于字段级复算比对。需要 1e-9 容差时选择器会自动排除它，"
                 "而不是安静地给出一个看似正常的错误结论。", "info")
        self.children.append(box)

    # ---- 运行记录
    def _runs(self, p: tk.Frame) -> None:
        W.label(p, "运行记录", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        runs = self.runs()
        box = W.card(p)
        box.pack(fill="both", expand=True)
        if not runs:
            W.label(box.inner, "还没有运行记录", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("h2")).pack(pady=(20, 6))
            W.label(box.inner, "到「容量分析」页发起一次评估。",
                    bg=T.CARD, fg=T.MUTED).pack(pady=(0, 20))
            self.children.append(box)
            return
        cols = [{"key": "run", "text": "运行号", "width": 150},
                {"key": "task", "text": "任务号", "width": 130},
                {"key": "cap", "text": "物理容量 kW", "width": 120, "anchor": "e"},
                {"key": "lvl", "text": "分级", "width": 80},
                {"key": "code", "text": "退出码", "width": 70, "anchor": "e"},
                {"key": "stage", "text": "最后阶段", "width": 100},
                {"key": "when", "text": "完成时间", "width": 160}]

        def on_pick(iid):
            try:
                rec = runs[int(iid)]
            except Exception:
                return
            self.app.open_run(rec.get("run_id", ""))

        tb = W.Table(box.inner, cols, height=12, on_select=on_pick)
        tb.set_rows([(
            r.get("run_id", ""), r.get("task_id", "—"),
            fmt(r.get("capacity_physical_kw")),
            level_tag(r.get("level"))[0] if r.get("level") else "—",
            str(r.get("exit_code", "—")), str(r.get("last_stage", "—")),
            str(r.get("finished_at", ""))[:19],
        ) for r in runs])
        tb.pack(fill="both", expand=True)

        # ---- 操作行：打开计算报告 / 查看过程 / 打开目录
        bar = tk.Frame(box.inner, bg=T.CARD)
        bar.pack(fill="x", pady=(10, 0))
        W.label(bar, "选中一行后：", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(side="left", padx=(0, 8))

        def _sel_run() -> str:
            sel = tb.tree.selection()
            if not sel:
                return ""
            try:
                return str(runs[int(sel[0])].get("run_id", ""))
            except Exception:
                return ""

        def do_open_report() -> None:
            rid = _sel_run()
            if not rid:
                W.notice(box.inner, "请先选中一行。", "warn")
                return
            self.app.open_report(rid)

        def do_process() -> None:
            rid = _sel_run()
            if not rid:
                W.notice(box.inner, "请先选中一行。", "warn")
                return
            self.app.open_run(rid)
            self.app.show("process", "stages")

        def do_ask() -> None:
            rid = _sel_run()
            if not rid:
                W.notice(box.inner, "请先选中一行。", "warn")
                return
            self.app.ask_question(f"{rid} 是什么情况？")
            self.app.show("chat")

        W.button(bar, "打开计算报告", do_open_report, small=True).pack(side="left")
        W.button(bar, "查看运算过程", do_process, small=True).pack(
            side="left", padx=(6, 0))
        W.button(bar, "问这次运行", do_ask, small=True).pack(
            side="left", padx=(6, 0))
        W.label(box.inner,
                "报告是 Word 文档，用系统默认程序打开；"
                "「查看运算过程」会跳到阶段轨迹与搜索轨迹。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small")).pack(anchor="w", pady=(8, 0))
        self.children.append(box)


# ============================================================ 页面 2：容量分析
class WorkbenchPage(Page):
    title = "容量分析"
    SUBS = [("howto", "发起评估"), ("scenario", "算例与场景"),
            ("constraints", "约束与阈值")]

    def render(self, parent: tk.Frame) -> None:
        self.clear()
        self.sub = self.sub or "howto"
        {"howto": self._howto, "scenario": self._scenario,
         "constraints": self._constraints}[self.sub](parent)

    def _howto(self, p: tk.Frame) -> None:
        W.label(p, "发起一次评估", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="x")
        W.label(box.inner, "输入包目录", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(anchor="w")
        row = tk.Frame(box.inner, bg=T.CARD)
        row.pack(fill="x", pady=(4, 10))
        ent = tk.Entry(row, font=T.F.get("base"), bg="#FFFFFF", fg=T.TEXT,
                       relief="solid", bd=1, highlightthickness=0,
                       insertbackground=T.TEXT)
        ent.insert(0, str(self.app.state.get("input_dir", "")))
        ent.pack(side="left", fill="x", expand=True, ipady=5)
        self.app.input_entry = ent

        W.label(box.inner, "输出目录", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(anchor="w")
        row2 = tk.Frame(box.inner, bg=T.CARD)
        row2.pack(fill="x", pady=(4, 10))
        ent2 = tk.Entry(row2, font=T.F.get("base"), bg="#FFFFFF", fg=T.TEXT,
                        relief="solid", bd=1, highlightthickness=0,
                        insertbackground=T.TEXT)
        ent2.insert(0, str(self.app.state.get("output_dir", "")))
        ent2.pack(side="left", fill="x", expand=True, ipady=5)
        self.app.output_entry = ent2

        btns = tk.Frame(box.inner, bg=T.CARD)
        btns.pack(fill="x", pady=(4, 0))
        W.button(btns, "选择输入目录", self.app.pick_input).pack(side="left", padx=(0, 8))
        W.button(btns, "选择输出目录", self.app.pick_output).pack(side="left", padx=(0, 8))
        self.app.run_btn = W.button(btns, "开始评估", self.app.start_task,
                                    kind="primary")
        self.app.run_btn.pack(side="left")

        self.app.progress = W.label(box.inner, "", bg=T.CARD, fg=T.PRIMARY,
                                    font=T.F.get("base"))
        self.app.progress.pack(anchor="w", pady=(10, 0))
        W.notice(box.inner,
                 "计算在独立后端进程中执行，界面保持可用；"
                 "若引擎中毒，看门狗会以退出码 70 请求重启后端，"
                 "桌面端自动拉起新进程，界面不会跟着消失。", "info")
        self.children.append(box)

        # ---- 本次运行结果（跑完当场就能开报告，不用再跑去「运行记录」页）
        self._last_run_block(p)

        W.section_title(p, "退出码契约", bg=T.BG)
        box2 = W.card(p)
        box2.pack(fill="x")
        cols = [{"key": "code", "text": "码", "width": 60, "anchor": "e"},
                {"key": "mean", "text": "含义", "width": 240},
                {"key": "how", "text": "排查方向", "width": 420}]
        tb = W.Table(box2.inner, cols, height=7)
        tb.set_rows([
            ("0", "成功", "—"),
            ("2", "输入阻断", "issues.csv 中的 blocking 项"),
            ("3", "计算失败", "错误码（DSS_*）"),
            ("4", "独立复算失败", "verification/ 差异明细"),
            ("5", "未满足发布门禁", "三道门禁中未提交的那一道"),
            ("6", "任务被中断（没来得及判定）", "崩溃日志 + 最后落盘阶段"),
            ("70", "看门狗判定引擎不可在进程内恢复", "由桌面端自动重启后端"),
        ])
        tb.pack(fill="both", expand=True)
        self.children.append(box2)

    def _last_run_block(self, p: tk.Frame) -> None:
        """显示"上一次跑的结果"并给出报告/过程/问答三个入口。

        存在的意义：跑完评估后用户最想做的就是**看报告**。
        以前要切到「运行记录」页选中那一行才能打开，多绕两步。
        """
        rid = str(self.app.state.get("last_run_id") or "")
        rdir = str(self.app.state.get("last_run_dir") or "")
        if not rid and not rdir:
            return

        W.section_title(p, "本次运行结果", bg=T.BG)
        box = W.card(p)
        box.pack(fill="x")

        W.label(box.inner, f"运行号：{rid or '（未知）'}", bg=T.CARD,
                fg=T.TEXT, font=T.F.get("bold")).pack(anchor="w")
        if rdir:
            W.label(box.inner, f"输出目录：{rdir}", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("tiny"), wrap=880).pack(anchor="w")

        # 报告是否已生成：当场判断并如实说明
        from hc_agent.desktop import runs as runs_mod
        rep = None
        try:
            rep = runs_mod.find_report_in(rdir) if rdir else None
            if rep is None and rid:
                rep = runs_mod.find_report(rid)
        except Exception:
            rep = None

        if rep is not None:
            W.tag(box.inner, f"报告已生成：{rep.name}", "ok",
                  bg=T.CARD).pack(anchor="w", pady=(6, 0))
        else:
            W.notice(box.inner,
                     "这次运行没有生成报告文件。可能停在生成报告之前的阶段，"
                     "看「查看运算过程」里的阶段轨迹能定位卡在哪。", "warn")

        ops = tk.Frame(box.inner, bg=T.CARD)
        ops.pack(fill="x", pady=(10, 0))

        def do_report() -> None:
            if rep is None:
                messagebox.showinfo(
                    "没有报告",
                    "这次运行没有报告文件，无法打开。", parent=self.app)
                return
            self.app.open_report_path(str(rep))

        def do_process() -> None:
            if rid:
                self.app.state["detail_run"] = rid
            self.app.show("process", "stages")

        def do_code() -> None:
            if rid:
                self.app.state["detail_run"] = rid
            self.app.show("process", "code")

        def do_ask() -> None:
            label = rid or rdir
            self.app.ask_question(f"{label} 是什么情况？")
            self.app.show("chat")

        b1 = W.button(ops, "打开计算报告", do_report, kind="primary")
        b1.pack(side="left")
        if rep is None:
            b1.set_enabled(False)
        W.button(ops, "查看运算过程", do_process, small=True).pack(
            side="left", padx=(8, 0))
        W.button(ops, "看运行代码", do_code, small=True).pack(
            side="left", padx=(6, 0))
        W.button(ops, "问这次运行", do_ask, small=True).pack(
            side="left", padx=(6, 0))

        W.label(box.inner,
                "报告是 Word 文档，用系统默认程序打开。「看运行代码」会显示"
                "这次实际怎么跑的（含可复现命令）。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small"), wrap=880).pack(anchor="w", pady=(8, 0))
        self.children.append(box)

    def _scenario(self, p: tk.Frame) -> None:
        d = self.detail()
        s = d.get("summary") or {}
        W.label(p, "算例与场景", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="x")
        W.kv_row(box.inner, "运行号", str(s.get("run_id") or d.get("run_id") or "—"), mono=True)
        W.kv_row(box.inner, "任务号", str(s.get("task_id") or "—"))
        W.kv_row(box.inner, "业务场景", str(s.get("business_scene") or "—"))
        W.kv_row(box.inner, "场景数", fmt(s.get("scenario_count")))
        W.kv_row(box.inner, "时间片总数", fmt(s.get("slice_count")))
        W.kv_row(box.inner, "基线收敛", str(s.get("all_converged", NA)))
        self.children.append(box)

    def _constraints(self, p: tk.Frame) -> None:
        d = self.detail()
        s = d.get("summary") or {}
        cs = s.get("constraint_summary") or {}
        W.label(p, "约束与阈值", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="x")
        W.notice(box.inner,
                 "所有限值来自经批准的规则包，Agent 不内置任何未经确认的常数。"
                 "规则为 null 表示未批准，必须阻断而非猜测。", "info")
        W.kv_row(box.inner, "规则包", str(s.get("rule_package") or "—"), mono=True)
        self.children.append(box)

        if cs:
            row = tk.Frame(p, bg=T.BG)
            row.pack(fill="x", pady=(12, 0))
            for lab, val, txt, kind in (
                    ("约束总数", fmt(cs.get("total"), 0), "", "gray"),
                    ("通过", fmt(cs.get("pass"), 0), "pass", "ok"),
                    ("不通过", fmt(cs.get("fail"), 0), "fail", "err"),
                    ("未覆盖", fmt(cs.get("unknown"), 0), "unknown", "warn")):
                c = W.kpi(row, lab, val,
                          tag_text=txt if txt and val not in ("0", NA) else "",
                          tag_kind=kind)
                c.pack(side="left", fill="both", expand=True, padx=(0, 10))
                self.children.append(c)
            self.children.append(row)

            bc = cs.get("binding_constraints") or []
            if bc:
                W.section_title(p, "临界约束", bg=T.BG)
                box2 = W.card(p)
                box2.pack(fill="both", expand=True)
                cols = [{"key": "cid", "text": "约束", "width": 150},
                        {"key": "obj", "text": "对象", "width": 140},
                        {"key": "metric", "text": "指标", "width": 130},
                        {"key": "val", "text": "实测", "width": 100, "anchor": "e"},
                        {"key": "lim", "text": "限值区间", "width": 150, "anchor": "e"},
                        {"key": "margin", "text": "余量", "width": 100, "anchor": "e"}]
                tb = W.Table(box2.inner, cols, height=8)
                tb.set_rows([(
                    str(b.get("constraint_id", "")), str(b.get("object_id", "")),
                    str(b.get("metric", "")), fmt(b.get("value")),
                    f"[{fmt(b.get('limit_lower'))}, {fmt(b.get('limit_upper'))}]",
                    fmt(b.get("margin")),
                ) for b in bc])
                tb.pack(fill="both", expand=True)
                self.children.append(box2)

        box3 = W.card(p)
        box3.pack(fill="x", pady=(12, 0))
        W.notice(box3.inner,
                 "unknown 口径：能力未覆盖（缺故障模型、零序参数等）记为 unknown，"
                 "它既不计为通过、也不计为不可行。把 unknown 当成不可行会让容量被"
                 "系统性低估，当成通过则会发布无依据的结论。", "warn")
        self.children.append(box3)


# ============================================================ 页面 3：结果与证据
class ResultsPage(Page):
    title = "结果与证据"
    SUBS = [("result", "容量结论"), ("caliber", "求解口径声明"),
            ("evidence", "证据链与门禁"), ("verify", "独立复算"),
            ("chapters", "报告章节"), ("acceptance", "验收与指标")]

    def render(self, parent: tk.Frame) -> None:
        self.clear()
        self.sub = self.sub or "result"
        {"result": self._result, "caliber": self._caliber,
         "evidence": self._evidence, "verify": self._verify,
         "chapters": self._chapters,
         "acceptance": self._acceptance}[self.sub](parent)

    def _acceptance(self, p: tk.Frame) -> None:
        """验收指标矩阵 + 可观测性（开发文档 11.2 / 12.3）。

        分两块：上面是"这一次运行达不达标"，下面是"最近所有运行的健康状况"。
        状态用四种：达标 / 未达标 / 无数据 / 不适用 ——
        **"无数据"不能显示成"达标"**，否则看板会变成自我安慰。
        """
        from hc_agent import acceptance as A

        d = self.detail()
        rid = d.get("run_id") or self.app.state.get("detail_run") or ""
        W.label(p, "验收与指标", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 6))

        # ---- 本次运行
        W.section_title(p, "本次运行的验收指标", bg=T.BG)
        box = W.card(p)
        box.pack(fill="x")
        run_dir = None
        if rid:
            try:
                from hc_agent.desktop import runs as runs_mod
                run_dir = runs_mod.resolve_run_dir(rid)
            except Exception:
                run_dir = None
        if run_dir is None:
            W.notice(box.inner,
                     "还没有选中运行。到「运行总览」里点一行，"
                     "或在下方看总体状况。", "warn")
        else:
            res = A.single_run(run_dir)
            rows = []
            for it in res["items"]:
                rows.append((
                    it["name"], it["target"],
                    str(it["value"]) if it["value"] is not None else "—",
                    A.STATUS_LABEL.get(it["status"], it["status"]),
                ))
            tb = W.Table(box.inner,
                         [{"key": "n", "text": "指标", "width": 200},
                          {"key": "t", "text": "门槛", "width": 110},
                          {"key": "v", "text": "实测", "width": 130},
                          {"key": "s", "text": "判定", "width": 90}],
                         height=len(rows))
            tb.set_rows(rows)
            tb.pack(fill="x")
            s_ = res["summary"]
            W.label(box.inner,
                    "汇总：" + "、".join(
                        f"{A.STATUS_LABEL.get(k, k)} {v}"
                        for k, v in sorted(s_.items(), key=lambda x: -x[1])),
                    bg=T.CARD, fg=T.TEXT, font=T.F.get("base")).pack(
                anchor="w", pady=(10, 0))
            # 不达标/不适用的说明
            for it in res["items"]:
                if it["status"] in ("fail", "unavailable", "unknown"):
                    W.label(box.inner,
                            f"· {it['name']}（{A.STATUS_LABEL.get(it['status'])}）"
                            f"：{it.get('detail') or ''}",
                            bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
                            wrap=880).pack(anchor="w", pady=(2, 0))
        self.children.append(box)

        # ---- 总体可观测性
        W.section_title(p, "近期运行总体状况", bg=T.BG)
        box2 = W.card(p)
        box2.pack(fill="x")
        try:
            from hc_agent.desktop import runs as runs_mod
            dirs = runs_mod.list_run_dirs()[:40]
            ov = A.overview(list(dirs))
        except Exception as exc:
            ov = {}
            W.notice(box2.inner, f"读不到运行记录：{exc}", "err")
        if ov:
            row = tk.Frame(box2.inner, bg=T.CARD)
            row.pack(fill="x")
            cards = [
                W.kpi(row, "运行总数", str(ov.get("total", 0)),
                      foot=f"成功 {ov.get('success', 0)} 次"),
                W.kpi(row, "成功率", f"{ov.get('success_rate', 0)}%",
                      tag_kind="ok" if ov.get("success_rate", 0) >= 95 else "warn"),
                W.kpi(row, "不收敛运行", str(ov.get("convergence_fail", 0)),
                      tag_kind="ok" if not ov.get("convergence_fail") else "warn"),
                W.kpi(row, "unknown 约束项",
                      str(ov.get("unknown_constraints", 0)),
                      foot="unknown ≠ 不可行"),
            ]
            for c in cards:
                c.pack(side="left", fill="both", expand=True, padx=(0, 10))
            row2 = tk.Frame(box2.inner, bg=T.CARD)
            row2.pack(fill="x", pady=(10, 0))
            lv = ov.get("level_dist") or {}
            rd = ov.get("rule_dist") or {}
            W.label(row2,
                    "等级分布：" + ("、".join(f"{k} {v}" for k, v in lv.items())
                                or "—")
                    + "　　规则版本：" + ("、".join(f"{k} {v}" for k, v in rd.items())
                                     or "—"),
                    bg=T.CARD, fg=T.TEXT, font=T.F.get("base"),
                    wrap=880).pack(anchor="w")
            bad = ov.get("chain_broken", 0)
            W.label(box2.inner,
                    f"审计链异常运行数：{bad}"
                    + ("（正常）" if not bad else "（需要追查）"),
                    bg=T.CARD, fg=T.SUCCESS if not bad else T.DANGER,
                    font=T.F.get("small")).pack(anchor="w", pady=(6, 0))
            if ov.get("failures"):
                W.label(box2.inner,
                        "失败原因分布：" + "、".join(
                            f"{k} {v}" for k, v in ov["failures"].items()),
                        bg=T.CARD, fg=T.MUTED,
                        font=T.F.get("small")).pack(anchor="w")
        self.children.append(box2)

        # ---- 说明
        W.section_title(p, "指标口径说明", bg=T.BG)
        box3 = W.card(p)
        box3.pack(fill="x")
        W.notice(box3.inner,
                 "判定分四种：**达标 / 未达标 / 无数据 / 不适用**。"
                 "「无数据」不等于通过 —— 表示这次没有可用来判定的产物。"
                 .replace("**", ""), "info")
        for k, name, target, why in A.MATRIX:
            W.label(box3.inner, f"· {name}（{target}）：{why}",
                    bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
                    wrap=880).pack(anchor="w")
        self.children.append(box3)

    #: 报告 9 章 → 建议问法（点一下就跳问答去问）
    CHAPTERS = (
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
    )

    def _chapters(self, p: tk.Frame) -> None:
        """报告 9 章逐章入口：每章一个"问这个"按钮。

        设计意图：报告是给人读的，但用户经常**只想问其中某一段**。
        把 9 章直接做成可点的问句，比让他记住怎么问更省事。
        """
        d = self.detail()
        rid = d.get("run_id") or self.app.state.get("detail_run") or ""
        W.label(p, "报告章节", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 6))
        if not rid:
            box = W.card(p)
            box.pack(fill="x")
            W.notice(box.inner,
                     "还没有选中运行。到「运行总览 → 运行记录」里点一行。",
                     "warn")
            self.children.append(box)
            return
        W.label(p,
                f"当前运行：{rid}　点任意一章的「问这个」，"
                f"会带着该章问题跳到问答页，答案只取这一章的产物。",
                bg=T.BG, fg=T.MUTED, font=T.F.get("small"),
                wrap=900).pack(anchor="w", pady=(0, 8))

        # ---- 成品报告：封面 + 目录 + 页眉页脚页码，可直接下载
        # 运行目录：优先用 state 里最近一次运行，其次按 run_id 解析。
        # 不能用 detail["report_path"]：detail 里没有这个键（实测取不到，
        # 于是误报“没有报告文件”）。
        from hc_agent.desktop import runs as runs_mod
        _run_dir = None
        _last = self.app.state.get("last_run_dir") or ""
        if _last:
            _run_dir = Path(str(_last))
        elif rid:
            try:
                _run_dir = runs_mod.resolve_run_dir(rid)
            except Exception:
                _run_dir = None
        _rep = None
        try:
            _rep = runs_mod.find_report_in(_run_dir) if _run_dir else None
            if _rep is None and rid:
                _rep = runs_mod.find_report(rid)
        except Exception:
            _rep = None
        bar = tk.Frame(p, bg=T.BG)
        bar.pack(fill="x", pady=(0, 8))

        def do_export() -> None:
            from hc_agent import report_export
            if _run_dir is None:
                messagebox.showinfo(
                    "没有选中的运行",
                    "先到「运行总览 → 运行记录」里点一行，再回来导出。",
                    parent=self.app)
                return
            if _rep is None and (Path(_run_dir) / "results" / "run_summary.json").is_file():
                # 这次运行有结果但没生成报告：当场补生成，而不是让用户去找
                try:
                    from hc_agent import reporting
                    reporting.generate_report(str(_run_dir))
                except Exception as exc:
                    messagebox.showerror("生成报告失败",
                                         f"{type(exc).__name__}: {exc}",
                                         parent=self.app)
                    return
            if not (Path(_run_dir) / "report" / "report.docx").is_file():
                messagebox.showinfo(
                    "没有报告文件",
                    "这次运行没有 report.docx，无法导出成品。"
                    "可能停在「生成报告」之前，看运算过程的阶段轨迹能定位卡在哪。",
                    parent=self.app)
                return
            try:
                out = report_export.export(Path(_run_dir))
            except Exception as exc:      # 导出失败要说清原因，不静默
                messagebox.showerror("导出失败",
                                     f"{type(exc).__name__}: {exc}",
                                     parent=self.app)
                return
            if messagebox.askyesno(
                    "已生成成品报告",
                    f"{out.name}\n\n保存位置：{out.parent}\n\n现在打开吗？",
                    parent=self.app):
                self.app.open_report_path(str(out))

        W.button(bar, "下载成品报告（含封面与目录）", do_export,
                 kind="primary", bg=T.BG).pack(side="left")
        W.label(bar, "　生成可直接交付的 docx：封面 + 目录 + 页眉页脚页码",
                bg=T.BG, fg=T.MUTED, font=T.F.get("small")).pack(side="left")
        self.children.append(bar)

        box = W.card(p)
        box.pack(fill="x")

        for idx, name, question, desc in self.CHAPTERS:
            row = tk.Frame(box.inner, bg=T.CARD)
            row.pack(fill="x", pady=3)
            W.label(row, f"{idx}. {name}", bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold"), width=14, anchor="w").pack(
                side="left")
            W.label(row, desc, bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("tiny"), anchor="w",
                    wrap=430).pack(side="left", fill="x", expand=True)

            def _ask(q=question, r=rid) -> None:
                # 把运行号拼进问句，确保问的是**当前选中的这次**运行
                self.app.ask_question(f"{r} {q}")
                self.app.show("chat")

            W.button(row, "问这个", _ask, small=True).pack(side="right")
        self.children.append(box)

        W.section_title(p, "说明", bg=T.BG)
        box2 = W.card(p)
        box2.pack(fill="x")
        W.notice(box2.inner,
                 "第 5 章（接入校核）只在业务场景是“接入校核/并网申请”时才有产物；"
                 "纯容量评估不会有这一章，问它时会如实说明“本章为空”，"
                 "不会拿别的章节凑。", "info")
        self.children.append(box2)

    def _result(self, p: tk.Frame) -> None:
        d = self.detail()
        s = d.get("summary") or {}
        oc = d.get("outcome") or {}
        merged = {**oc, **{k: v for k, v in s.items() if v is not None}}
        W.label(p, "容量结论", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        if not merged and not oc:
            box = W.card(p)
            box.pack(fill="x")
            W.label(box.inner, "还没有可展示的结果", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("h2")).pack(pady=(16, 4))
            W.label(box.inner, "先到「容量分析」页发起一次评估。",
                    bg=T.CARD, fg=T.MUTED).pack(pady=(0, 16))
            self.children.append(box)
            return

        row = tk.Frame(p, bg=T.BG)
        row.pack(fill="x")
        txt, k = level_tag(merged.get("level"))
        for c in (W.kpi(row, "物理可开放容量", fmt_unit(merged.get("capacity_physical_kw"), "kW"),
                        foot="受物理约束的最大可接入量"),
                  W.kpi(row, "管理可开放容量", fmt_unit(merged.get("capacity_managed_kw"), "kW"),
                        foot="再叠加管理裕度后的可发布值"),
                  W.kpi(row, "红黄绿等级", txt if merged.get("level") else NA,
                        tag_text=txt if merged.get("level") else "",
                        tag_kind=k, foot=str(merged.get("level_reason") or ""))):
            c.pack(side="left", fill="both", expand=True, padx=(0, 10))
            self.children.append(c)
        self.children.append(row)

        box = W.card(p)
        box.pack(fill="x", pady=(12, 0))
        W.kv_row(box.inner, "运行号", str(merged.get("run_id") or d.get("run_id") or "—"), mono=True)
        W.kv_row(box.inner, "主导限制因素", str(merged.get("binding_constraint") or "—"))
        W.kv_row(box.inner, "搜索方法", str(merged.get("search_method") or "—"))
        W.kv_row(box.inner, "搜索迭代次数", fmt(merged.get("search_iterations")))
        W.kv_row(box.inner, "搜索状态", str(merged.get("search_status") or "—"))
        W.kv_row(box.inner, "场景数 / 时间片",
                 f"{fmt(merged.get('scenario_count'))} / {fmt(merged.get('slice_count'))}")
        for n in (merged.get("search_notes") or []):
            W.label(box.inner, "· " + str(n), bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("small"), wrap=900).pack(anchor="w", pady=2)
        self.children.append(box)

    def _caliber(self, p: tk.Frame) -> None:
        d = self.detail()
        s = d.get("summary") or {}
        cal = s.get("caliber") or {}
        W.label(p, "求解口径声明", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        if not cal or cal.get("error"):
            box = W.card(p)
            box.pack(fill="x")
            W.notice(box.inner, "本次产物未包含口径声明。重新运行一次评估即可生成。",
                     "warn")
            self.children.append(box)
            return
        box = W.card(p)
        box.pack(fill="both", expand=True)
        head = tk.Frame(box.inner, bg=T.CARD)
        head.pack(fill="x", pady=(0, 10))
        W.label(head, "本次求解口径", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("h2")).pack(side="left")
        W.tag(head, str(cal.get("phase_state") or "balanced"), "info",
              bg=T.CARD, dot=True).pack(side="right")

        cols = [{"key": "k", "text": "项", "width": 140, "stretch": False},
                {"key": "v", "text": "本次口径", "width": 340},
                {"key": "n", "text": "说明", "width": 480}]
        tb = W.Table(box.inner, cols, height=10)
        tb.set_rows([(str(i.get("label", "")), str(i.get("value", "")),
                      str(i.get("note", "")).replace("**", ""))
                     for i in (cal.get("items") or [])])
        tb.pack(fill="both", expand=True)
        if cal.get("phase_statement"):
            W.notice(box.inner, str(cal["phase_statement"]).replace("**", ""), "info")
        self.children.append(box)

        if cal.get("assumptions"):
            W.section_title(p, "模型假设", bg=T.BG)
            b2 = W.card(p)
            b2.pack(fill="x")
            for a in cal["assumptions"]:
                W.label(b2.inner, "· " + str(a).replace("**", ""), bg=T.CARD,
                        fg=T.TEXT, wrap=900).pack(anchor="w", pady=2)
            self.children.append(b2)
        if cal.get("unsupported"):
            W.section_title(p, f"本次未覆盖（{len(cal['unsupported'])} 项）", bg=T.BG)
            b3 = W.card(p)
            b3.pack(fill="x")
            for u in cal["unsupported"]:
                W.label(b3.inner, "· " + str(u).replace("**", ""), bg=T.CARD,
                        fg=T.TEXT, wrap=900).pack(anchor="w", pady=2)
            self.children.append(b3)

    def _evidence(self, p: tk.Frame) -> None:
        d = self.detail()
        W.label(p, "证据链与门禁", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="x")
        gate = d.get("gate") or {}
        pub = d.get("publication") or {}
        st = d.get("state") or {}
        W.notice(box.inner,
                 "独立复算 + 专业复核 + 审批，任一道未满足即不得发布（退出码 5）。"
                 "这是设计约束，不是提示。", "info")
        passed = gate.get("passed")
        W.kv_row(box.inner, "门禁结论", "已通过" if passed else "未通过")
        W.kv_row(box.inner, "产物签封", "已签封" if pub else "未签封")
        W.kv_row(box.inner, "状态遗言阶段", str(st.get("_persisted_stage") or "—"), mono=True)
        sm = d.get("summary") or {}
        if sm.get("input_digest"):
            W.kv_row(box.inner, "输入清单摘要", str(sm["input_digest"]), mono=True)
        self.children.append(box)

    def _verify(self, p: tk.Frame) -> None:
        d = self.detail()
        v = d.get("verification") or {}
        W.label(p, "独立复算", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="x")
        W.notice(box.inner,
                 "独立复算由另一条引擎通道重跑并逐字段比对，用来发现单一通道的"
                 "系统性错误。有效位不足的通道会被自动排除，不参与比对。", "info")
        if not v:
            W.label(box.inner, "本次运行未包含复算产物。", bg=T.CARD,
                    fg=T.MUTED).pack(anchor="w", pady=8)
        else:
            W.kv_row(box.inner, "复算结论", "通过" if v.get("pass") else "未通过")
            cmp_ = v.get("comparison") or {}
            W.kv_row(box.inner, "差异项数", fmt(cmp_.get("mismatch_count"), 0))
        self.children.append(box)


class DssCasePage(Page):
    """OpenDSS 原生算例分析页（FULLSCREEN）。"""

    title = "算例分析"
    SUBS: list = []
    FULLSCREEN = True
    STATEFUL = True

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = None

    def render(self, parent: tk.Frame) -> None:
        from hc_agent.desktop.dss_page import DssPage
        self.clear()
        holder = tk.Frame(parent, bg=T.BG)
        holder.pack(fill="both", expand=True)
        self.view = DssPage(holder, on_ask=self.app.ask_question)
        self.view.pack(fill="both", expand=True)
        self.children.append(holder)


class ProcessPage(Page):
    """运算过程页：阶段轨迹 + 搜索轨迹 + 分场景结果。"""

    title = "运算过程"
    SUBS = [("stages", "阶段轨迹"), ("search", "容量搜索"),
            ("scenarios", "分场景结果"), ("code", "运行代码")]

    def render(self, parent: tk.Frame) -> None:
        self.clear()
        self.sub = self.sub or "stages"
        d = self.detail()
        run_id = d.get("run_id") or (self.app.state.get("detail_run") or "")
        if not run_id:
            box = W.card(parent)
            box.pack(fill="x")
            W.notice(box.inner, "还没有选中运行。到「运行总览 → 运行记录」里点一行。", "warn")
            self.children.append(box)
            return
        from hc_agent.desktop import process as P
        from hc_agent.desktop import runs as R
        rd = R.resolve_run_dir(run_id)
        if rd is None:
            box = W.card(parent)
            box.pack(fill="x")
            W.notice(box.inner, f"运行 {run_id} 不存在", "err")
            self.children.append(box)
            return
        proc = P.load(rd)
        if not proc.has_data():
            box = W.card(parent)
            box.pack(fill="x")
            W.notice(box.inner, "这次运行没有过程产物。", "warn")
            self.children.append(box)
            return
        if self.sub == "stages":
            self._stages(parent, run_id, proc)
        elif self.sub == "search":
            self._search(parent, run_id, proc)
        elif self.sub == "code":
            self._code(parent, run_id, proc)
        else:
            self._scenarios(parent, run_id, proc)

    def _stages(self, p, run_id, proc) -> None:
        W.label(p, f"{run_id} · 阶段轨迹", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="both", expand=True)
        tb = W.Table(box.inner, [
            {"key": "n", "text": "#", "width": 50, "anchor": "e"},
            {"key": "st", "text": "阶段", "width": 150},
            {"key": "lb", "text": "说明", "width": 170},
            {"key": "s", "text": "状态", "width": 90},
            {"key": "t", "text": "耗时 s", "width": 100, "anchor": "e"},
        ], height=12)
        tb.set_rows([(i + 1, s.key, s.label, s.status,
                      fmt(s.elapsed_s)) for i, s in enumerate(proc.stages)])
        tb.pack(fill="both", expand=True)
        self.children.append(box)

    def _search(self, p, run_id, proc) -> None:
        W.label(p, f"{run_id} · 容量搜索轨迹", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(p)
        box.pack(fill="both", expand=True)
        if not proc.trials:
            W.notice(box.inner, "这次运行没有搜索轨迹（可能是接入校核而非容量搜索）。", "warn")
            self.children.append(box)
            return
        tb = W.Table(box.inner, [
            {"key": "sq", "text": "序", "width": 50, "anchor": "e"},
            {"key": "d", "text": "试探容量 kW", "width": 130, "anchor": "e"},
            {"key": "r", "text": "结论", "width": 90},
            {"key": "b", "text": "卡在", "width": 170},
            {"key": "p", "text": "通过", "width": 70, "anchor": "e"},
            {"key": "f", "text": "不通过", "width": 80, "anchor": "e"},
            {"key": "u", "text": "未覆盖", "width": 80, "anchor": "e"},
        ], height=14)
        tb.set_rows([(
            getattr(t, "seq", i + 1), fmt(t.delta_kw),
            "可行" if t.feasible else "不可行",
            t.binding or "—", t.pass_count, t.fail_count, t.unknown_count,
        ) for i, t in enumerate(proc.trials)])
        tb.pack(fill="both", expand=True)
        feas = proc.feasible_trials
        W.notice(box.inner,
                 f"共 {proc.total_trials} 次试探，其中 {len(feas)} 次可行；"
                 f"最大可行容量 {fmt(max((t.delta_kw for t in feas), default=0))} kW。", "info")
        self.children.append(box)

    def _code(self, p, run_id, proc) -> None:
        """运行代码：如实区分"实际执行"与"等效复现"。"""
        rc = getattr(proc, "code", None)
        W.label(p, f"{run_id} · 运行代码", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))

        # ---- 触发方式与命令行
        box = W.card(p)
        box.pack(fill="x")
        if rc is None:
            W.notice(box.inner, "这次运行没有留下执行记录。", "warn")
            self.children.append(box)
            return

        if rc.exact:
            tag = ("实际执行", "ok")
        else:
            tag = ("等效复现（重建）", "warn")
        head = tk.Frame(box.inner, bg=T.CARD)
        head.pack(fill="x")
        W.label(head, "命令行", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(side="left")
        W.tag(head, tag[0], tag[1], bg=T.CARD).pack(side="left", padx=8)
        if rc.channel:
            W.tag(head, f"渠道 {rc.channel}", "gray",
                  bg=T.CARD).pack(side="left")

        cmd = tk.Text(box.inner, height=3, font=T.F.get("mono_sm"),
                      bg=T.HEAD_BG, fg=T.TEXT, relief="flat", bd=0,
                      wrap="char")
        cmd.pack(fill="x", pady=(6, 0))
        cmd.insert("1.0", rc.cmdline or "（无）")
        cmd.configure(state="disabled")

        ops = tk.Frame(box.inner, bg=T.CARD)
        ops.pack(fill="x", pady=(8, 0))

        def _copy() -> None:
            try:
                self.app.clipboard_clear()
                self.app.clipboard_append(rc.cmdline or "")
                W.notice(box.inner, "命令已复制到剪贴板。", "ok")
            except Exception as exc:
                W.notice(box.inner, f"复制失败：{exc}", "err")

        W.button(ops, "复制命令", _copy, small=True).pack(side="left")
        if rc.note:
            W.label(box.inner, rc.note, bg=T.CARD, fg=T.WARNING,
                    font=T.F.get("small"), wrap=880).pack(
                anchor="w", pady=(6, 0))
        if rc.exact and rc.argv:
            W.label(box.inner, "原始 argv：" + " ".join(rc.argv)[:600],
                    bg=T.CARD, fg=T.FAINT, font=T.F.get("tiny"),
                    wrap=880).pack(anchor="w", pady=(6, 0))
        if rc.exact and rc.cwd:
            W.label(box.inner, f"工作目录：{rc.cwd}", bg=T.CARD,
                    fg=T.FAINT, font=T.F.get("tiny"), wrap=880).pack(
                anchor="w")
        if rc.exact and rc.python:
            W.label(box.inner, f"解释器：{rc.python}", bg=T.CARD,
                    fg=T.FAINT, font=T.F.get("tiny"), wrap=880).pack(
                anchor="w")
        self.children.append(box)

        # ---- 执行轨迹（这部分是**实际**发生的，来自阶段记录与搜索轨迹）
        W.section_title(p, "实际执行轨迹", bg=T.BG)
        box2 = W.card(p)
        box2.pack(fill="x")
        for line in proc.code_summary():
            W.label(box2.inner, "· " + line, bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("base"), wrap=880).pack(anchor="w",
                                                         pady=(2, 0))
        if proc.stages:
            W.label(box2.inner, "阶段明细", bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold")).pack(anchor="w", pady=(10, 4))
            tb = W.Table(box2.inner, [
                {"key": "k", "text": "阶段", "width": 170},
                {"key": "l", "text": "说明", "width": 170},
                {"key": "s", "text": "状态", "width": 80},
                {"key": "t", "text": "耗时 s", "width": 90, "anchor": "e"},
            ], height=8)
            tb.set_rows([(st.key, st.label, st.status, fmt(st.elapsed_s))
                         for st in proc.stages])
            tb.pack(fill="x")
        self.children.append(box2)

        # ---- 引擎侧日志位置
        if rc.engine_log:
            W.section_title(p, "引擎侧记录", bg=T.BG)
            box3 = W.card(p)
            box3.pack(fill="x")
            W.label(box3.inner,
                    f"OpenDSS 适配器日志：{rc.engine_log}"
                    f"（共 {rc.engine_log_lines} 行）",
                    bg=T.CARD, fg=T.TEXT, font=T.F.get("base"),
                    wrap=880).pack(anchor="w")
            W.label(box3.inner,
                    "这是引擎实际收到的命令与报错记录，排查"
                    "“为什么这一步失败”时看它。",
                    bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
                    wrap=880).pack(anchor="w", pady=(4, 0))
            self.children.append(box3)

    def _scenarios(self, p, run_id, proc) -> None:
        W.label(p, f"{run_id} · 分场景结果", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        row = tk.Frame(p, bg=T.BG)
        row.pack(fill="x")
        for sc in proc.time_scenarios:
            wv = sc.worst_voltage()
            ml = sc.max_loading()
            c = W.kpi(row, f"{sc.label}",
                      f"{len(sc.rows)} 行",
                      tag_text=(f"越限 {sc.fail_count}" if sc.fail_count
                                else "无越限"),
                      tag_kind="warn" if sc.fail_count else "ok",
                      foot=f"Vmin {fmt(wv, 5)} pu · 最大负载 {fmt(ml, 1)}%")
            c.pack(side="left", fill="both", expand=True, padx=(0, 10))
            self.children.append(c)
        self.children.append(row)

        for sc in proc.time_scenarios:
            W.section_title(p, f"{sc.label}（{sc.scenario_id}）", bg=T.BG)
            box = W.card(p)
            box.pack(fill="x")
            rows = sorted(sc.rows, key=lambda r: float(r.get("value") or 0))
            tb = W.Table(box.inner, [
                {"key": "cid", "text": "约束", "width": 150},
                {"key": "obj", "text": "对象", "width": 160},
                {"key": "m", "text": "指标", "width": 120},
                {"key": "v", "text": "实测", "width": 110, "anchor": "e"},
                {"key": "lim", "text": "限值", "width": 150, "anchor": "e"},
                {"key": "s", "text": "状态", "width": 80},
            ], height=7)
            tb.set_rows([(
                str(r.get("constraint_id", "")), str(r.get("object_id", "")),
                str(r.get("metric", "")), fmt(r.get("value")),
                f"[{fmt(r.get('lower_limit'))}, {fmt(r.get('upper_limit'))}]",
                str(r.get("status", "")),
            ) for r in rows[:120]])
            tb.pack(fill="x")
            self.children.append(box)


class SettingsPageWrap(Page):
    """基础配置与知识库（FULLSCREEN）。"""

    title = "配置"
    SUBS: list = []
    FULLSCREEN = True
    STATEFUL = True

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = None

    def render(self, parent: tk.Frame) -> None:
        from hc_agent.desktop.settings_page import SettingsPage
        self.clear()
        holder = tk.Frame(parent, bg=T.BG)
        holder.pack(fill="both", expand=True)
        self.view = SettingsPage(
            holder,
            on_open_settings=lambda: self.app.chat_view.open_settings()
            if getattr(self.app, "chat_view", None) else None)
        self.view.pack(fill="both", expand=True)
        self.children.append(holder)


class KbPage(Page):
    """知识库管理（FULLSCREEN）——多库、上传、索引任务、检索测试、评估。"""

    title = "知识库"
    SUBS: list = []
    FULLSCREEN = True
    STATEFUL = True

    def __init__(self, app) -> None:
        super().__init__(app)
        self.view = None

    def render(self, parent: tk.Frame) -> None:
        from hc_agent.desktop.kb_page import KnowledgePage
        self.clear()
        holder = tk.Frame(parent, bg=T.BG)
        holder.pack(fill="both", expand=True)
        self.view = KnowledgePage(holder, on_ask=self.app.ask_question)
        self.view.pack(fill="both", expand=True)
        self.children.append(holder)


PAGES = {"chat": ChatPage, "overview": OverviewPage,
         "workbench": WorkbenchPage, "process": ProcessPage,
         "dss": DssCasePage, "kb": KbPage,
         "settings": SettingsPageWrap,
         "results": ResultsPage}
