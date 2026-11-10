"""app.py — 原生桌面主窗口。

启动方式：
    python -m hc_agent.desktop          （等价 pythonw -m ... 可免控制台）

这是**原生程序**：Tk 绘制界面，计算后端是受管子进程，全程没有 HTTP 服务、
没有端口、没有浏览器引擎。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

from hc_agent import paths
from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W
from hc_agent.desktop.backend import Backend
from hc_agent.desktop.pages import PAGES, NA, fmt

APP_TITLE = "配网承载力智能体Agent"
_PROJECT_ROOT = paths.bundle_root()


def _dpi_aware() -> None:
    """声明 DPI 感知，否则高分屏上 Tk 界面会被系统拉伸成模糊的。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        _dpi_aware()
        self.title(APP_TITLE)
        self.configure(bg=T.BG)
        self.minsize(1100, 700)

        # 按屏幕比例给初始尺寸（固定值在高分屏上会偏小）
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = max(1100, min(1760, int(sw * 0.78)))
        h = max(700, min(sh - 120, int(sh * 0.84)))   # 留出状态条与任务栏
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

        T.init_fonts(self)
        T.apply(self)

        self.state: Dict[str, Any] = {
            "diagnostics": None, "channels": None, "runs": [], "detail": {},
            "latest_outcome": None,
            "input_dir": str(_PROJECT_ROOT / "tests" / "golden" / "FD002"),
            "output_dir": str(paths.runs_root() / "DESKTOP"),
        }
        self.current = "overview"
        self.page: Optional[object] = None
        self._run_busy = False
        # 深链：HC_DESKTOP_PAGE / HC_DESKTOP_SUB 直接打开某一页
        # （供自动化截图与验收使用；不影响正常交互）
        self._deep_page = os.environ.get("HC_DESKTOP_PAGE", "").strip()
        self._deep_sub = os.environ.get("HC_DESKTOP_SUB", "").strip()

        self._build_titlebar()
        self._build_statusbar()
        self._build_body()

        self._backend = Backend(on_event=self._on_backend_event,
                                root=_PROJECT_ROOT)
        self._hook_close()
        self.after(200, self._boot)
        self.after(300, self._drain_loop)
        self.after(5000, self._heartbeat)

    # ------------------------------------------------------------ 骨架
    def _build_titlebar(self) -> None:
        """不绘制自绘标题栏，改用系统原生边框。

        原实现假设窗口是 `overrideredirect(True)`，但全工程从未设置过它，
        于是屏幕上一直有**两条**标题栏（原生一条 + 自绘一条）。
        原生边框自带拖动、缩放、最小化与贴边，比自绘可靠，因此这里只保留
        原生标题，把「折叠导航」入口移进侧栏。

        保留该方法名与 `self.title_lbl`，避免调用方与测试失效。
        """
        self.title_lbl = None
        self._drag = (0, 0)

    def _drag_start(self, e) -> None:
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e) -> None:
        # `_maxed` 必须先初始化：此前只有 `_toggle_max` 里用 getattr 兜底，
        # 而这里直接读属性，于是"先拖标题栏后最大化"会抛 AttributeError。
        if getattr(self, "_maxed", False):
            return
        self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _toggle_max(self) -> None:
        self._maxed = not getattr(self, "_maxed", False)
        if self._maxed:
            self._restore_geo = self.geometry()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry(f"{sw}x{sh}+0+0")
        else:
            self.geometry(getattr(self, "_restore_geo", "1200x800+80+60"))

    def _load_license(self) -> None:
        """启动时校验本地授权；失败不阻断启动，回落演示模式。"""
        try:
            from hc_agent import license as lic
            st = lic.status()
            self.state["license"] = st
            self.state["license_text"] = lic.banner(st)
        except Exception:
            self.state["license"] = None
            self.state["license_text"] = "授权：状态未知"

    def _build_statusbar(self) -> None:
        """30px 状态条：白底 + 1px 顶边，字号压到 small，不与内容抢注意力。"""
        tk.Frame(self, bg=T.BORDER, height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=T.CARD, height=T.STATUSBAR_H)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.crumb = tk.Label(bar, text="", bg=T.CARD, fg=T.MUTED,
                              font=T.F.get("small"))
        self.crumb.pack(side="left", padx=(16, 12))
        self._load_license()
        lic_lb = tk.Label(bar, text=self.state.get("license_text", "授权：状态未知"),
                          bg=T.CARD, fg=(T.MUTED if getattr(self.state.get("license"), "ok", False)
                                         else "#864B48"),
                          font=T.F.get("small"))
        lic_lb.pack(side="right", padx=(6, 16))
        self.license_label = lic_lb
        self.status_items: Dict[str, tuple] = {}
        for key, text in (("backend", "后端检查中…"), ("engine", "引擎检查中…"),
                          ("watchdog", "看门狗检查中…")):
            dot = W.Dot(bar, "gray", bg=T.CARD)
            dot.pack(side="left", padx=(10, 3))
            lb = tk.Label(bar, text=text, bg=T.CARD, fg=T.MUTED,
                          font=T.F.get("small"))
            lb.pack(side="left")
            self.status_items[key] = (dot, lb)
        self.run_count = tk.Label(bar, text="运行记录 0", bg=T.CARD, fg=T.MUTED,
                                  font=T.F.get("small"))
        self.run_count.pack(side="right", padx=16)

    def _build_body(self) -> None:
        """两级侧栏：一级导航列 + 工作区列，都是近白底，靠 1px 边框分界。"""
        body = tk.Frame(self, bg=T.NAV_BG)
        body.pack(fill="both", expand=True)

        self.nav = tk.Frame(body, bg=T.NAV_BG, width=T.NAV_W)
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)

        # ---- 品牌：粗体主名 + 浅色副名（参考图的双色标题）
        brand = tk.Frame(self.nav, bg=T.NAV_BG)
        brand.pack(fill="x", padx=14, pady=(14, 12))
        row0 = tk.Frame(brand, bg=T.NAV_BG)
        row0.pack(anchor="w")
        tk.Label(row0, text="天智五号", bg=T.NAV_BG, fg=T.TEXT,
                 font=T.F.get("brand")).pack(side="left")
        tk.Label(row0, text="配电网智能体", bg=T.NAV_BG, fg=T.MUTED,
                 font=T.F.get("brand_sub")).pack(side="left", padx=(6, 0),
                                                 pady=(7, 0))
        tog = tk.Label(brand, text="☰", bg=T.NAV_BG, fg=T.FAINT,
                       font=(T.F.get("fam", "TkDefaultFont"), 11),
                       padx=4, cursor="hand2")
        tog.pack(side="right")
        tog.bind("<Button-1>", lambda e: self._toggle_nav())
        tog.bind("<Enter>", lambda e: tog.configure(bg=T.NAV_HOVER, fg=T.TEXT))
        tog.bind("<Leave>", lambda e: tog.configure(bg=T.NAV_BG, fg=T.FAINT))

        # ---- 「新会话」药丸按钮（白底 + 1px 边框，参考图同款）
        pill = tk.Frame(self.nav, bg=T.PILL_BG,
                        highlightbackground=T.PILL_BORDER,
                        highlightthickness=1, cursor="hand2")
        pill.pack(fill="x", padx=14, pady=(0, 14))
        pv = tk.Label(pill, text="＋   新会话", bg=T.PILL_BG, fg=T.TEXT,
                      font=T.F.get("bold"), pady=7, cursor="hand2")
        pv.pack(fill="x")
        for w in (pill, pv):
            w.bind("<Button-1>", lambda e: self._new_session())
            w.bind("<Enter>", lambda e: (pill.configure(bg=T.NAV_HOVER),
                                         pv.configure(bg=T.NAV_HOVER)))
            w.bind("<Leave>", lambda e: (pill.configure(bg=T.PILL_BG),
                                         pv.configure(bg=T.PILL_BG)))

        # ---- 一级导航（图标 + 文字，行高 34，选中是浅灰不是蓝底）
        ICONS = {"chat": "◌", "overview": "◈", "workbench": "▣",
                 "process": "◷", "dss": "◫", "results": "▤",
                 "kb": "▦", "settings": "⚙"}
        self.nav_items: Dict[str, tk.Label] = {}
        # 「问答」放在最前：这是最自然的入口 —— 大多数用户先想问个问题，
        # 而不是先看状态面板。
        for key, label in (("chat", "问答"), ("overview", "运行总览"),
                           ("workbench", "容量分析"), ("process", "运算过程"),
                           ("dss", "算例分析"), ("results", "结果与证据")):
            lb = tk.Label(self.nav, text=f"  {ICONS[key]}   {label}",
                          bg=T.NAV_BG, fg=T.NAV_TEXT, font=T.F.get("nav"),
                          anchor="w", padx=10, pady=7, cursor="hand2")
            lb.pack(fill="x", padx=8, pady=1)
            lb.bind("<Button-1>", lambda e, k=key: self.show(k))
            lb.bind("<Enter>", lambda e, x=lb, k=key: self._nav_enter(x, k))
            lb.bind("<Leave>", lambda e, x=lb, k=key: self._nav_leave(x, k))
            self.nav_items[key] = lb

        # ---- 底部固定项（知识库 / 配置），与参考图底部的入口一致
        self.bottom_items: Dict[str, tk.Label] = {}
        for key, label in (("settings", "配置"), ("kb", "知识库")):
            lb = tk.Label(self.nav, text=f"  {ICONS[key]}   {label}",
                          bg=T.NAV_BG, fg=T.NAV_TEXT, font=T.F.get("nav"),
                          anchor="w", padx=10, pady=7, cursor="hand2")
            lb.pack(side="bottom", fill="x", padx=8, pady=(1, 1))
            lb.bind("<Button-1>", lambda e, k=key: self.show(k))
            lb.bind("<Enter>", lambda e, x=lb, k=key: self._nav_enter(x, k))
            lb.bind("<Leave>", lambda e, x=lb, k=key: self._nav_leave(x, k))
            self.bottom_items[key] = lb
        tk.Frame(self.nav, bg=T.BORDER, height=1).pack(side="bottom", fill="x",
                                                       padx=14, pady=(10, 8))

        # ---- 工作区列（子导航 / 会话）
        self.subnav = tk.Frame(body, bg=T.SUBNAV_BG, width=T.SUBNAV_W)
        self.subnav.pack(side="left", fill="y")
        self.subnav.pack_propagate(False)
        self._ws_head = tk.Frame(self.subnav, bg=T.SUBNAV_BG)
        self._ws_head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(self._ws_head, text="工作区", bg=T.SUBNAV_BG, fg=T.MUTED,
                 font=T.F.get("small")).pack(side="left")
        self._ws_rows = tk.Frame(self.subnav, bg=T.SUBNAV_BG)
        self._ws_rows.pack(fill="both", expand=True)
        # 工作区列右缘的 1px 分隔线（与内容区之间）
        tk.Frame(self.subnav, bg=T.BORDER, width=1).place(relx=1.0, rely=0,
                                                          relheight=1.0,
                                                          anchor="ne")

        self.content = tk.Frame(body, bg=T.BG)
        self.content.pack(side="left", fill="both", expand=True)
        self.scroll = W.ScrollArea(self.content, bg=T.BG)
        self.scroll.pack(fill="both", expand=True)
        self.pagebody = tk.Frame(self.scroll.body, bg=T.BG)
        self.pagebody.pack(fill="both", expand=True, padx=24, pady=20)

    def _nav_enter(self, wdg, key) -> None:
        if key != self.current:
            wdg.configure(bg=T.NAV_HOVER, fg=T.TEXT)

    def _nav_leave(self, wdg, key) -> None:
        active = (key == self.current)
        wdg.configure(bg=T.NAV_ACTIVE if active else T.NAV_BG,
                      fg=T.TEXT if active else T.NAV_TEXT)

    def _new_session(self) -> None:
        """「新会话」：切到问答页并开一段干净对话。"""
        self.show("chat")
        page = self.page
        if page is not None and hasattr(page, "new_session"):
            try:
                page.new_session()
            except Exception:
                pass

    def _toggle_nav(self) -> None:
        if self.nav.winfo_ismapped():
            self.nav.pack_forget()
            self.subnav.pack_forget()
        else:
            self.nav.pack(side="left", fill="y", before=self.content)
            if self._subnav_visible():
                self.subnav.pack(side="left", fill="y", before=self.content)

    def _subnav_visible(self) -> bool:
        return bool(getattr(getattr(self, "page", None), "SUBS", []))

    # ------------------------------------------------------------ 页面切换
    def _license_gate(self, key: str) -> bool:
        """演示模式下阻止进入需授权的页面；返回 True 表示放行。"""
        from tkinter import messagebox
        messagebox.showinfo(
            "需要授权",
            str(self.state.get("license_text", "")) +
            "\n\n容量分析与容量搜索需要有效授权。\n"
            "请把本机机器码发给供方换取授权文件 lic.json。",
            parent=self)
        return False

    def show(self, key: str, sub: str = "") -> None:
        self.current = key
        for k, lb in list(self.nav_items.items()) + list(
                getattr(self, "bottom_items", {}).items()):
            self._nav_leave(lb, k)
        # 演示模式：容量分析与容量搜索锁定
        _lic = self.state.get("license")
        if key in ("workbench",) and _lic is not None and not _lic.ok:
            if not self._license_gate(key):
                return
        cls = PAGES[key]
        self.page = cls(self)
        if sub:
            self.page.sub = sub
        self._render_subnav()
        self._render_page()

    def _render_subnav(self) -> None:
        for w in self._ws_rows.winfo_children():
            w.destroy()
        subs = list(getattr(self.page, "SUBS", []))
        # 没有子导航的页面（问答 / 算例分析）不占工作区列，把宽度还给内容
        if not subs:
            if self.subnav.winfo_ismapped():
                self.subnav.pack_forget()
            return
        if not self.subnav.winfo_ismapped() and self.nav.winfo_ismapped():
            self.subnav.pack(side="left", fill="y", before=self.content)
        for key, label in subs:
            active = (self.page.sub == key)
            lb = tk.Label(self._ws_rows, text=label,
                          bg=T.NAV_ACTIVE if active else T.SUBNAV_BG,
                          fg=T.TEXT if active else T.NAV_TEXT,
                          font=T.F.get("bold") if active else T.F.get("base"),
                          anchor="w", padx=12, pady=7, cursor="hand2")
            lb.pack(fill="x", padx=8, pady=1)
            lb.bind("<Button-1>", lambda e, k=key: self.show_sub(k))
            if not active:
                lb.bind("<Enter>", lambda e, x=lb: x.configure(bg=T.NAV_HOVER))
                lb.bind("<Leave>", lambda e, x=lb: x.configure(
                    bg=T.SUBNAV_BG))

    def show_sub(self, key: str) -> None:
        self.page.sub = key
        self._render_subnav()
        self._render_page()
    def _render_page(self) -> None:
        if self.page is None:
            return
        for w in self.pagebody.winfo_children():
            w.destroy()
        subs = list(getattr(self.page, "SUBS", []))
        sub_lbl = next((lbl for k, lbl in subs if k == self.page.sub), "")
        self.crumb.configure(text=f"{self.page.title} / {sub_lbl}"
                             if sub_lbl else self.page.title)
        # 标题栏中间只放当前页面名（参考图没有大标题，避免与内容抢视线）
        if getattr(self, "title_lbl", None) is not None:
            try:
                self.title_lbl.configure(text=self.page.title)
            except Exception:
                pass
        # 对话页要**占满可视区**，不能塞进滚动容器里：
        # pagebody 高度跟随内容（实测只有 377px），而对话需要撑满整个视口
        # 才有"对话感"，且它自己内部已经有滚动条。
        fullscreen = bool(getattr(self.page, "FULLSCREEN", False))
        host = self.content if fullscreen else self.pagebody
        if fullscreen:
            self.scroll.pack_forget()
            if not getattr(self, "_chat_host", None):
                self._chat_host = tk.Frame(self.content, bg=T.BG)
            self._chat_host.pack(fill="both", expand=True)
            for w in self._chat_host.winfo_children():
                w.destroy()
            host = self._chat_host
        else:
            if getattr(self, "_chat_host", None):
                self._chat_host.pack_forget()
            if not self.scroll.winfo_ismapped():
                self.scroll.pack(fill="both", expand=True)
        try:
            self.page.render(host)
        except Exception as exc:
            import traceback
            W.notice(host, f"渲染失败：{type(exc).__name__}: {exc}",
                     "err")
            traceback.print_exc()
        if not fullscreen:
            self.scroll.canvas.yview_moveto(0)

    # ------------------------------------------------------------ 报告
    def open_report(self, run_id: str) -> None:
        """用系统默认程序打开该次运行的计算报告（Word / HTML）。

        只允许打开运行目录内的文件；不允许时如实提示而不是静默失败。
        """
        try:
            from hc_agent.desktop import runs as runs_mod
            r = runs_mod.open_report(run_id)
        except Exception as exc:
            r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if r.get("ok"):
            self._toast(f"已打开报告：{r.get('name')}")
        else:
            msg = str(r.get("error") or "未知错误")
            if r.get("path"):
                msg += f"\n可手动打开：{r['path']}"
            messagebox.showwarning("打开报告失败", msg, parent=self)

    def open_report_path(self, path: str) -> None:
        """打开一个计算报告文件（问答气泡与运行记录页共用）。

        安全边界：只允许打开**确实存在**的、扩展名在白名单里的文件。
        路径来自本工程的分析/运行产物，但仍要校验 ——
        不能让模型输出的一段文本变成"打开任意文件"的能力。
        """
        try:
            p = Path(str(path)).expanduser().resolve()
        except Exception:
            messagebox.showwarning("打开失败", f"路径无效：{path}", parent=self)
            return
        if not p.is_file():
            messagebox.showwarning("打开失败",
                                   f"文件不存在：\n{p}", parent=self)
            return
        allowed = {".docx", ".md", ".html", ".htm", ".pdf", ".txt", ".csv"}
        if p.suffix.lower() not in allowed:
            messagebox.showwarning(
                "打开失败",
                f"出于安全考虑，只打开报告类文件"
                f"（{'、'.join(sorted(allowed))}）。\n实际：{p.suffix}",
                parent=self)
            return
        try:
            os.startfile(str(p))
            self._toast(f"已打开报告：{p.name}")
        except Exception as exc:
            messagebox.showwarning("打开失败",
                                   f"{type(exc).__name__}: {exc}\n\n{p}",
                                   parent=self)

    def _toast(self, text: str) -> None:
        """轻量提示：状态栏右侧临时显示，不弹窗打断。"""
        try:
            self.run_count.configure(text=str(text), fg=T.SUCCESS)
            self.after(4000, lambda: self.run_count.configure(
                text=f"运行记录 {len(self.state.get('runs') or [])}",
                fg=T.MUTED))
        except Exception:
            pass

    # ------------------------------------------------------------ 问答
    # ------------------------------------------------------------ 问答运行时
    def chat_history(self):
        """对话历史库（懒加载，全局一份）。"""
        if getattr(self, "_chat_history", None) is None:
            from hc_agent.chat_history import ChatHistory
            try:
                self._chat_history = ChatHistory()
            except Exception:
                self._chat_history = None
        return self._chat_history

    def chat_runtime(self):
        """问答运行时：按会话 id 在后台算，切页/切会话都不丢。"""
        if getattr(self, "_chat_runtime", None) is None:
            from hc_agent.desktop.chat_runtime import ChatRuntime
            self._chat_runtime = ChatRuntime(self.chat_history)
        return self._chat_runtime

    def _current_session_id(self) -> str:
        page = getattr(self, "page", None)
        sid = getattr(page, "session_id", "") if page is not None else ""
        if sid:
            return str(sid)
        view = getattr(self, "chat_view", None)
        return str(getattr(view, "session_id", "") or "")

    def ask_question(self, question: str) -> None:
        """处理用户提问。

        真正的计算在 `ChatRuntime` 里、按会话 id 进行：
        即使提问后马上切到别的页面或别的会话，答案也会算完并写进
        **提问时那个会话**，切回来就能看到。
        """
        q = (question or "").strip()
        if not q:
            return
        rt = self.chat_runtime()
        sid = self._current_session_id()
        if not sid:
            # 还没进过对话页（例如从「结果与证据」直接提问）：先开一个会话
            hist = self.chat_history()
            if hist is None:
                return
            try:
                sid = (hist.ensure_session() or {}).get("id", "")
            except Exception:
                sid = ""
        if not sid:
            return
        rt.submit(sid, q)

    # ------------------------------------------------------------ 后端
    def _boot(self) -> None:
        # 排障开关：设 HC_TRACE=1 才写启动追踪（装好后用户不需要这些日志）
        if os.environ.get("HC_TRACE"):
            try:
                from hc_agent import paths
                paths.ensure_writable(paths.logs_root())
                with open(paths.logs_root() / "gui-trace.log", "a",
                          encoding="utf-8") as fh:
                    import time as _t
                    fh.write(f"{_t.strftime('%H:%M:%S')} _boot 被调用\n")
            except Exception:
                pass
        self._backend.start()
        # **必须保证至少显示一页**：此前只在深链存在时调用 show()，正常启动时
        # self.page 一直为 None，内容区全空白（只剩导航和状态栏）。
        #
        # 但**不能无条件重设**：如果外部已经 show() 过（深链、自动化演示），
        # 覆盖会把用户/测试打开的页面又踢回总览。所以只在尚未建页时才走默认。
        if self.page is None:
            self.show(self._deep_page or "chat", self._deep_sub)
        self.after(3000, self._load_all)

    def _on_backend_event(self, event: str, payload: dict) -> None:
        """后端事件在**工作线程**里触发；Tk 不是线程安全的，必须回主线程。"""
        self.after(0, lambda: self._handle_event(event, payload))

    def _handle_event(self, event: str, payload: dict) -> None:
        if event == "ready":
            self._set_status("backend", "后端正常", "ok")
            ch = payload.get("channel") or ""
            if ch:
                self._set_status("engine", f"引擎就绪（{ch}）", "ok")
        elif event == "starting":
            self._set_status("backend", "后端启动中…", "warn")
        elif event == "restarting":
            self._set_status("backend", "后端重启中…", "warn")
            if self.app_progress_alive():
                self.progress.configure(
                    text=str(payload.get("message", "")), fg=T.WARNING)
        elif event == "stopped":
            self._set_status("backend", "后端已停止", "gray")
        elif event == "error":
            self._set_status("backend", "后端异常", "err")
            msg = str(payload.get("message", ""))
            if payload.get("fatal"):
                messagebox.showerror("后端反复退出", msg, parent=self)
            elif self.app_progress_alive():
                self.progress.configure(text=msg, fg=T.DANGER)
        elif event == "log":
            pass          # 后端日志已在子进程的 stderr 上，界面不刷屏

    def app_progress_alive(self) -> bool:
        p = getattr(self, "progress", None)
        try:
            return bool(p and p.winfo_exists())
        except Exception:
            return False

    def _set_status(self, key: str, text: str, kind: str) -> None:
        item = self.status_items.get(key)
        if not item:
            return
        dot, lb = item
        dot.set_kind(kind)
        lb.configure(text=text)

    def _load_all(self) -> None:
        """拉取诊断 / 通道 / 运行列表，并**自动载入最近一次运行的明细**。

        明细必须一起载入：否则「结果与证据」页永远拿不到 summary，
        口径声明/门禁/复算全部显示为空 —— 明明有产物却看起来什么都没有
        （实测踩过）。
        """
        self._backend.call_async("diagnostics", on_done=self._diag_cb)
        self._backend.call_async("channels", on_done=self._chan_cb)
        self._backend.call_async("runs", on_done=self._runs_cb)

    def _autoload_detail(self) -> None:
        """挑一次**有结论**的运行载入明细；已有明细且用户没换运行时不覆盖。"""
        runs = self.state.get("runs") or []
        if not runs:
            return
        if self.state.get("detail"):
            return
        pick = next((r for r in runs if r.get("exit_code") is not None), runs[0])
        rid = pick.get("run_id")
        if not rid:
            return
        self.state["detail_run"] = rid
        self._backend.call_async("run_detail", {"run_id": rid},
                                 on_done=self._detail_cb)

    def _detail_cb(self, msg: dict) -> None:
        if msg.get("ok"):
            self.state["detail"] = msg.get("detail") or {}
            self._maybe_rerender()

    def _diag_cb(self, msg: dict) -> None:
        if msg.get("ok"):
            d = msg.get("diagnostics") or {}
            self.state["diagnostics"] = d
            self.state["latest_outcome"] = d.get("run_outcome") or {}
            wd = d.get("watchdog") or {}
            self._set_status("watchdog",
                             "看门狗运行中" if wd.get("started") else "看门狗未启动",
                             "ok" if wd.get("started") else "warn")
            cl = d.get("crashlog") or {}
            self._set_status("crashlog",
                             "崩溃日志已装" if cl.get("installed") else "崩溃日志未装",
                             "ok" if cl.get("installed") else "gray")
        self._maybe_rerender()

    def _chan_cb(self, msg: dict) -> None:
        if msg.get("ok"):
            self.state["channels"] = msg
        self._maybe_rerender()

    def _runs_cb(self, msg: dict) -> None:
        if msg.get("ok"):
            self.state["runs"] = msg.get("runs") or []
            self.run_count.configure(text=f"运行记录 {len(self.state['runs'])}")
            self._autoload_detail()
        self._maybe_rerender()

    def _maybe_rerender(self) -> None:
        """后端数据到达后按需重绘当前页。

        **有状态的页面必须跳过**：`_render_page()` 会销毁并重建整页。
        问答页的对话内容、算例分析页的结果、知识库页的选中项都在页面对象里，
        整页重建就等于把这些内容清空 —— 实测表现是"提问后消息突然消失"，
        因为后台的 diagnostics/runs 回调在启动后几秒才返回并触发了重绘。
        这些页面自己管刷新，不需要外部重建。
        """
        if self.state.get("diagnostics") is None:
            return
        if getattr(self.page, "STATEFUL", False):
            return
        self._render_page()

    # ------------------------------------------------------------ 操作
    def open_run(self, run_id: str) -> None:
        def cb(msg):
            if msg.get("ok"):
                self.state["detail"] = msg.get("detail") or {}
                self.state["detail_run"] = run_id
                self.show("results", "result")
            else:
                messagebox.showwarning("读取失败", str(msg.get("error")), parent=self)
        self._backend.call_async("run_detail", {"run_id": run_id}, on_done=cb)

    def pick_input(self) -> None:
        d = filedialog.askdirectory(title="选择输入包目录", parent=self)
        if d and getattr(self, "input_entry", None):
            self.input_entry.delete(0, "end")
            self.input_entry.insert(0, d)
            self.state["input_dir"] = d

    def pick_output(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录", parent=self)
        if d and getattr(self, "output_entry", None):
            self.output_entry.delete(0, "end")
            self.output_entry.insert(0, d)
            self.state["output_dir"] = d

    def start_task(self) -> None:
        if self._run_busy:
            return
        inp = self.input_entry.get().strip() if getattr(self, "input_entry", None) else ""
        out = self.output_entry.get().strip() if getattr(self, "output_entry", None) else ""
        if not inp or not Path(inp).is_dir():
            messagebox.showwarning("输入无效", f"输入目录不存在：\n{inp}", parent=self)
            return
        if not out:
            messagebox.showwarning("输出无效", "请选择输出目录", parent=self)
            return
        self._run_busy = True
        self.state["input_dir"], self.state["output_dir"] = inp, out
        if getattr(self, "run_btn", None):
            self.run_btn.set_enabled(False)
        if self.app_progress_alive():
            self.progress.configure(text="正在执行评估…", fg=T.PRIMARY)

        def cb(msg):
            self._run_busy = False
            if getattr(self, "run_btn", None):
                self.run_btn.set_enabled(True)
            if not msg.get("ok"):
                if self.app_progress_alive():
                    self.progress.configure(
                        text=f"评估失败：{msg.get('error') or msg.get('error')}", fg=T.DANGER)
                messagebox.showerror("评估失败", str(msg.get("error")), parent=self)
                return
            oc = msg.get("outcome") or {}
            # 记住这次跑到哪去了：容量分析页要**当场**给出报告入口，
            # 不该让用户再跑去「运行记录」页找（实测这是最常被抱怨的一步）。
            self.state["last_run_id"] = str(msg.get("run_id") or "")
            self.state["last_run_dir"] = str(msg.get("output_dir") or "")
            if self.app_progress_alive():
                code = oc.get("exit_code")
                note = {0: "成功", 2: "输入阻断", 3: "计算失败",
                        4: "复算失败", 5: "未满足发布门禁",
                        6: "被中断"}.get(code, str(code))
                self.progress.configure(
                    text=f"评估完成：退出码 {code}（{note}）", fg=T.SUCCESS)
            self._load_all()
            # 重画当前页，让"本次运行结果"区块出现（该页无状态，可安全重建）
            try:
                self._render_page()
            except Exception:
                pass

        self._backend.call_async("run_task", {"input_dir": inp, "output_dir": out},
                                 on_done=cb, timeout=3600.0)

    # ------------------------------------------------------------ 循环/收尾
    # ------------------------------------------------------------ 拖放
    def _attach_drop(self) -> None:
        """挂上系统拖放。失败只降级，不影响其它功能。"""
        if getattr(self, "_drop", None) is not None:
            return
        try:
            from hc_agent.desktop.dnd import DropTarget
            self._drop = DropTarget(self, self._on_dropped)
            ok = self._drop.attach()
            # 拖放不可用时，界面上的「选择文件…」仍可用
            self._drop_ok = bool(ok)
            if not ok:
                self._drop_err = getattr(self._drop, "error", "")
        except Exception as exc:
            self._drop = None
            self._drop_ok = False
            self._drop_err = f"{type(exc).__name__}: {exc}"
        self.after(400, self._poll_drops)

    def _poll_drops(self) -> None:
        """主线程轮询已投放的路径。

        为什么不在窗口过程里直接处理：那里做耗时工作会卡住消息循环，
        而且 Tk 控件只能在主线程正常操作。
        """
        try:
            if getattr(self, "_drop", None) is not None:
                for group in self._drop.poll():
                    self._on_dropped(group)
        except Exception:
            pass
        try:
            self.after(400, self._poll_drops)
        except Exception:
            pass

    def _on_dropped(self, paths) -> None:
        """把拖进来的路径交给当前页；不在对话页就切过去。"""
        try:
            page = getattr(self, "page", None)
            handler = getattr(page, "handle_drop", None)
            if handler is None:
                self.show("chat")
                self.update_idletasks()
                page = getattr(self, "page", None)
                handler = getattr(page, "handle_drop", None)
            if handler is None:
                self._toast("当前页不支持拖入")
                return
            handler(list(paths))
        except Exception as exc:
            try:
                self._toast(f"处理拖入内容失败：{exc}")
            except Exception:
                pass

    def _drain_loop(self) -> None:
        """主线程轮询已完成的后端调用并执行回调。"""
        try:
            self._backend.drain()
        except Exception:
            pass
        self.after(120, self._drain_loop)

    def _heartbeat(self) -> None:
        """状态栏心跳（不重绘页面主体，避免打断阅读）。"""
        def cb(msg):
            if not msg.get("ok"):
                self._set_status("backend", "后端未连接", "err")
                return
            self._set_status("backend", "后端正常", "ok")
            wd = (msg.get("diagnostics") or {}).get("watchdog") or {}
            self._set_status("watchdog",
                             "看门狗运行中" if wd.get("started") else "看门狗未启动",
                             "ok" if wd.get("started") else "warn")
        self._backend.call_async("diagnostics", on_done=cb)
        self.after(5000, self._heartbeat)

    def _hook_close(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F5>", lambda e: self._load_all())

    def _on_close(self) -> None:
        try:
            self._backend.stop()
        except Exception:
            pass
        self.destroy()


def main(argv: Optional[List[str]] = None) -> int:
    """GUI 入口。

    **打包后（windowed）stderr 不可见**，任何启动期异常都会表现为"窗口出现了
    但内容永远是未获取"，毫无线索。因此启动过程与异常都写文件来自证。
    """
    import traceback

    def _trace(msg: str) -> None:
        if not os.environ.get("HC_TRACE"):
            return
        try:
            from hc_agent import paths
            paths.ensure_writable(paths.logs_root())
            with open(paths.logs_root() / "gui-trace.log", "a",
                      encoding="utf-8") as fh:
                import time as _t
                fh.write(f"{_t.strftime('%H:%M:%S')} {msg}\n")
        except Exception:
            pass

    _trace(f"main() 进入 frozen={getattr(sys, 'frozen', False)}")
    try:
        from hc_agent.runtime import crashlog
        crashlog.install()
        _trace("crashlog 已安装")
    except Exception:
        _trace("crashlog 安装失败:\n" + traceback.format_exc()[-600:])
    try:
        app = App()
        _trace("App 构造完成，进入 mainloop")
    except Exception:
        _trace("App 构造失败:\n" + traceback.format_exc()[-2000:])
        raise
    try:
        app.mainloop()
        _trace("mainloop 结束")
    except Exception:
        _trace("mainloop 异常:\n" + traceback.format_exc()[-2000:])
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
