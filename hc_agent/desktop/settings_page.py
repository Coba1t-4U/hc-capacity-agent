"""settings_page.py — 基础配置与知识库（原生 Tk）。

三块内容：
  1. **运行环境**：引擎通道 / 版本 / Python / 数据目录 / 缓存（只读）
  2. **知识库**：内置技能卡 + 用户文档；可搜索、可添加、可导入文件、可删除
  3. **模型接入**：DeepSeek Key 状态与设置入口

设计原则：**只读信息如实显示**（取不到就写"未获取"），
用户知识可增删，内置知识不可删（界面上会说明原因）。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W

TABS = [("env", "运行环境"), ("model", "模型接入")]


class SettingsPage(tk.Frame):
    def __init__(self, parent, *, on_open_settings=None):
        super().__init__(parent, bg=T.BG, bd=0)
        self._on_open_settings = on_open_settings
        self._sub = "env"
        self._kb = None                 # 懒加载 KnowledgeBase
        self._inbox: "queue.Queue" = queue.Queue()
        self.after(150, self._drain)

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)
        self.side = tk.Frame(body, bg=T.SUBNAV_BG, width=170)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.scroll = W.ScrollArea(body, bg=T.BG)
        self.scroll.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.scroll.body, bg=T.BG)
        self.inner.pack(fill="both", expand=True, padx=20, pady=16)

        self._render_tabs()
        self.render()

    # ------------------------------------------------------------ 基础
    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _drain(self) -> None:
        if not self._alive():
            return
        try:
            while True:
                fn = self._inbox.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(150, self._drain)
        except Exception:
            pass

    def _clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

    def _license_card(self, parent) -> None:
        """授权与机器码：无授权时给出机器码，便于换取 lic.json。"""
        import tkinter as tk
        from tkinter import messagebox
        try:
            from hc_agent import license as lic
            mid = lic.machine_id()
            banner = lic.banner(lic.status())
        except Exception as exc:
            mid, banner = "未获取", f"授权状态读取失败：{type(exc).__name__}"
        box = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1,
                       highlightbackground="#D8DEE6")
        box.pack(fill="x", pady=(0, 10))
        tk.Label(box, text="授权", bg="#FFFFFF", fg="#1F242B",
                 font=("微软雅黑", 11, "bold"), anchor="w").pack(
            fill="x", padx=14, pady=(10, 2))
        tk.Label(box, text=banner, bg="#FFFFFF",
                 fg=("#1E6B3A" if "授权：" in banner and "演示" not in banner else "#864B48"),
                 font=("微软雅黑", 10), anchor="w", justify="left").pack(
            fill="x", padx=14)
        row = tk.Frame(box, bg="#FFFFFF")
        row.pack(fill="x", padx=14, pady=(6, 12))
        tk.Label(row, text="本机机器码：", bg="#FFFFFF", fg="#5A626A",
                 font=("微软雅黑", 10)).pack(side="left")
        var = tk.StringVar(value=mid)
        ent = tk.Entry(row, textvariable=var, state="readonly",
                       readonlybackground="#F5F7FA", fg="#1F242B",
                       font=("Consolas", 10), width=38, relief="flat")
        ent.pack(side="left", padx=(2, 10), ipady=3)

        def copy_mid() -> None:
            parent.clipboard_clear()
            parent.clipboard_append(mid)
            messagebox.showinfo("已复制", "机器码已复制到剪贴板，请发给供方换取授权文件。",
                                parent=parent)

        tk.Button(row, text="复制", command=copy_mid, relief="flat",
                  bg="#014C83", fg="#FFFFFF", activebackground="#0B5FA5",
                  activeforeground="#FFFFFF", font=("微软雅黑", 9),
                  padx=12, cursor="hand2").pack(side="left")
        def import_lic() -> None:
            import shutil, sys
            from tkinter import filedialog
            src = filedialog.askopenfilename(
                parent=parent, title="选择授权文件 lic.json",
                filetypes=[("授权文件", "*.json"), ("所有文件", "*.*")])
            if not src:
                return
            st = lic.verify(Path(src))
            if not st.ok:
                messagebox.showerror("授权无效",
                                     f"{st.reason}\n\n请确认该授权文件是为本机机器码签发的。",
                                     parent=parent)
                return
            targets = []
            if getattr(sys, "frozen", False):
                targets.append(Path(sys.executable).parent / "lic.json")
            else:
                targets.append(Path(__file__).resolve().parents[2] / "lic.json")
            targets.append(Path.home() / ".hc_agent" / "lic.json")
            saved = None
            for t in targets:
                try:
                    t.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, t)
                    saved = t
                    break
                except Exception:
                    continue
            if saved is None:
                messagebox.showerror("导入失败", "没有可写入的位置，请手工复制 lic.json。",
                                     parent=parent)
                return
            messagebox.showinfo("导入成功",
                                f"授权已写入：\n{saved}\n\n重启程序后生效。",
                                parent=parent)

        tk.Button(row, text="导入授权文件…", command=import_lic, relief="flat",
                  bg="#F5F7FA", fg="#014C83", activebackground="#E4EBF5",
                  font=("微软雅黑", 9), padx=10, cursor="hand2").pack(
            side="left", padx=(8, 0))

        tk.Label(box, text="授权文件 lic.json 放到程序目录或 %USERPROFILE%\\.hc_agent\\，重启后生效。",
                 bg="#FFFFFF", fg="#5A626A", font=("微软雅黑", 9),
                 anchor="w", justify="left").pack(fill="x", padx=14, pady=(0, 10))

    def _render_tabs(self) -> None:
        for w in self.side.winfo_children():
            w.destroy()
        for key, label in TABS:
            active = (self._sub == key)
            lb = tk.Label(self.side, text=label,
                          bg=T.PRIMARY_WEAK if active else T.SUBNAV_BG,
                          fg=T.PRIMARY if active else T.TEXT,
                          font=T.F.get("bold") if active else T.F.get("base"),
                          anchor="w", padx=14, pady=8, cursor="hand2")
            lb.pack(fill="x")
            lb.bind("<Button-1>", lambda e, k=key: self._switch(k))

    def _switch(self, key: str) -> None:
        self._sub = key
        self._render_tabs()
        self.render()

    def render(self) -> None:
        self._clear()
        if self._sub == "env":
            self._render_env()
        elif self._sub == "kb":
            self._render_kb_link()
        else:
            self._render_model()

    # ------------------------------------------------------------ 运行环境
    def _render_env(self) -> None:
        self._license_card(self.inner)
        W.label(self.inner, "运行环境", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        info = self._collect_env()
        box = W.card(self.inner)
        box.pack(fill="x")
        for k, v, mono in info:
            W.kv_row(box.inner, k, str(v), mono=mono)
        W.notice(box.inner,
                 "以上为只读信息。打包运行时可执行 HC-Agent.exe --paths / "
                 "--self-test 复核。", "info")

    def _collect_env(self) -> List[tuple]:
        rows: List[tuple] = []
        try:
            from hc_agent import __version__, paths
            rows.append(("Agent 版本", __version__, False))
            d = paths.describe()
            rows.append(("运行形态", "打包" if d.get("frozen") else "源码", False))
            rows.append(("程序目录", d.get("bundle_root"), True))
            rows.append(("数据目录", d.get("data_root"), True))
            rows.append(("运行产物", d.get("runs_root"), True))
            rows.append(("日志目录", d.get("logs_root"), True))
        except Exception as exc:
            rows.append(("路径信息", f"未获取（{exc}）", False))
        try:
            from hc_agent import runtime_env
            lib = runtime_env.ensure_engine_on_path()
            rows.append(("引擎库目录", lib or "不可用", True))
            import opendssdirect as dss
            ver = str(dss.Basic.Version())
            rows.append(("引擎版本", ver.split("based on")[0].strip(), True))
        except Exception as exc:
            rows.append(("引擎", f"未获取（{type(exc).__name__}）", False))
        try:
            from hc_agent import llm
            cfg = llm.LLMConfig.from_env()
            rows.append(("DeepSeek", 
                         f"已配置（{cfg.masked()}，{cfg.model}）"
                         if cfg.enabled else "未配置（用本地规则引擎）", False))
        except Exception:
            rows.append(("DeepSeek", "未获取", False))
        try:
            from hc_agent.knowledge import KnowledgeBase
            st = KnowledgeBase().stats()
            rows.append(("知识库", f"{st['total']} 篇"
                                   f"（内置 {st['builtin']} / 用户 {st['user']}）",
                         False))
        except Exception:
            rows.append(("知识库", "未获取", False))
        try:
            from hc_agent.desktop import runs as R
            rows.append(("已有运行", f"{len(R.list_runs())} 次", False))
        except Exception:
            pass
        return rows

    # ------------------------------------------------------------ 知识库
    def _render_kb_link(self) -> None:
        """知识库已独立成页（内容多，塞在设置里放不下）。"""
        W.label(self.inner, "知识库", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(self.inner)
        box.pack(fill="x")
        W.notice(box.inner,
                 "知识库已独立成页：支持多知识库、文档上传、异步索引任务、"
                 "检索测试与 RAG 评估集。请到左侧导航的「知识库」页操作。",
                 "info")

        def _go() -> None:
            try:
                app = self.winfo_toplevel()
                if hasattr(app, "show"):
                    app.show("kb")
            except Exception:
                pass

        W.button(box.inner, "打开知识库", _go, kind="primary").pack(
            anchor="w", pady=(10, 0))
        try:
            from hc_agent.knowledge import KnowledgeService
            st = KnowledgeService().stats()
            W.label(box.inner,
                    f"当前默认库：{st['docs']} 篇文档 / {st['chunks']} 块"
                    f"；共 {st['kbs']} 个知识库",
                    bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("small")).pack(anchor="w", pady=(8, 0))
        except Exception as exc:
            W.label(box.inner, f"读取知识库状态失败：{exc}", bg=T.CARD,
                    fg=T.MUTED, font=T.F.get("small")).pack(anchor="w",
                                                             pady=(8, 0))

    def _render_model(self) -> None:
        W.label(self.inner, "模型接入", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(self.inner)
        box.pack(fill="x")
        try:
            from hc_agent import llm
            cfg = llm.LLMConfig.from_env()
            W.kv_row(box.inner, "当前引擎",
                     "DeepSeek（" + cfg.model + "）" if cfg.enabled
                     else "本地规则引擎")
            W.kv_row(box.inner, "API Key", cfg.masked(), mono=True)
            W.kv_row(box.inner, "接口地址", cfg.base_url, mono=True)
            W.kv_row(box.inner, "配置位置", str(llm.config_path()), mono=True)
        except Exception as exc:
            W.kv_row(box.inner, "状态", f"未获取（{exc}）")
        W.notice(box.inner,
                 "填 Key 后由 DeepSeek 负责理解与措辞；所有数字仍从本地评估"
                 "产物经工具读取，模型不得自行计算。留空则用本地规则引擎。",
                 "info")
        btns = tk.Frame(box.inner, bg=T.CARD)
        btns.pack(fill="x", pady=(10, 0))
        if self._on_open_settings:
            W.button(btns, "打开模型设置", self._on_open_settings,
                     kind="primary").pack(side="left")

        def do_test() -> None:
            info = W.label(box.inner, "正在测试…", bg=T.CARD, fg=T.MUTED,
                           font=T.F.get("small"))
            info.pack(anchor="w", pady=(10, 0))

            def _work() -> None:
                from hc_agent import llm
                r = llm.self_test()

                def _show() -> None:
                    if r.get("ok"):
                        info.configure(text=f"连接成功（{r.get('model')}）",
                                       fg=T.SUCCESS)
                    else:
                        info.configure(text=f"连接失败：{r.get('reason')}",
                                       fg=T.DANGER)
                self._inbox.put(_show)

            threading.Thread(target=_work, daemon=True).start()

        W.button(btns, "测试连接", do_test).pack(side="left", padx=(8, 0))

        W.section_title(self.inner, "知识库如何参与回答", bg=T.BG)
        box2 = W.card(self.inner)
        box2.pack(fill="x")
        W.notice(box2.inner,
                 "提问时先检索知识库，把命中的**方法说明**作为上下文交给模型；"
                 "但**实测数字仍只来自运行产物**。两者严格分开，"
                 "否则知识里写死的旧数值会污染实时结论。", "info")
        W.tag(box2.inner, "内置技能卡 12 张 + 规则模板 1 份", "gray",
              bg=T.CARD).pack(anchor="w", pady=(6, 0))
