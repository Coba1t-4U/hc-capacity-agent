"""chat.py — 原生 Tk 对话界面（嵌入式问答）。

形态参考常见的智能体对话：上方是消息流，下方是输入框 + 建议问题。
**全原生控件**，不依赖任何 Web 视图。

关键设计：
  * 回答在**后台线程**里算，算完回主线程插入 —— Tk 不是线程安全的，
    直接在工作线程里碰控件会随机崩溃。
  * 每条回答都显示**出处**（来自哪个运行的哪个文件），可核对。
  * 建议问题做成可点击的胶囊，降低"不知道能问什么"的门槛。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Dict, List, Optional

from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W


class ChatView(tk.Frame):
    """对话视图。pack 到任意容器即可用。"""

    def __init__(self, parent, *, on_ask: Callable[[str], None],
                 suggestions: Optional[List[str]] = None,
                 on_open_report: Optional[Callable[[str], None]] = None,
                 history=None, session_id: str = "",
                 on_message_saved=None, on_drop=None, runtime=None):
        super().__init__(parent, bg=T.BG, bd=0)
        self._on_ask = on_ask
        #: 点击"打开计算报告"时回调（由 App 提供）
        self._on_open_report = on_open_report
        #: 会话持久化（ChatHistory）。为 None 时退化为不存历史。
        #: **不能叫 self._history** —— 那个名字已被"输入框↑键历史"占用，
        #: 赋值后会被下面的 self._history = [] 覆盖成列表，
        #: 于是所有落库都 AttributeError 并被静默吞掉（实测踩过：
        #: 会话条数一直是 0）。故用具名 self._db。
        self._db = history
        self._session_id = session_id
        #: 每条消息落库后通知侧栏刷新（标题/条数会变）
        self._on_message_saved = on_message_saved
        #: 拖入文件后的处理回调（由 ChatPage 提供）
        self._on_drop = on_drop
        #: 问答运行时（App 持有）。有它时：算与存都归它，视图只管显示
        self._runtime = runtime
        self._runtime_managed = runtime is not None
        self._busy = False
        self._bubbles: List[tk.Widget] = []
        self._suggestions = suggestions or []

        # ---- 底部输入区（**先 pack bottom**，否则 scroll 会吃掉整窗高度，
        #       把输入区挤到窗口外 —— 实测踩过：canvas 只有 265px 高）
        bottom = tk.Frame(self, bg=T.BG, bd=0)
        bottom.pack(side="bottom", fill="x")

        # 输入卡片：白底 + 1px 边框 + 上下留白（参考图的 composer）
        card = tk.Frame(bottom, bg=T.CARD,
                        highlightbackground=T.BORDER_STRONG,
                        highlightthickness=1)
        card.pack(fill="x", padx=24, pady=(8, 8))

        # ---- 消息流（可滚动）
        self.scroll = W.ScrollArea(self, bg=T.BG)
        self.scroll.pack(side="top", fill="both", expand=True)
        self.stream = tk.Frame(self.scroll.body, bg=T.BG)
        self.stream.pack(fill="both", expand=True, padx=2, pady=2)

        # 建议问题胶囊（卡片顶部）
        self.chips = tk.Frame(card, bg=T.CARD)
        self.chips.pack(fill="x", padx=12, pady=(9, 0))
        self.engine_lbl = W.label(card, "", bg=T.CARD, fg=T.FAINT,
                                  font=T.F.get("tiny"))
        self.engine_lbl.pack(anchor="e", padx=14)

        row = tk.Frame(card, bg=T.CARD)
        row.pack(fill="x", padx=12, pady=(6, 10))
        # 输入框去边框：卡片本身已经是边框，再套一层会显脏
        self.entry = tk.Entry(row, font=T.F.get("base"), bg=T.CARD, fg=T.TEXT,
                              relief="flat", bd=0, highlightthickness=0,
                              insertbackground=T.TEXT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.bind("<Up>", self._recall)
        self.send_btn = W.button(row, "↑  发送", self._submit, kind="primary")
        self.send_btn.pack(side="right", padx=(8, 0))
        self.case_btn = W.button(row, "＋ 添加算例", self.open_add_case,
                                 small=True)
        self.case_btn.pack(side="right", padx=(8, 0))
        self.cfg_btn = W.button(row, "模型设置", self.open_settings, small=True)
        self.cfg_btn.pack(side="right", padx=(8, 0))
        # 计算引擎选择（放在参考图模型标签的位置）：点开是单选菜单
        self.engine_mode_lbl = tk.Label(
            row, text="", bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
            cursor="hand2", padx=8)
        self.engine_mode_lbl.pack(side="right", padx=(8, 0))
        self.engine_mode_lbl.bind("<Button-1>", lambda e: self._pick_engine())
        self.engine_mode_lbl.bind("<Enter>", lambda e: self.engine_mode_lbl.configure(
            bg=T.NAV_HOVER))
        self.engine_mode_lbl.bind("<Leave>", lambda e: self.engine_mode_lbl.configure(
            bg=T.CARD))
        self._refresh_engine_mode()

        # ---- 拖放提示：把压缩包/文档拖进来就能问
        drop_row = tk.Frame(card, bg=T.CARD)
        drop_row.pack(fill="x", padx=12, pady=(0, 6))
        self.drop_hint = tk.Label(
            drop_row, text="⬇ 把压缩包 / 文档 / 算例拖到这里，或",
            bg=T.CARD, fg=T.MUTED, font=T.F.get("small"), anchor="w")
        self.drop_hint.pack(side="left")
        self.drop_btn = W.button(drop_row, "选择文件…",
                                 self._pick_drop, small=True)
        self.drop_btn.pack(side="left", padx=(6, 0))
        self.drop_note = tk.Label(
            drop_row, text="", bg=T.CARD, fg=T.FAINT,
            font=T.F.get("tiny"), anchor="w")
        self.drop_note.pack(side="left", padx=(8, 0))

        self.cases_row = tk.Frame(card, bg=T.CARD)
        self.rebuild_chips()
        self.rebuild_cases()
        self.refresh_engine_badge()

        self._pending: "queue.Queue[tuple]" = queue.Queue()
        self._history: List[str] = []
        self._queue: List[str] = []
        self._hist_idx = 0
        self.after(120, self._drain)

    # ------------------------------------------------------------ 算例
    def _pick_drop(self) -> None:
        """后备入口：手动选文件/文件夹/压缩包，走和拖放相同的处理。"""
        from tkinter import filedialog, messagebox
        try:
            paths = filedialog.askopenfilenames(
                parent=self,
                title="选择要导入的文件（压缩包/文档/算例）",
                filetypes=[("压缩包", "*.zip"),
                           ("文档", "*.md *.txt *.csv *.docx *.pdf *.xlsx"),
                           ("OpenDSS 算例", "*.dss"),
                           ("全部文件", "*.*")])
        except Exception:
            paths = ()
        if not paths:
            # 也可能是想选一个算例目录
            try:
                d = filedialog.askdirectory(
                    parent=self, title="或选择一个算例目录 / 资料文件夹")
            except Exception:
                d = ""
            if not d:
                return
            paths = (d,)
        if self._on_drop:
            self._on_drop(list(paths))
        else:
            messagebox.showinfo("暂不可用", "当前没有接入导入处理。", parent=self)

    def post_drop(self, result: dict) -> None:
        """后台导入完成后，把结果投进对话区（主线程渲染）。"""
        if not self._alive():
            return
        try:
            self._pending.put(("drop", result))
        except Exception:
            pass

    def open_add_case(self) -> None:
        """添加一个 OpenDSS 算例（目录或多文件中选择 .dss）。

        做成小选择框而不是直接弹目录选择：算例可能是**一个目录**，
        也可能是**单个 .dss 文件**，两种都得能加。
        """
        # 父窗口挂到**顶层窗口**，不要挂到 ChatView 这个 Frame 上：
        # 挂 Frame 上时 Toplevel 不属于 root 的子窗口树，
        # 模态与定位都会怪，测试也找不到它（实测踩过）。
        win = tk.Toplevel(self.winfo_toplevel())
        win.title("添加 OpenDSS 算例")
        win.configure(bg=T.BG)
        win.geometry("560x300")
        win.transient(self.winfo_toplevel())
        win.grab_set()

        body = tk.Frame(win, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        W.label(body, "添加 OpenDSS 算例", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w")
        W.label(body,
                "算例不会被复制，只记录它在你磁盘上的位置。"
                "所以你改完算例参数后重新问一次，就按新参数重算。",
                bg=T.BG, fg=T.MUTED, font=T.F.get("small"),
                wrap=470).pack(anchor="w", pady=(4, 12))

        def _do(path: str) -> None:
            try:
                from hc_agent.io import dss_registry
                r = dss_registry.add_case(path)
            except Exception as exc:
                messagebox.showwarning("添加失败",
                                       f"{type(exc).__name__}: {exc}",
                                       parent=win)
                return
            win.destroy()
            if not r.get("ok"):
                messagebox.showwarning("添加失败", r.get("error", ""),
                                       parent=self)
                return
            c = r["case"]
            note = "（这个算例之前已经加过）" if r.get("already") else ""
            self.add_system(
                f"已添加算例：{c['name']}{note}\n"
                f"  位置：{c['dir']}\n"
                f"  共 {c['files']} 个 .dss 文件"
                + ("（含 Master.dss）" if c.get("has_master") else "")
                + "\n直接说「算一下我加的算例」即可，报告生成后按钮会出现在回答里。")
            self.rebuild_cases()

        def pick_dir() -> None:
            d = filedialog.askdirectory(title="选择 OpenDSS 算例目录")
            if d:
                _do(d)

        def pick_file() -> None:
            f = filedialog.askopenfilename(
                title="选择 OpenDSS 算例主文件",
                filetypes=[("OpenDSS", "*.dss *.DSS"), ("所有文件", "*.*")])
            if f:
                _do(f)

        btns = tk.Frame(body, bg=T.BG)
        btns.pack(fill="x", pady=(4, 0))
        W.button(btns, "选择算例目录", pick_dir, kind="primary").pack(
            side="left")
        W.button(btns, "选择 .dss 文件", pick_file).pack(side="left", padx=8)
        W.button(btns, "取消", win.destroy).pack(side="right")

    def rebuild_cases(self) -> None:
        """刷新"已添加算例"快捷条：点一下就让它去算这个算例。"""
        try:
            row = self.cases_row
            if not row.winfo_exists():
                return
        except Exception:
            return
        for w in row.winfo_children():
            w.destroy()
        try:
            from hc_agent.io import dss_registry
            cases = dss_registry.list_cases()
        except Exception:
            cases = []
        if not cases:
            row.pack_forget()
            return
        row.pack(fill="x", padx=16, pady=(8, 0), before=self.chips)
        W.label(row, "已添加算例：", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("tiny")).pack(side="left", padx=(0, 6))
        for c in cases[:4]:
            alive = bool(c.get("alive"))
            label = c["name"] + ("" if alive else "（已失效）")
            tag = W.tag(row, label, "info" if alive else "gray", bg=T.CARD)
            tag.pack(side="left", padx=(0, 6))
            if alive:
                for wdg in (tag, tag._lbl, tag._box):
                    wdg.bind("<Button-1>",
                             lambda e, n=c["name"]: self.ask(
                                 f"算一下算例 {n}，并给我计算报告"))
                    try:
                        wdg.configure(cursor="hand2")
                    except Exception:
                        pass

    def _clear_cases(self) -> None:
        try:
            from hc_agent.io import dss_registry
            for c in dss_registry.list_cases():
                dss_registry.remove_case(str(c.get("id")))
        except Exception:
            pass
        self.rebuild_cases()

    # ------------------------------------------------------------ 模型设置
    def refresh_engine_badge(self) -> None:
        """底部显示当前用哪个引擎回答。"""
        try:
            from hc_agent import llm
            cfg = llm.LLMConfig.from_env()
            txt = ("DeepSeek · " + cfg.mask_str()) if cfg.enabled else "本地规则引擎"
        except Exception:
            txt = "本地规则引擎"
        try:
            self.engine_lbl.configure(text="当前： " + txt)
        except Exception:
            pass

    def open_settings(self) -> None:
        """配置 DeepSeek API Key 的对话框（原生 Toplevel）。"""
        from hc_agent import llm
        cur = llm.LLMConfig.from_env()

        win = tk.Toplevel(self)
        win.title("模型设置")
        win.configure(bg=T.BG)
        win.geometry("620x330")
        win.transient(self.winfo_toplevel())
        win.grab_set()

        body = tk.Frame(win, bg=T.BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        W.label(body, "DeepSeek API 接入", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w")
        W.label(body,
                "填 Key 后由 DeepSeek 负责理解与措辞；所有数字仍从本地评估产物"
                "经工具读取，模型不得自行计算。留空则使用本地规则引擎。",
                bg=T.BG, fg=T.MUTED, font=T.F.get("small"),
                wrap=560).pack(anchor="w", pady=(4, 12))

        def field(label_text: str, value: str, show: str = "") -> tk.Entry:
            W.label(body, label_text, bg=T.BG, fg=T.MUTED,
                    font=T.F.get("small")).pack(anchor="w", pady=(6, 2))
            e = tk.Entry(body, font=T.F.get("mono"), bg="#FFFFFF", fg=T.TEXT,
                         relief="solid", bd=1, highlightthickness=0,
                         insertbackground=T.TEXT, show=show)
            e.insert(0, value)
            e.pack(fill="x", ipady=5)
            return e

        e_key = field("API Key（sk-…）", cur.api_key, show="•")
        e_model = field("模型", cur.model)
        e_base = field("接口地址", cur.base_url)

        status = W.label(body, "", bg=T.BG, fg=T.MUTED,
                         font=T.F.get("small"), wrap=560)
        status.pack(anchor="w", pady=(10, 0))

        btns = tk.Frame(body, bg=T.BG)
        btns.pack(fill="x", pady=(14, 0))

        def do_test() -> None:
            cfg = llm.LLMConfig(
                api_key=e_key.get().strip(),
                base_url=e_base.get().strip() or llm.DEFAULT_BASE,
                model=e_model.get().strip() or llm.DEFAULT_MODEL)
            status.configure(text="正在测试连接…", fg=T.MUTED)
            win.update_idletasks()

            def _t() -> None:
                r = llm.self_test(cfg)

                def _show() -> None:
                    if r.get("ok"):
                        status.configure(
                            text=f"连接成功（模型 {r.get('model')}）", fg=T.SUCCESS)
                    else:
                        status.configure(text=f"连接失败：{r.get('reason')}",
                                         fg=T.DANGER)
                        if r.get("detail"):
                            print(r["detail"])
                try:
                    win.after(0, _show)
                except Exception:
                    pass
            threading.Thread(target=_t, daemon=True).start()

        def do_save() -> None:
            r = llm.save_config(e_key.get(), base_url=e_base.get(),
                                model=e_model.get())
            self.refresh_engine_badge()
            status.configure(
                text=f"已保存到 {r['path']}（Key {r['masked']}）。"
                     f"Key 以明文存于你的用户目录。",
                fg=T.SUCCESS)

        W.button(btns, "测试连接", do_test).pack(side="left")
        W.button(btns, "保存", do_save, kind="primary").pack(side="left", padx=8)
        W.button(btns, "关闭", win.destroy).pack(side="right")

    # ------------------------------------------------------------ 建议问题
    # ------------------------------------------------------------ 引擎选择
    def _refresh_engine_mode(self) -> None:
        """把当前引擎写回标签（选择变了就刷新）。"""
        try:
            from hc_agent.engines import selector as S
            self.engine_mode_lbl.configure(
                text=f"引擎 {S.label_of(S.get_mode())} ▾")
        except Exception:
            pass

    def _pick_engine(self) -> None:
        """弹出引擎单选菜单。"""
        try:
            from hc_agent.engines import selector as S
        except Exception:
            return
        menu = tk.Menu(self, tearoff=0)
        var = tk.StringVar(value=S.get_mode())
        for m in S.MODES:
            menu.add_radiobutton(
                label=f"{m['label']}　·　{m['hint']}",
                value=m["id"], variable=var,
                command=lambda mid=m["id"]: self._set_engine(mid))
        try:
            menu.tk_popup(self.engine_mode_lbl.winfo_rootx(),
                          self.engine_mode_lbl.winfo_rooty()
                          + self.engine_mode_lbl.winfo_height())
        finally:
            menu.grab_release()

    def _set_engine(self, mode: str) -> None:
        """切换引擎，并当场说明代价，避免用户以为换了还一样。"""
        try:
            from hc_agent.engines import selector as S
        except Exception:
            return
        mid = S.set_mode(mode)
        self._refresh_engine_mode()
        self.add_system(f"计算引擎已切换为「{S.label_of(mid)}」：{S.hint_of(mid)}")

    def rebuild_chips(self) -> None:
        for w in self.chips.winfo_children():
            w.destroy()
        W.label(self.chips, "可以这样问：", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(side="left", padx=(0, 8))
        for s in self._suggestions[:5]:
            c = W.tag(self.chips, s, "info", bg=T.CARD)
            c.pack(side="left", padx=(0, 6))
            for wdg in (c, c._lbl, c._box):          # type: ignore[attr-defined]
                wdg.bind("<Button-1>", lambda e, q=s: self.ask(q))
                try:
                    wdg.configure(cursor="hand2")
                except Exception:
                    pass

    # ------------------------------------------------------------ 消息渲染
    def _bubble(self, who: str, text: str, *, source: str = "",
                followups: Optional[List[str]] = None,
                engine: str = "", note: str = "",
                report_path: str = "", report_label: str = "") -> None:
        """插入一个气泡。`who` 为 'user' 或 'agent'。"""
        if not self._alive():
            return
        holder = tk.Frame(self.stream, bg=T.BG)
        holder.pack(fill="x", padx=8, pady=4)

        is_user = (who == "user")
        # 参考图：用户消息是浅蓝药丸（右偏），智能体回答直接落在白底上、
        # 不加卡片边框 —— 加框会把长回答切成一堆格子，反而难读。
        bg = T.PRIMARY_WEAK if is_user else T.BG
        anchor = "e" if is_user else "w"
        card = tk.Frame(holder, bg=bg,
                        highlightbackground=T.BORDER if is_user else bg,
                        highlightcolor=T.BORDER if is_user else bg,
                        highlightthickness=1 if is_user else 0, bd=0)
        card.pack(side="right" if is_user else "left", fill=None,
                  anchor=anchor)

        inner = tk.Frame(card, bg=bg)
        inner.pack(padx=14 if is_user else 0, pady=10 if is_user else 6)

        head = tk.Frame(inner, bg=bg)
        head.pack(anchor="w", fill="x", pady=(0, 4))
        W.label(head, "你" if is_user else "承载力 Agent", bg=bg,
                fg=T.MUTED if not is_user else T.PRIMARY,
                font=T.F.get("small")).pack(side="left")
        if engine:
            # 标明这次答案由谁产生 —— 用户有权知道走的是大模型还是本地规则
            if engine.startswith("deepseek"):
                W.tag(head, "DeepSeek", "ok", bg=bg).pack(side="left", padx=(8, 0))
            else:
                W.tag(head, "本地规则", "gray", bg=bg).pack(
                    side="left", padx=(8, 0))

        # 正文：按可用宽度换行；保留原始换行
        self._render_text(inner, text, bg)

        if note:
            W.label(inner, note, bg=bg, fg=T.WARNING,
                    font=T.F.get("small"), wrap=760).pack(
                anchor="w", pady=(6, 0))
        if source:
            W.label(inner, "出处：" + source, bg=bg, fg=T.FAINT,
                    font=T.F.get("tiny"), wrap=760).pack(
                anchor="w", pady=(6, 0))

        # 有计算报告就給一个直接打开的按钮。
        # 用户不该为了看报告自己去翻目录找路径。
        if report_path:
            rb = tk.Frame(inner, bg=bg)
            rb.pack(anchor="w", pady=(8, 0))
            label = report_label or "打开计算报告"
            btn = W.button(rb, label, lambda p=report_path: (
                self._on_open_report(p) if self._on_open_report else None),
                kind="primary", small=True)
            btn.pack(side="left")
            W.label(rb, "  " + Path(report_path).name, bg=bg, fg=T.FAINT,
                    font=T.F.get("tiny")).pack(side="left")

        if followups:
            f = tk.Frame(inner, bg=bg)
            f.pack(anchor="w", pady=(8, 0))
            for q in followups[:4]:
                t = W.tag(f, q, "gray", bg=bg)
                t.pack(side="left", padx=(0, 6))
                for wdg in (t, t._lbl, t._box):      # type: ignore[attr-defined]
                    wdg.bind("<Button-1>", lambda e, s=q: self.ask(s))
                    try:
                        wdg.configure(cursor="hand2")
                    except Exception:
                        pass

        self._bubbles.append(holder)
        # 滚到底
        self.update_idletasks()
        self.scroll.canvas.yview_moveto(1.0)

    def _render_text(self, parent: tk.Widget, text: str, bg: str) -> None:
        """渲染正文。支持 **粗体** 与 `代码`，其余按纯文本。"""
        import re
        wrap = 720
        for para in str(text).split("\n"):
            if not para.strip():
                tk.Frame(parent, bg=bg, height=6).pack(anchor="w")
                continue
            seg_line = tk.Frame(parent, bg=bg)
            seg_line.pack(anchor="w")
            # 逐段切分 **bold** 与 `code`
            for seg in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", para):
                if not seg:
                    continue
                if seg.startswith("**") and seg.endswith("**"):
                    W.label(seg_line, seg[2:-2], bg=bg, fg=T.TEXT,
                            font=T.F.get("bold")).pack(side="left")
                elif seg.startswith("`") and seg.endswith("`"):
                    W.label(seg_line, seg[1:-1], bg=T.HEAD_BG, fg=T.TEXT,
                            font=T.F.get("mono_sm")).pack(
                        side="left", padx=2, pady=1)
                else:
                    W.label(seg_line, seg, bg=bg, fg=T.TEXT,
                            font=T.F.get("base"), wrap=wrap).pack(side="left")

    # ------------------------------------------------------------ 交互
    def _render_drop(self, r: dict) -> None:
        """把一次拖入处理的结果如实展示：导入了什么、跳过了什么。

        注意：`add_system` 是普通 Label，**不渲染 Markdown**，
        所以这里不能用 `**加粗**`（会原样显示星号）。
        """
        lines = ["已处理拖入内容", ""]
        lines.append(r.get("message") or "（无结果）")
        for it in (r.get("items") or []):
            kind = {"zip": "压缩包", "dir": "文件夹", "doc": "文档",
                    "dss": "算例"}.get(it.get("kind"), it.get("kind"))
            head = f"\n{kind}：{it.get('name')}"
            if it.get("case_dir"):
                head += f"　→ 已登记算例：{it['case_dir']}"
            lines.append(head)
            imp = it.get("imported") or []
            if imp:
                lines.append("· 入库：" + "、".join(
                    f"{x.get('name')}（{x.get('chars', 0)} 字）" for x in imp))
            bad = it.get("failed") or []
            if bad:
                lines.append("· 未入库：" + "；".join(
                    f"{x.get('name')}（{x.get('why')}）" for x in bad))
            sk = it.get("skipped") or []
            if sk:
                names = "、".join(str(x.get("name")) for x in sk[:6])
                more = f" 等 {len(sk)} 个" if len(sk) > 6 else ""
                lines.append(f"· 跳过（不可作知识）：{names}{more}")
            if it.get("case_error"):
                lines.append(f"· 登记算例失败：{it['case_error']}")
        errs = r.get("errors") or []
        if errs:
            lines.append("\n未处理：")
            for e in errs[:8]:
                lines.append(f"· {e}")
        if r.get("imported_count"):
            lines.append("\n资料已入库，可以直接问里面的内容；"
                         "「知识库」页能看到「拖入资料」这个库。")
        self.add_system("\n".join(lines))
        try:
            self.rebuild_cases()
        except Exception:
            pass

    def add_system(self, text: str) -> None:
        if not self._alive():
            return
        holder = tk.Frame(self.stream, bg=T.BG)
        holder.pack(fill="x", pady=6)
        W.label(holder, text, bg=T.BG, fg=T.FAINT,
                font=T.F.get("small"), wrap=760, anchor="center").pack(
            fill="x", padx=20)
        self._bubbles.append(holder)
        self.update_idletasks()
        self.scroll.canvas.yview_moveto(1.0)

    def ask(self, question: str) -> None:
        q = (question or "").strip()
        if not q:
            return
        # 视图可能已被销毁（用户切页/关了窗口）：此时碰控件会抛 TclError。
        # 这类"对象没了还在用"的崩溃在 Tk 里很典型，必须显式守卫。
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        try:
            self.entry.delete(0, "end")
        except Exception:
            pass
        self._history.append(q)
        self._hist_idx = len(self._history)
        self._bubble("user", q)
        # 用户消息由 ChatRuntime 负责写库（它要先落库再算，保证提问不丢）。
        # 只有在没有接 runtime 的测试场景下才由视图兜底存一次。
        if not getattr(self, "_runtime_managed", False):
            self._save("user", q)
        # 接了 runtime 的话，**计算不由视图管**：直接提交即可。
        # 旧的 _busy 闸门是单飞时代的产物，在 runtime 模式下会出事：
        # 答案投给别的会话时本视图收不到 deliver，_busy 永远不清零，
        # 之后所有提问都被卡在队列里（实测踩过）。重复提交由 runtime 按会话拒绝。
        if getattr(self, "_runtime_managed", False):
            if self._on_ask is not None:
                self._on_ask(q)
            # 立刻刷新侧栏，让 ⏳ 马上出现（poll 只作兜底）
            try:
                if self._on_message_saved:
                    self._on_message_saved()
            except Exception:
                pass
            return

        # 上一个还没答完就**排队**，不能丢弃。
        # 早先的做法是提示"请稍候"然后 return —— 用户的话再也得不到回答，
        # 看上去就像消息丢了（实测踩过）。
        self._queue.append(q)
        if self._busy:
            self.add_system(f"已排队（前面还有 {len(self._queue) - 1} 个问题）")
            return
        self._pump_queue()

    def _pump_queue(self) -> None:
        """取出队列里下一个问题去处理。"""
        if self._busy or not self._queue:
            return
        q = self._queue.pop(0)
        self._busy = True
        try:
            self.send_btn.set_enabled(False)
        except Exception:
            pass
        self.add_system("思考中…")
        self._on_ask(q)

    # ------------------------------------------------------------ 会话持久化
    def _save(self, role: str, text: str, *, engine: str = "",
              source: str = "", report_path: str = "",
              report_label: str = "") -> None:
        """把一条消息写进当前会话。

        写失败不能影响对话本身（磁盘满/权限问题都可能发生），
        所以这里只静默记录，不抛给界面。
        """
        if self._db is None or not self._session_id:
            return
        try:
            self._db.add_message(
                self._session_id, role, text, engine=engine, source=source,
                report_path=report_path, report_label=report_label)
        except Exception as exc:
            # 落库失败不影响对话，但**必须留痕**：
            # 静默失败会让"消息没保存"看起来像"功能就是这样"。
            import sys as _sys
            print(f"[chat] 消息落库失败：{type(exc).__name__}: {exc}",
                  file=_sys.stderr)
            return
        try:
            if self._on_message_saved:
                self._on_message_saved()
        except Exception:
            pass

    def set_session(self, session_id: str) -> None:
        self._session_id = session_id or ""
        # 换会话：把上一个会话的排队与忙状态清掉，别带到新会话
        try:
            self._queue.clear()
        except Exception:
            pass
        self._busy = False
        # 告诉运行时"当前显示的是这个会话"，答案算完才知道要不要推气泡
        if self._runtime is not None:
            try:
                self._runtime.bind_view(self._session_id, self)
            except Exception:
                pass

    @property
    def session_id(self) -> str:
        return self._session_id

    def clear_stream(self) -> None:
        """清空对话区（切换会话时用）。"""
        if not self._alive():
            return
        for w in self.stream.winfo_children():
            w.destroy()
        self._bubbles.clear()
        self.update_idletasks()

    def replay(self, messages) -> None:
        """把历史消息回放进对话区。

        必须带回 engine / report_path：否则历史消息会"比当时少了东西"
        （徽标没了、报告按钮没了），用户会以为记录不完整。
        """
        if not self._alive():
            return
        self.clear_stream()
        for m in messages or []:
            role = m.get("role")
            who = "user" if role == "user" else "agent"
            self._bubble(
                who, m.get("content") or "",
                source=m.get("source") or "",
                engine=m.get("engine") or "",
                report_path=m.get("report_path") or "",
                report_label=m.get("report_label") or "")
        # 回放完滚到底
        try:
            self.update_idletasks()
            self.scroll.canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _submit(self) -> None:
        self.ask(self.entry.get())

    def _recall(self, _e=None) -> None:
        if not self._history:
            return
        self._hist_idx = max(0, self._hist_idx - 1)
        self.entry.delete(0, "end")
        self.entry.insert(0, self._history[self._hist_idx])

    def deliver(self, answer: Dict[str, Any]) -> None:
        """由主线程调用：把回答放进队列（后台线程算完后用它回报）。"""
        self._pending.put(("answer", answer))

    def deliver_error(self, message: str) -> None:
        self._pending.put(("error", {"text": message}))

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def destroy(self) -> None:              # noqa: D401  (Tk 生命周期)
        """销毁前解绑运行时，避免往死控件上投答案。"""
        if self._runtime is not None:
            try:
                self._runtime.unbind_view(self)
            except Exception:
                pass
        try:
            super().destroy()
        except Exception:
            pass

    def _drain(self) -> None:
        """主线程轮询：把后台算好的回答插进界面。

        视图被销毁（切页/关窗）后必须停止轮询，否则 `after` 回调会在已销毁
        的控件上执行而抛 TclError。
        """
        if not self._alive():
            return
        try:
            while True:
                kind, payload = self._pending.get_nowait()
                if kind == "drop":
                    self._render_drop(payload)
                    continue
                if kind == "answer":
                    self._bubble("agent", payload.get("text", ""),
                                 source=payload.get("source", ""),
                                 followups=payload.get("followups"),
                                 engine=payload.get("engine", ""),
                                 note=payload.get("note", ""),
                                 report_path=payload.get("report_path", ""),
                                 report_label=payload.get("report_label", ""))
                    if not payload.get("_persisted"):
                        self._save(
                            "assistant", payload.get("text", ""),
                            engine=payload.get("engine", ""),
                            source=payload.get("source", ""),
                            report_path=payload.get("report_path", ""),
                            report_label=payload.get("report_label", ""))
                else:
                    self._bubble("agent", payload.get("text", ""),
                                 source=payload.get("source", ""),
                                 engine=payload.get("engine", ""),
                                 note=payload.get("note", ""),
                                 report_path=payload.get("report_path", ""),
                                 report_label=payload.get("report_label", ""))
                self._busy = False
                try:
                    self.send_btn.set_enabled(True)
                except Exception:
                    pass
                # 有排队的就接着答（在插入本条之后再触发，保持顺序）
                self.after(1, self._pump_queue)
                self.update_suggestions(payload.get("followups") or [])
        except queue.Empty:
            pass
        except Exception:
            # 任何渲染异常都不该让整个对话循环死掉
            pass
        try:
            self.after(120, self._drain)
        except Exception:
            pass

    def update_suggestions(self, items: List[str]) -> None:
        if items:
            self._suggestions = list(items)
            self.rebuild_chips()
