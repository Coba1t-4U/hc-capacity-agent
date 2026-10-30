"""dss_page.py — 原生 Tk 的「OpenDSS 算例分析」页。

与承载力页的区别：这里是**直接跑 OpenDSS 原生算例**（.dss / 文件夹），
不做模型重建、不产出开放容量，报告里会明确声明这一点。

交互：
  1. 选文件或文件夹 → 「分析」；
  2. 左侧按类别列结果（概览 / 母线电压 / 支路负载 / 越限明细）；
  3. 右下角可一键「问这个算例」，把实测值喂给问答。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Any, Dict, List, Optional

from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W


def _fmt(v: Any, d: int = 4) -> str:
    if v is None or v == "":
        return "—"
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if n != n:
        return "—"
    return f"{n:,.{d}f}".rstrip("0").rstrip(".")


class DssPage(tk.Frame):
    """算例分析视图（可嵌在任意容器里）。"""

    def __init__(self, parent, *, on_ask=None):
        super().__init__(parent, bg=T.BG, bd=0)
        self._on_ask = on_ask
        self._result = None
        self._busy = False
        # 工作线程 → 主线程的结果队列（跨线程不碰控件）
        self._inbox: "queue.Queue" = queue.Queue()
        self.after(120, self._drain)

        # ---- 顶部：路径 + 按钮
        top = tk.Frame(self, bg=T.CARD, bd=0)
        top.pack(fill="x")
        tk.Frame(top, bg=T.BORDER, height=1).pack(side="bottom", fill="x")
        row = tk.Frame(top, bg=T.CARD)
        row.pack(fill="x", padx=16, pady=10)
        W.label(row, "OpenDSS 算例", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(side="left", padx=(0, 8))
        self.path_var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.path_var,
                              font=T.F.get("mono_sm"), bg="#FFFFFF",
                              fg=T.TEXT, relief="solid", bd=1,
                              highlightthickness=0, insertbackground=T.TEXT)
        self.entry.pack(side="left", fill="x", expand=True, ipady=5)
        W.button(row, "选文件", self._pick_file, small=True).pack(
            side="left", padx=(8, 0))
        W.button(row, "选目录", self._pick_dir, small=True).pack(
            side="left", padx=(6, 0))
        self.run_btn = W.button(row, "分析", self.run_analysis, kind="primary")
        self.run_btn.pack(side="left", padx=(8, 0))
        self.ask_btn = W.button(row, "问这个算例", self._ask, small=True)
        self.ask_btn.pack(side="left", padx=(6, 0))
        self.ask_btn.set_enabled(False)

        self.status = W.label(top, "", bg=T.CARD, fg=T.MUTED,
                              font=T.F.get("small"))
        self.status.pack(anchor="w", padx=18, pady=(0, 8))

        # ---- 主体：左导航 + 右内容
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)

        self.side = tk.Frame(body, bg=T.SUBNAV_BG, width=180)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        self.scroll = W.ScrollArea(body, bg=T.BG)
        self.scroll.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.scroll.body, bg=T.BG)
        self.inner.pack(fill="both", expand=True, padx=20, pady=16)

        self._tabs: Dict[str, tk.Label] = {}
        self._sub = "overview"
        self._render_tabs()
        self._placeholder()

    # ------------------------------------------------------------ 选择
    def _pick_file(self) -> None:
        f = filedialog.askopenfilename(
            title="选择 OpenDSS 算例",
            filetypes=[("OpenDSS", "*.dss *.DSS"), ("所有文件", "*.*")])
        if f:
            self.path_var.set(f)
            self.status.configure(text="已选择文件，点「分析」开始", fg=T.MUTED)

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(title="选择算例目录（含 Master.dss）")
        if d:
            self.path_var.set(d)
            self.status.configure(text="已选择目录，点「分析」开始", fg=T.MUTED)

    # ------------------------------------------------------------ 分析
    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def run_analysis(self) -> None:
        path = self.path_var.get().strip()
        if not path:
            self.status.configure(text="请先选择 .dss 文件或目录", fg=T.WARNING)
            return
        if self._busy:
            return
        self._busy = True
        self.run_btn.set_enabled(False)
        self.status.configure(text="正在求解…", fg=T.PRIMARY)

        def _work() -> None:
            try:
                from hc_agent.io import dss_case
                res = dss_case.analyze(path)
                self._post(lambda: self._done(res))
            except BaseException as exc:
                import traceback
                msg = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()[-500:]
                self._post(lambda: self._failed(msg, tb))

        threading.Thread(target=_work, daemon=True, name="dss-analyze").start()

    def _drain(self) -> None:
        """主线程轮询：执行工作线程排进来的回调。

        **不能在工作线程里调 after()** —— Tk 会抛
        "main thread is not in main loop"（实测踩过）。所以走队列。
        """
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
            self.after(120, self._drain)
        except Exception:
            pass

    def _post(self, fn) -> None:
        """把工作线程的结果排进队列，由主线程消费。"""
        self._inbox.put(fn)

    def _done(self, res) -> None:
        self._busy = False
        try:
            self.run_btn.set_enabled(True)
            self.ask_btn.set_enabled(True)
        except Exception:
            pass
        self._result = res
        if res.converged:
            self.status.configure(
                text=f"求解成功：{res.bus_count} 母线 / "
                     f"{len(res.branches)} 支路，{res.iterations} 次迭代，"
                     f"{res.elapsed_s}s", fg=T.SUCCESS)
        else:
            self.status.configure(text="潮流未收敛，结果不可作结论", fg=T.DANGER)
        self._render()

    def _failed(self, msg: str, tb: str) -> None:
        self._busy = False
        try:
            self.run_btn.set_enabled(True)
        except Exception:
            pass
        self.status.configure(text=f"分析失败：{msg}", fg=T.DANGER)
        self._clear()
        W.notice(self.inner, f"分析失败：{msg}\n\n{tb}", "err")

    # ------------------------------------------------------------ 渲染
    def _render_tabs(self) -> None:
        for w in self.side.winfo_children():
            w.destroy()
        tabs = [("capacity", "承载能力"), ("voltage", "电压校核"),
                ("loading", "设备负载"), ("whatif", "增减假设"),
                ("boundary", "口径与边界")]
        for key, label in tabs:
            active = (self._sub == key)
            lb = tk.Label(self.side, text=label,
                          bg=T.PRIMARY_WEAK if active else T.SUBNAV_BG,
                          fg=T.PRIMARY if active else T.TEXT,
                          font=T.F.get("bold") if active else T.F.get("base"),
                          anchor="w", padx=14, pady=8, cursor="hand2")
            lb.pack(fill="x")
            lb.bind("<Button-1>", lambda e, k=key: self._switch(k))
            self._tabs[key] = lb

    def _switch(self, key: str) -> None:
        self._sub = key
        self._render_tabs()
        if self._result is None:
            self._placeholder()
        else:
            self._render()

    def _clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

    def _placeholder(self) -> None:
        self._clear()
        box = W.card(self.inner)
        box.pack(fill="x")
        W.label(box.inner, "选择 OpenDSS 算例后点「分析」", bg=T.CARD,
                fg=T.TEXT, font=T.F.get("h2")).pack(anchor="w")
        W.notice(box.inner,
                 "本页从**承载力视角**看一个算例：还有多少裕度、"
                 "哪个约束先到限、把电源加到多少会越限。", "info")
        W.notice(box.inner,
                 "注意：对原生算例只做单断面潮流与约束校核，"
                 "**不产出开放容量**（那需要容量搜索 + 批准规则包 + 独立复算）。",
                 "warn")
        W.label(box.inner,
                "示例路径：opendss_demo\\examples\\13Bus\\IEEE13Nodeckt.dss",
                bg=T.CARD, fg=T.FAINT, font=T.F.get("small")).pack(anchor="w")

    def _render(self) -> None:
        self._clear()
        r = self._result
        if r is None:
            self._placeholder()
            return
        {"capacity": self._render_capacity,
         "voltage": self._render_voltage,
         "loading": self._render_loading,
         "whatif": self._render_whatif,
         "boundary": self._render_boundary}.get(
            self._sub, self._render_capacity)(r)

    # ------------------------------------------------------------ 裕度计算
    @staticmethod
    def _margins(r):
        """算三条约束的裕度：电压上/下、载流。负值＝已越限。"""
        vmax = r.best_voltage().vmin_pu if r.best_voltage() else None
        vmin = r.worst_voltage().vmin_pu if r.worst_voltage() else None
        wb = r.worst_branch()
        up = None if vmax is None else (1.10 - vmax)
        lo = None if vmin is None else (vmin - 0.90)
        v_margin = None
        if up is not None and lo is not None:
            v_margin = min(up, lo)
        l_margin = None if wb is None else (100.0 - wb.loading_pct)
        return {"vmax": vmax, "vmin": vmin, "up": up, "lo": lo,
                "v_margin": v_margin, "l_margin": l_margin, "wb": wb}

    def _render_capacity(self, r) -> None:
        """承载能力：先回答"卡在哪"。"""
        m = self._margins(r)
        nv = len(r.voltage_violations())
        no = len(r.overloaded())
        W.label(self.inner, f"承载能力　{r.source_name}", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(self.inner, bg=T.BG)
        row.pack(fill="x")

        def tag_of(margin, unit):
            if margin is None:
                return "", "gray"
            if margin < 0:
                return f"已越限 {margin:+.4f} {unit}", "err"
            return f"剩余 {margin:.4f} {unit}", "ok"

        t1, k1 = tag_of(m["v_margin"], "pu")
        t2, k2 = tag_of(m["l_margin"], "%")
        cards = [
            W.kpi(row, "电压裕度（判据 0.90~1.10）",
                  _fmt(m["v_margin"], 4) if m["v_margin"] is not None else "—",
                  tag_text=t1, tag_kind=k1,
                  foot=f"Vmax {_fmt(m['vmax'], 4)} / Vmin {_fmt(m['vmin'], 4)} pu"),
            W.kpi(row, "载流裕度（判据 ≤100%）",
                  _fmt(m["l_margin"], 2) if m["l_margin"] is not None else "—",
                  tag_text=t2, tag_kind=k2,
                  foot=f"最重 {m['wb'].name} {_fmt(m['wb'].loading_pct, 2)}%"
                       if m["wb"] else ""),
            W.kpi(row, "越限 / 过载", f"{nv} / {no}",
                  tag_text="全部满足" if (nv == 0 and no == 0) else "存在越限",
                  tag_kind="ok" if (nv == 0 and no == 0) else "warn",
                  foot="母线越限数 / 元件过载数"),
        ]
        for c in cards:
            c.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # ---- 结论：哪个约束先到限
        box = W.card(self.inner)
        box.pack(fill="x", pady=(14, 0))
        W.label(box.inner, "哪个约束先到限", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w", pady=(0, 6))
        cands = []
        if m["v_margin"] is not None:
            cands.append(("电压", m["v_margin"], "pu",
                          (r.best_voltage().name if (m["up"] is not None
                           and m["up"] <= (m["lo"] if m["lo"] is not None
                                           else 9e9))
                           else r.worst_voltage().name)))
        if m["l_margin"] is not None:
            cands.append(("载流", m["l_margin"], "%",
                          m["wb"].name if m["wb"] else ""))
        if cands:
            cands.sort(key=lambda x: x[1])
            name, margin, unit, obj = cands[0]
            if margin < 0:
                W.notice(box.inner,
                         f"{name}约束**已经越限**：{obj} 裕度 "
                         f"{margin:+.4f} {unit}。这个断面不允许再加分布式电源，"
                         f"先要解决现有越限。".replace("**", ""), "err")
            else:
                W.notice(box.inner,
                         f"{name}约束**最先到限**：{obj} 剩余裕度 "
                         f"{margin:.4f} {unit}。继续加大电源，{name}会先被顶到。"
                         .replace("**", ""), "ok")
            if len(cands) > 1:
                nm, mg, un, ob = cands[1]
                W.label(box.inner, f"其次：{nm}约束 {ob} 裕度 {mg:.4f} {un}",
                        bg=T.CARD, fg=T.MUTED,
                        font=T.F.get("small")).pack(anchor="w")

        # ---- 断面规模（弱化，放一行）
        W.label(box.inner,
                f"断面规模：母线 {r.bus_count} 个、支路 {len(r.branches)} 条；"
                f"总负荷 {_fmt(r.total_load_kw, 2)} kW、"
                f"电源 {_fmt(r.total_gen_kw, 2)} kW、"
                f"网损 {_fmt(r.losses_kw, 2)} kW（{_fmt(r.losses_pct, 2)}%）；"
                f"源侧 {'倒送' if r.source_kw > 0 else '受电'} "
                f"{abs(r.source_kw):.2f} kW",
                bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
                wrap=880).pack(anchor="w", pady=(10, 0))

        box2 = W.card(self.inner)
        box2.pack(fill="x", pady=(12, 0))
        W.notice(box2.inner, str(r.capacity_note).replace("**", ""), "warn")
        for w in r.warnings:
            W.notice(box2.inner, w, "warn")

        row3 = tk.Frame(box2.inner, bg=T.CARD)
        row3.pack(fill="x", pady=(10, 0))
        W.button(row3, "查看越限明细",
                 lambda: self._switch("voltage"), small=True).pack(side="left")
        W.button(row3, "试算「加到多少会越限」",
                 lambda: self._switch("whatif"), small=True).pack(
            side="left", padx=(8, 0))

    def _render_voltage(self, r) -> None:
        m = self._margins(r)
        vv = sorted(r.voltage_violations(), key=lambda b: -b.vmin_pu)
        W.label(self.inner, "电压校核", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(self.inner)
        box.pack(fill="x")
        W.label(box.inner,
                f"判据 0.90 ~ 1.10 pu（本工程规则包 HC-RULES@1.0）　"
                f"最高 {_fmt(m['vmax'], 5)} pu　最低 {_fmt(m['vmin'], 5)} pu",
                bg=T.CARD, fg=T.TEXT, font=T.F.get("base")).pack(anchor="w")
        W.notice(box.inner,
                 "注意：这个 ±10% 是项目批准值，与 GB/T 12325 对 10 kV 的 "
                 "±7% 不是同一口径。要按国标判定请说明。", "info")

        if vv:
            W.label(box.inner, f"越限母线（{len(vv)} 个，按电压从高到低）",
                    bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold")).pack(anchor="w", pady=(10, 4))
            tb = W.Table(box.inner,
                         [{"key": "b", "text": "母线", "width": 180},
                          {"key": "v", "text": "电压 pu", "width": 120,
                           "anchor": "e"},
                          {"key": "d", "text": "偏离 1.10", "width": 130,
                           "anchor": "e"}], height=8)
            tb.set_rows([(b.name, _fmt(b.vmin_pu, 6),
                          f"{b.vmin_pu - 1.10:+.4f}") for b in vv])
            tb.pack(fill="x")
        else:
            W.label(box.inner, "没有母线越限。", bg=T.CARD, fg=T.SUCCESS,
                    font=T.F.get("base")).pack(anchor="w", pady=10)
        W.label(box.inner,
                "全部母线的电压清单在报告附录 A 里（本页只列越限的，"
                "避免一屏几十行看不完）。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small")).pack(anchor="w", pady=(8, 0))

    def _render_loading(self, r) -> None:
        m = self._margins(r)
        ov = sorted(r.overloaded(), key=lambda b: -b.loading_pct)
        top = sorted(r.branches, key=lambda b: -b.loading_pct)[:8]
        W.label(self.inner, "设备负载校核", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(self.inner)
        box.pack(fill="x")
        wb = m["wb"]
        W.label(box.inner,
                f"判据 负载率 ≤ 100%（变压器按视在功率/额定容量，"
                f"线路按最重相电流）　最重 {wb.name} {_fmt(wb.loading_pct, 2)}%"
                if wb else "判据 负载率 ≤ 100%",
                bg=T.CARD, fg=T.TEXT, font=T.F.get("base"),
                wrap=880).pack(anchor="w")
        if ov:
            W.notice(box.inner,
                     "过载：" + "、".join(f"{b.name}（{b.loading_pct:.1f}%）"
                                        for b in ov), "err")
        else:
            W.notice(box.inner, "没有元件过载。", "ok")

        if top:
            W.label(box.inner, "负载最重的 8 个元件", bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold")).pack(anchor="w", pady=(10, 4))
            tb = W.Table(box.inner,
                         [{"key": "n", "text": "元件", "width": 160},
                          {"key": "k", "text": "类型", "width": 100},
                          {"key": "l", "text": "负载率 %", "width": 110,
                           "anchor": "e"},
                          {"key": "r", "text": "额定 kVA", "width": 110,
                           "anchor": "e"}], height=9)
            tb.set_rows([(b.name, b.kind, _fmt(b.loading_pct, 2),
                          _fmt(b.rating_kva, 1)) for b in top])
            tb.pack(fill="x")
        W.label(box.inner,
                "全部支路的负载清单在报告附录 B 里。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small")).pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------ 增减假设
    def _render_whatif(self, r) -> None:
        W.label(self.inner, "增减假设：加到多少会越限", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))
        box = W.card(self.inner)
        box.pack(fill="x")
        W.notice(box.inner,
                 "做法：把这个算例**复制一份**、把分布式电源改到目标容量、"
                 "重跑一次潮流，再和现在比。**你原来的算例不会被改动。**",
                 "info")

        row = tk.Frame(box.inner, bg=T.CARD)
        row.pack(fill="x", pady=(10, 0))
        W.label(row, "目标容量 (kW)：", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("base")).pack(side="left")
        ent = tk.Entry(row, width=12, font=T.F.get("base"), bg="#FFFFFF",
                       fg=T.TEXT, relief="solid", bd=1,
                       highlightthickness=0, insertbackground=T.TEXT)
        cur = getattr(r, "total_gen_kw", 0) or 0
        ent.insert(0, f"{max(1.0, round(cur * 1.5)):.0f}")
        ent.pack(side="left", padx=(6, 8), ipady=4)
        self._whatif_entry = ent
        self._whatif_out = tk.Frame(box.inner, bg=T.CARD)
        self._whatif_out.pack(fill="x", pady=(12, 0))

        def go() -> None:
            try:
                target = float(ent.get().strip())
            except Exception:
                W.notice(self._whatif_out, "请输入数字，例如 300", "err")
                return
            W.label(self._whatif_out, "正在重算…", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("small")).pack(anchor="w")
            import threading

            def work():
                from hc_agent.io import dss_facts
                try:
                    res = dss_facts.what_if_pv(r.source, target)
                except Exception as exc:
                    res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self._post(lambda: self._show_whatif(res))
            threading.Thread(target=work, daemon=True).start()

        W.button(row, "重算并对比", go, kind="primary").pack(side="left")

    def _show_whatif(self, res) -> None:
        out = getattr(self, "_whatif_out", None)
        if out is None or not out.winfo_exists():
            return
        for w in out.winfo_children():
            w.destroy()
        if not res.get("ok"):
            W.notice(out, f"试算失败：{res.get('error')}", "err")
            return

        def d(a, b, n=4):
            if a is None or b is None:
                return "未获取"
            return f"{a:.{n}f} → {b:.{n}f}"

        rows = [
            ("分布式电源", f"{res['gen_before_kw']:.0f} → "
                          f"{res['gen_after_kw']:.0f} kW"),
            ("最高电压", d(res["vmax_before"], res["vmax_after"]) + " pu"),
            ("最低电压", d(res["vmin_before"], res["vmin_after"]) + " pu"),
            ("电压越限母线", f"{res['violations_before']} → "
                            f"{res['violations_after']} 个"),
            ("过载元件", f"{res['overload_before']} → "
                        f"{res['overload_after']} 个"),
            ("最大负载率", d(res["maxload_before"], res["maxload_after"], 2) + " %"),
            ("网损", d(res["losses_before_kw"], res["losses_after_kw"], 2) + " kW"),
            ("整站倒送", f"{'无' if not res['reverse_before'] else '有'} → "
                        f"{'无' if not res['reverse_after'] else '有'}"),
        ]
        tb = W.Table(out, [{"key": "k", "text": "项目", "width": 160},
                           {"key": "v", "text": "改造前 → 改造后", "width": 300}],
                     height=len(rows))
        tb.set_rows(rows)
        tb.pack(fill="x")
        if not res.get("converged", True):
            W.notice(out, "改了之后潮流不收敛，上面的数值不可用。", "err")
        elif res["violations_after"] > res["violations_before"]:
            W.notice(out, "这个容量下出现了新的越限 —— 就是「加到这儿不行」的信号。",
                     "warn")

    # ------------------------------------------------------------ 口径与边界
    def _render_boundary(self, r) -> None:
        W.label(self.inner, "口径与边界", bg=T.BG, fg=T.TEXT,
                font=T.F.get("h2")).pack(anchor="w", pady=(0, 10))

        box = W.card(self.inner)
        box.pack(fill="x")
        W.label(box.inner, "本页用的判据", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w")
        tb = W.Table(box.inner,
                     [{"key": "k", "text": "约束", "width": 130},
                      {"key": "v", "text": "判据", "width": 150},
                      {"key": "s", "text": "来源", "width": 420}], height=5)
        tb.set_rows([
            ("电压", "0.90 ~ 1.10 pu",
             "本工程规则包 HC-RULES@1.0（项目批准值）；"
             "GB/T 12325 对 10 kV 是 ±7%，口径不同"),
            ("载流", "≤ 100%",
             "DL/T 5222 载流校验方法；100% 为项目批准值"),
            ("反向潮流", "≤ 400 kW",
             "项目批准值（Q/GDW 1484 只规定要求，不给全国统一数字）"),
            ("三相不平衡", "≤ 2.0%",
             "GB/T 15543-2008 公共连接点口径（不是「每个用户 1.3%」口径）"),
            ("短路电流", "≤ 20000 A",
             "DL/T 5222 校验方法；20 kA 为项目批准值"),
        ])
        tb.pack(fill="x")

        box2 = W.card(self.inner)
        box2.pack(fill="x", pady=(12, 0))
        W.label(box2.inner, "本页不产出什么", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w")
        W.notice(box2.inner,
                 "不产出开放容量（kW）。开放容量需要容量搜索 + 批准规则包 + "
                 "独立复算 + 人工审批，是对项目输入包的流程，不适用于外部原生算例。",
                 "warn")
        W.label(box2.inner, "未覆盖的能力", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w", pady=(8, 4))
        for line in (
                "概率潮流与数据驱动潮流",
                "自动分接头调压、无功补偿控制、拓扑重构、负荷削减、重调度",
                "N-1 自动扫描",
                "暂态稳定、保护配合、谐波、频率全过程",
                "现场控制闭环（Agent 无任何设备写操作能力）"):
            W.label(box2.inner, "· " + line, bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("small")).pack(anchor="w")

        box3 = W.card(self.inner)
        box3.pack(fill="x", pady=(12, 0))
        W.label(box3.inner, "要拿承载力结论该怎么做", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w")
        W.label(box3.inner,
                "补齐项目输入包（拓扑/负荷/电源/约束/规则），到「容量分析」页"
                "发起评估，再走「结果与证据」里的独立复算与发布门禁。",
                bg=T.CARD, fg=T.MUTED, font=T.F.get("small"),
                wrap=880).pack(anchor="w", pady=(4, 0))

    # ------------------------------------------------------------ 问这个算例
    def _ask(self) -> None:
        if self._result is None or self._on_ask is None:
            return
        r = self._result
        wv, wb = r.worst_voltage(), r.worst_branch()
        q = (f"OpenDSS 算例 {r.source_name} 的结果怎么样？"
             f"（母线 {r.bus_count} 个，支路 {len(r.branches)} 条，"
             f"总负荷 {_fmt(r.total_load_kw, 2)} kW，"
             f"网损 {_fmt(r.losses_kw, 2)} kW 占 {_fmt(r.losses_pct, 2)}%，"
             f"最低电压 {_fmt(wv.vmin_pu, 5) if wv else '—'} pu"
             f"@{wv.name if wv else '—'}，"
             f"最大负载率 {_fmt(wb.loading_pct, 2) if wb else '—'}%"
             f"@{wb.name if wb else '—'}，"
             f"电压越限 {len(r.voltage_violations())} 个、"
             f"过载 {len(r.overloaded())} 条）")
        self._on_ask(q)
