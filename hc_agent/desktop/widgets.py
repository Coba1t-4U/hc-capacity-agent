"""widgets.py — 原生 Tk 组件库。

Tk 没有圆角和阴影，所以层次感靠「白底卡片 + 1px 极浅描边 + 留白」建立。
所有组件都用 `tk` 原生控件（Frame/Label/Canvas），**不用任何 HTML 渲染**。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Iterable, Optional, Sequence

from hc_agent.desktop import theme as T


# ------------------------------------------------------------------ 基础容器
def frame(parent, bg: str = T.BG, **kw) -> tk.Frame:
    return tk.Frame(parent, bg=bg, **kw)


def card(parent, *, pad: int = 16, bg: str = T.CARD) -> tk.Frame:
    """白底卡片：1px 浅描边代替阴影。"""
    outer = tk.Frame(parent, bg=T.CARD, highlightbackground=T.BORDER,
                     highlightcolor=T.BORDER, highlightthickness=1,
                     bd=0)
    inner = tk.Frame(outer, bg=bg, bd=0)
    inner.pack(fill="both", expand=True, padx=pad, pady=pad)
    outer.inner = inner              # 便于调用方直接往内容区塞东西
    return outer


def hline(parent, bg: str = T.BORDER) -> tk.Frame:
    f = tk.Frame(parent, bg=bg, height=1)
    f.pack(fill="x")
    return f


# ------------------------------------------------------------------ 文本
def label(parent, text: str = "", *, bg: str = T.CARD, fg: str = T.TEXT,
          font=None, anchor: str = "w", wrap: int = 0, **kw) -> tk.Label:
    lb = tk.Label(parent, text=text, bg=bg, fg=fg,
                  font=font or T.F.get("base"), anchor=anchor,
                  justify="left" if anchor == "w" else "center", **kw)
    if wrap:
        lb.configure(wraplength=wrap)
    return lb


def section_title(parent, text: str, *, bg: str = T.BG) -> tk.Label:
    lb = label(parent, text, bg=bg, fg=T.TEXT, font=T.F.get("h2"), anchor="w")
    lb.pack(fill="x", pady=(18, 8))
    return lb


# ------------------------------------------------------------------ 标签徽章
class Tag(tk.Frame):
    """圆角无法实现，改用「浅底 + 语义色文字 + 左侧圆点」表达状态。"""

    def __init__(self, parent, text: str = "", kind: str = "gray",
                 bg_parent: str = T.CARD, dot: bool = False):
        super().__init__(parent, bg=bg_parent, bd=0)
        tbg, tfg = T.TAG_COLORS.get(kind, T.TAG_COLORS["gray"])
        self._box = tk.Frame(self, bg=tbg, bd=0)
        self._box.pack()
        self._dot = None
        if dot:
            self._dot = tk.Label(self._box, text="●", bg=tbg, fg=tfg,
                                 font=(T.F.get("fam", "TkDefaultFont"), 6))
            self._dot.pack(side="left", padx=(7, 0), pady=2)
        self._lbl = tk.Label(self._box, text=text, bg=tbg, fg=tfg,
                             font=T.F.get("small"), padx=8, pady=2)
        self._lbl.pack(side="left")

    def set(self, text: str, kind: str = "gray") -> None:
        tbg, tfg = T.TAG_COLORS.get(kind, T.TAG_COLORS["gray"])
        self._box.configure(bg=tbg)
        self._lbl.configure(text=text, bg=tbg, fg=tfg)
        if self._dot:
            self._dot.configure(bg=tbg, fg=tfg)


def tag(parent, text: str, kind: str = "gray", *, bg: str = T.CARD,
        dot: bool = False) -> Tag:
    return Tag(parent, text, kind, bg, dot)


# ------------------------------------------------------------------ 按钮
class Button(tk.Label):
    """用 Label 实现按钮：Tk 原生 Button 在 Windows 上无法设置字体色与底色
    组合（会有系统灰边），自绘才能保持设计一致。"""

    def __init__(self, parent, text: str, command: Optional[Callable] = None,
                 *, kind: str = "default", bg_parent: str = T.CARD,
                 small: bool = False):
        self._kind = kind
        self._cmd = command
        self._enabled = True
        self._palette = self._colors(kind)
        super().__init__(parent, text=text, bd=0, padx=12 if small else 16,
                         pady=4 if small else 6,
                         font=T.F.get("small") if small else T.F.get("base"),
                         cursor="hand2", **self._palette)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    @staticmethod
    def _colors(kind: str) -> dict:
        if kind == "primary":
            return {"bg": T.PRIMARY, "fg": "#FFFFFF"}
        if kind == "danger":
            return {"bg": T.CARD, "fg": T.DANGER}
        return {"bg": T.CARD, "fg": T.TEXT}

    def _on_enter(self, _e=None):
        if not self._enabled:
            return
        if self._kind == "primary":
            self.configure(bg=T.PRIMARY_HOVER)
        elif self._kind == "danger":
            self.configure(bg=T.DANGER_BG)
        else:
            self.configure(bg=T.HEAD_BG)

    def _on_leave(self, _e=None):
        if not self._enabled:
            return
        self.configure(**self._palette)

    def _on_click(self, _e=None):
        if self._enabled and self._cmd:
            self._cmd()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        if on:
            self.configure(cursor="hand2", **self._palette)
        else:
            self.configure(cursor="", bg=T.CARD, fg=T.FAINT)


def button(parent, text: str, command=None, *, kind: str = "default",
           bg: str = T.CARD, small: bool = False) -> Button:
    return Button(parent, text, command, kind=kind, bg_parent=bg, small=small)


# ------------------------------------------------------------------ 状态点
class Dot(tk.Label):
    def __init__(self, parent, kind: str = "gray", bg: str = T.CARD):
        super().__init__(parent, text="●", bg=bg,
                         font=(T.F.get("fam", "TkDefaultFont"), 7))
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        self.configure(fg={"ok": T.SUCCESS, "warn": T.WARNING,
                           "err": T.DANGER}.get(kind, T.FAINT))


# ------------------------------------------------------------------ KPI 卡片
def kpi(parent, label_text: str, value: str, *, tag_text: str = "",
        tag_kind: str = "gray", foot: str = "", width: int = 0) -> tk.Frame:
    outer = card(parent, pad=14)
    inner = outer.inner
    label(inner, label_text, bg=T.CARD, fg=T.MUTED, font=T.F.get("small")).pack(anchor="w")
    label(inner, value, bg=T.CARD, fg=T.TEXT, font=T.F.get("kpi")).pack(anchor="w", pady=(2, 0))
    if tag_text:
        t = tag(inner, tag_text, tag_kind, bg=T.CARD, dot=True)
        t.pack(anchor="w", pady=(6, 0))
    if foot:
        label(inner, foot, bg=T.CARD, fg=T.MUTED,
              font=T.F.get("small"), wrap=260).pack(anchor="w", pady=(6, 0))
    if width:
        outer.configure(width=width)
        outer.pack_propagate(False)
    return outer


# ------------------------------------------------------------------ 键值行
def kv_row(parent, k: str, v: str, *, mono: bool = False,
           bg: str = T.CARD, value_fg: str = T.TEXT) -> tk.Frame:
    row = tk.Frame(parent, bg=bg, bd=0)
    row.pack(fill="x")
    label(row, k, bg=bg, fg=T.MUTED, font=T.F.get("base")).pack(side="left", pady=5)
    val = label(row, v, bg=bg, fg=value_fg,
                font=T.F.get("mono") if mono else T.F.get("base"),
                anchor="e")
    val.pack(side="right", pady=5, padx=(12, 0))
    hline(row)
    return row


# ------------------------------------------------------------------ 提示条
def notice(parent, text: str, kind: str = "info", *, bg: str = T.CARD,
           wrap: int = 900) -> tk.Frame:
    """左侧 3px 语义色条 + 浅色底（对应 CSS 的 border-left）。"""
    bar = tk.Frame(parent, bg=T.CARD, bd=0)
    bar.pack(fill="x", pady=6)
    stripe = tk.Frame(bar, bg={"info": T.PRIMARY, "ok": T.SUCCESS,
                               "warn": T.WARNING, "err": T.DANGER}
                      .get(kind, T.PRIMARY), width=3)
    stripe.pack(side="left", fill="y")
    body = tk.Frame(bar, bg=T.TAG_COLORS.get(
        {"info": "info", "ok": "ok", "warn": "warn", "err": "err"}.get(kind, "info"),
        T.TAG_COLORS["info"])[0])
    body.pack(side="left", fill="both", expand=True)
    lb = label(body, text, bg=body["bg"], fg=T.TEXT, font=T.F.get("base"),
               wrap=wrap)
    lb.pack(anchor="w", padx=12, pady=8)
    return bar


# ------------------------------------------------------------------ 表格
class Table(tk.Frame):
    """ttk.Treeview 的封装：统一配色、支持列宽与对齐、行点击回调。

    用 Treeview 而不是手绘表格，是因为它自带滚动、列宽拖拽与键盘导航 ——
    这些手写一遍只会更差。
    """

    def __init__(self, parent, columns: Sequence[dict], *, height: int = 8,
                 on_select: Optional[Callable[[str], None]] = None,
                 bg: str = T.CARD):
        super().__init__(parent, bg=bg, bd=0)
        import tkinter.ttk as ttk
        self._cols = list(columns)
        self._on_select = on_select
        keys = [c["key"] for c in self._cols]
        self.tree = ttk.Treeview(self, columns=keys, show="headings",
                                 height=height, style="P.Treeview")
        for c in self._cols:
            self.tree.heading(c["key"], text=c.get("text", c["key"]))
            self.tree.column(c["key"], width=c.get("width", 140),
                             anchor=c.get("anchor", "w"),
                             stretch=c.get("stretch", True))
        vs = ttk.Scrollbar(self, orient="vertical", style="P.Vertical.TScrollbar",
                           command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background=T.CARD)
        self.tree.tag_configure("even", background=T.ROW_HOVER)
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", self._sel)

    def _sel(self, _e=None):
        sel = self.tree.selection()
        if sel and self._on_select:
            self._on_select(sel[0])

    def clear(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)

    def set_rows(self, rows: Iterable[Sequence]) -> None:
        self.clear()
        for i, row in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), values=tuple(row),
                             tags=("even" if i % 2 else "odd",))


# ------------------------------------------------------------------ 可滚动区
class ScrollArea(tk.Frame):
    """纵向滚动容器（Canvas + 内嵌 Frame）。

    Tk 没有原生滚动 Frame，这是标准做法。**必须**绑定 `<Configure>` 同步
    scrollregion，否则内容变长后滚不到底部 —— 这是最常见的 Tk 坑。
    """

    def __init__(self, parent, bg: str = T.BG):
        super().__init__(parent, bg=bg, bd=0)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vs = tk.Scrollbar(self, orient="vertical",
                               command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self.canvas, bg=bg, bd=0)
        self._win = self.canvas.create_window((0, 0), window=self.body,
                                              anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        # 鼠标进入时才接管滚轮，避免抢别处的滚动
        self.canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

    def _on_body(self, _e=None):
        # 内容变高时也要把内嵌 frame 重新撑到「内容高与视口高取大」，
        # 否则气泡越加越多后，滚动区域不会跟着长。
        #
        # 注意：切页/销毁后 `<Configure>` 仍可能被排入队列，此时控件已经不存在，
        # 直接访问会抛 TclError（实测踩过）。所以每一步都单独兜住。
        try:
            if not self.canvas.winfo_exists() or not self.body.winfo_exists():
                return
            cv_h = self.canvas.winfo_height()
            self.canvas.itemconfigure(
                self._win, height=max(self.body.winfo_reqheight(), cv_h))
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas(self, e):
        """canvas 尺寸变化时同步内嵌 frame 的尺寸。

        **高度也要同步**：只同步宽度时，内容少于视口高度的话内嵌 frame 会被
        压成"内容高度"（实测：canvas 896px，但内嵌 frame 只有 68px），
        填满式的子控件（如对话流）就没法铺开，看起来像空白。
        取 `max(内容高, 视口高)` 才能既撑满又不裁掉长内容。
        """
        try:
            self.canvas.itemconfigure(self._win, width=e.width)
            need = self.body.winfo_reqheight()
            self.canvas.itemconfigure(self._win, height=max(need, e.height))
        except Exception:
            pass

    def _bind_wheel(self, on: bool):
        if on:
            self.canvas.bind_all("<MouseWheel>", self._wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)


# ------------------------------------------------------------------ 栅格助手
def grid_cards(parent, items, *, columns: int = 4, pad: int = 10,
               bg: str = T.BG) -> tk.Frame:
    """等宽卡片栅格。items 是已构造好的 widget 列表。"""
    holder = tk.Frame(parent, bg=bg, bd=0)
    for i, w in enumerate(items):
        r, c = divmod(i, columns)
        w.grid(row=r, column=c, sticky="nsew", padx=(0, pad) if c < columns - 1 else 0,
               pady=(0, pad))
    for c in range(columns):
        holder.columnconfigure(c, weight=1, uniform="card")
    return holder
