"""theme.py — 桌面端设计令牌与 ttk 样式。

视觉参考：天智五号配电网智能体（发布文档图 1–3）——**浅色侧栏 + 纯白内容区**，
色值由截图逐像素取色得到，不靠肉眼估计：

    侧栏底色 #F9FAFB · 主区 #FFFFFF · 边框 #E9E9E9 / #E0E0E0
    正文 #0F1115 · 次要文字 #81858C · 选中行 #ECEEF0 · 强调蓝 #1F6FEB

令牌与 `hc_agent/static/styles.css` 同源；`tests/verify_desktop.py` 会校验
两边一致（NAV_BG/PRIMARY/BG/CARD/BORDER/TEXT/MUTED/SUCCESS/DANGER/WARNING）。

Tk 没有 CSS 的 border-radius / box-shadow，观感靠三件事建立：
  1. 大块留白（侧栏近白、内容纯白，只用 1px 极浅描边分隔）；
  2. 单一强调蓝，且**只**用在主按钮、链接与焦点上，不做大面积填色；
  3. 紧凑但够读的字号层级（正文 10pt / 标题 15pt / 首页主标题 30pt）。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ------------------------------------------------------------------ 色板
# 侧栏：近白，与内容区靠 1px 边框与底色差区分（参考图没有竖分隔线）
NAV_BG = "#F9FAFB"
NAV_HOVER = "#F1F2F4"
NAV_ACTIVE = "#ECEEF0"     # 选中行是浅灰，不是蓝底
NAV_TEXT = "#454B54"       # 侧栏未选中文字
PRIMARY = "#1F6FEB"        # 唯一强调蓝
PRIMARY_HOVER = "#1A5FD0"
PRIMARY_WEAK = "#EEF4FF"
BG = "#FFFFFF"             # 内容区背景（参考图为纯白）
CARD = "#FFFFFF"
BORDER = "#E9E9E9"
BORDER_STRONG = "#E0E0E0"
TEXT = "#0F1115"
MUTED = "#81858C"
FAINT = "#A8ACB3"
SUCCESS = "#1D9E75"
SUCCESS_BG = "#E6F6F0"
DANGER = "#E04030"
DANGER_BG = "#FCEBEA"
WARNING = "#D98A17"
WARNING_BG = "#FDF3E2"
INFO_BG = "#F4F6F8"
HEAD_BG = "#FAFAFB"        # 表头底色
ROW_HOVER = "#FAFAFB"
GRAY_TAG_BG = "#F1F2F4"
SUBNAV_BG = "#F9FAFB"
CHIP_BG = "#F4F5F7"        # 行内代码/编号底
PILL_BG = "#FFFFFF"        # 「新会话」药丸按钮
PILL_BORDER = "#E0E0E0"
HERO_SUB = "#81858C"

# ------------------------------------------------------------------ 尺寸
TITLEBAR_H = 48
STATUSBAR_H = 30
NAV_W = 240               # 一级导航列（要放得下「承载力 配电网智能体」）
SUBNAV_W = 224            # 工作区/会话列（与参考图的会话区等宽）
PAD = 16

# 语义标签配色：(底色, 前景色)
TAG_COLORS = {
    "ok": (SUCCESS_BG, SUCCESS),
    "warn": (WARNING_BG, WARNING),
    "err": (DANGER_BG, DANGER),
    "info": (PRIMARY_WEAK, PRIMARY),
    "gray": (GRAY_TAG_BG, MUTED),
}

#: 字体表。`init_fonts()` 填入；之所以延后，是因为 Tk 字体族要在 root 建立后
#: 才能查询 —— 查不到就静默回退成方框，中文尤其明显。
F: dict = {}


def init_fonts(root: tk.Misc) -> dict:
    """挑选一个**确实装了**的中文字体，避免 Tk 静默回退成方框。"""
    global F
    fams = set(tkfont.families(root))
    for cand in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
        if cand in fams:
            fam = cand
            break
    else:
        fam = "TkDefaultFont"
    mono = "Consolas" if "Consolas" in fams else "Courier New"
    F = {
        "base": (fam, 10),
        "small": (fam, 9),
        "tiny": (fam, 8),
        "bold": (fam, 10, "bold"),
        "h1": (fam, 15, "bold"),
        "h2": (fam, 12, "bold"),
        "kpi": (fam, 18, "bold"),
        "nav": (fam, 10),
        "nav_bold": (fam, 13, "bold"),
        "mono": (mono, 9),
        "mono_sm": (mono, 8),
        # 首页主标题与品牌字（参考图里品牌与主标题同一字号，仅颜色不同）
        "hero": (fam, 28, "bold"),
        "brand": (fam, 17, "bold"),
        "brand_sub": (fam, 11),
        "fam": fam,
        "mono_fam": mono,
    }
    return F


def apply(root: tk.Misc) -> ttk.Style:
    """套用全局 ttk 样式（Treeview / 滚动条）。"""
    root.configure(bg=BG)
    st = ttk.Style(root)
    try:
        # clam 是唯一能被完整重配颜色的内置主题；默认 winnative 会忽略大部分配色
        st.theme_use("clam")
    except tk.TclError:
        pass

    st.configure("P.Treeview", background=CARD, fieldbackground=CARD,
                 foreground=TEXT, rowheight=28, font=F["base"],
                 borderwidth=0, relief="flat")
    st.configure("P.Treeview.Heading", background=HEAD_BG, foreground=MUTED,
                 font=F["bold"], relief="flat", borderwidth=0, padding=(8, 7))
    st.map("P.Treeview.Heading", background=[("active", HEAD_BG)])
    st.map("P.Treeview",
           background=[("selected", PRIMARY_WEAK)],
           foreground=[("selected", TEXT)])

    for orient in ("Vertical", "Horizontal"):
        name = f"P.{orient}.TScrollbar"
        st.configure(name, background="#DCDEE2", troughcolor=BG,
                     bordercolor=BG, arrowcolor=MUTED, borderwidth=0,
                     relief="flat", arrowsize=12)
        st.map(name, background=[("active", FAINT)])
    return st


def level_style(level: str) -> tuple:
    """红黄绿等级 → (标签文案, 标签种类)。"""
    return {
        "green": ("绿色", "ok"),
        "yellow": ("黄色", "warn"),
        "red": ("红色", "err"),
        "grey": ("灰色（内部态）", "gray"),
    }.get(str(level or ""), (str(level or "—"), "gray"))
