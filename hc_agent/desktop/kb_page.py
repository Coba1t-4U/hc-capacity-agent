"""kb_page.py — 知识库管理界面（原生 Tk，对齐 ai-localbase 的功能面）。

四个区域：
  左：知识库列表（可建、可删）
  右：文档 / 检索测试 / 索引任务 / 评估集 四个标签

设计要点：
  * 索引在**后台线程**跑，界面只轮询数据库拿进度 —— Tk 非线程安全，
    工作线程绝不碰控件。
  * 长任务全部有可见进度条与「取消」，不让用户面对一个转圈的空界面。
  * 检索测试直接显示**得分、覆盖率、命中词**，便于判断为什么这条排第一。
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Any, Dict, List, Optional

from hc_agent.desktop import theme as T
from hc_agent.desktop import widgets as W

TABS = [("docs", "文档"), ("search", "检索测试"),
        ("jobs", "索引任务"), ("eval", "评估集")]


def _fmt(v: Any, d: int = 2) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):,.{d}f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


class KnowledgePage(tk.Frame):
    """知识库管理页。"""

    def __init__(self, parent, *, on_ask=None):
        super().__init__(parent, bg=T.BG, bd=0)
        self._on_ask = on_ask
        self._svc = None
        self._kb_id = ""
        self._sub = "docs"
        self._busy_job: Optional[str] = None
        self._inbox: "queue.Queue" = queue.Queue()

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True)

        # ---- 左侧：知识库列表
        self.side = tk.Frame(body, bg=T.SUBNAV_BG, width=250)
        self.side.pack(side="left", fill="y")
        self.side.pack_propagate(False)
        head = tk.Frame(self.side, bg=T.SUBNAV_BG)
        head.pack(fill="x", padx=12, pady=(14, 8))
        W.label(head, "知识库", bg=T.SUBNAV_BG, fg=T.TEXT,
                font=T.F.get("bold")).pack(side="left")
        W.button(head, "＋", self._create_kb, small=True).pack(side="right")
        self.kb_box = tk.Frame(self.side, bg=T.SUBNAV_BG)
        self.kb_box.pack(fill="both", expand=True)
        ops = tk.Frame(self.side, bg=T.SUBNAV_BG)
        ops.pack(fill="x", padx=10, pady=8)
        W.button(ops, "删除该库", self._delete_kb, small=True).pack(fill="x")

        # ---- 右侧
        right = tk.Frame(body, bg=T.BG)
        right.pack(side="left", fill="both", expand=True)
        bar = tk.Frame(right, bg=T.CARD)
        bar.pack(fill="x")
        tk.Frame(bar, bg=T.BORDER, height=1).pack(side="bottom", fill="x")
        r1 = tk.Frame(bar, bg=T.CARD)
        r1.pack(fill="x", padx=16, pady=9)
        self.title_lbl = W.label(r1, "知识库", bg=T.CARD, fg=T.TEXT,
                                 font=T.F.get("h2"))
        self.title_lbl.pack(side="left")
        self.stat_lbl = W.label(r1, "", bg=T.CARD, fg=T.MUTED,
                                font=T.F.get("small"))
        self.stat_lbl.pack(side="left", padx=10)
        W.button(r1, "生成评估", self._run_eval, small=True).pack(side="right")
        W.button(r1, "重建索引", self._rebuild, small=True).pack(
            side="right", padx=(0, 6))
        W.button(r1, "新建文档", self._new_doc, small=True).pack(
            side="right", padx=(0, 6))
        W.button(r1, "上传文件", self._upload, small=True,
                 kind="primary").pack(side="right", padx=(0, 6))

        tabs = tk.Frame(right, bg=T.BG)
        tabs.pack(fill="x", padx=16, pady=(10, 6))
        self._tab_w: Dict[str, tk.Label] = {}
        for key, label in TABS:
            lb = tk.Label(tabs, text=label, bg=T.BG,
                          fg=T.PRIMARY if key == self._sub else T.MUTED,
                          font=T.F.get("bold") if key == self._sub
                          else T.F.get("base"),
                          padx=10, pady=4, cursor="hand2")
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, k=key: self._switch(k))
            self._tab_w[key] = lb

        self.scroll = W.ScrollArea(right, bg=T.BG)
        self.scroll.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.scroll.body, bg=T.BG)
        self.inner.pack(fill="both", expand=True, padx=16, pady=10)

        # 底部进度条（索引任务）
        self.prog = tk.Frame(right, bg=T.CARD)
        self.prog_lbl = W.label(self.prog, "", bg=T.CARD, fg=T.MUTED,
                                font=T.F.get("small"))
        self.prog_lbl.pack(side="left", padx=12, pady=6)
        self.cancel_btn = W.button(self.prog, "取消", self._cancel_job,
                                   small=True)
        self.cancel_btn.pack(side="right", padx=8, pady=4)

        self.after(150, self._drain)
        self.after(400, self._bootstrap)

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

    def _post(self, fn) -> None:
        self._inbox.put(fn)

    def _svc_get(self):
        if self._svc is None:
            from hc_agent.knowledge import KnowledgeService
            self._svc = KnowledgeService()
        return self._svc

    def _bootstrap(self) -> None:
        try:
            svc = self._svc_get()
            if not self._kb_id:
                self._kb_id = svc.default_kb_id()
            self._render_kbs()
            self._render()
        except Exception as exc:
            self._clear()
            W.notice(self.inner, f"知识库初始化失败：{exc}", "err")

    def _clear(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

    def _switch(self, key: str) -> None:
        self._sub = key
        for k, lb in self._tab_w.items():
            on = (k == key)
            lb.configure(fg=T.PRIMARY if on else T.MUTED,
                         font=T.F.get("bold") if on else T.F.get("base"))
        self._render()

    # ------------------------------------------------------------ 知识库
    def _render_kbs(self) -> None:
        for w in self.kb_box.winfo_children():
            w.destroy()
        svc = self._svc_get()
        for kb in svc.list_kbs():
            on = (kb["id"] == self._kb_id)
            row = tk.Frame(self.kb_box,
                           bg=T.PRIMARY_WEAK if on else T.SUBNAV_BG)
            row.pack(fill="x", padx=8, pady=1)
            name = W.label(row, kb["name"], bg=row["bg"],
                           fg=T.PRIMARY if on else T.TEXT,
                           font=T.F.get("bold") if on else T.F.get("base"),
                           anchor="w")
            name.pack(side="left", padx=6, pady=6)
            W.label(row, f"{kb['doc_count']}篇", bg=row["bg"], fg=T.FAINT,
                    font=T.F.get("tiny")).pack(side="right", padx=6)
            for w in (row, name):
                w.bind("<Button-1>", lambda e, i=kb["id"]: self._pick_kb(i))

    def _pick_kb(self, kb_id: str) -> None:
        self._kb_id = kb_id
        self._render_kbs()
        self._render()

    def _create_kb(self) -> None:
        name = simpledialog.askstring("新建知识库", "名称（如：地区细则）：",
                                      parent=self)
        if not name:
            return
        r = self._svc_get().create_kb(name.strip())
        if not r.get("ok"):
            messagebox.showwarning("新建失败", r.get("error", ""), parent=self)
            return
        self._kb_id = r["kb"]["id"]
        self._render_kbs()
        self._render()

    def _delete_kb(self) -> None:
        if not self._kb_id:
            return
        kb = self._svc_get().get_kb(self._kb_id)
        if not kb:
            return
        if kb.get("is_default"):
            messagebox.showinfo(
                "默认知识库", "默认知识库不能删除。\n"
                "如需清理，可在「文档」里逐个删除。", parent=self)
            return
        if not messagebox.askyesno(
                "确认删除",
                f"删除知识库「{kb['name']}」及其全部文档与索引？\n"
                f"此操作不可恢复。", parent=self):
            return
        r = self._svc_get().delete_kb(self._kb_id)
        if not r.get("ok"):
            messagebox.showwarning("删除失败", r.get("error", ""), parent=self)
            return
        self._kb_id = self._svc_get().default_kb_id()
        self._render_kbs()
        self._render()

    # ------------------------------------------------------------ 渲染
    def _render(self) -> None:
        self._clear()
        svc = self._svc_get()
        kb = svc.get_kb(self._kb_id)
        if not kb:
            W.notice(self.inner, "还没有知识库。点左上角「＋」新建一个。", "warn")
            return
        st = svc.stats(self._kb_id)
        self.title_lbl.configure(text=kb["name"])
        self.stat_lbl.configure(
            text=f"{st['docs']} 篇（内置 {st['builtin']} / 用户 {st['user']}）"
                 f" · {st['chunks']} 块"
                 + (f" · {st['failed']} 篇失败" if st["failed"] else ""))
        if self._sub == "docs":
            self._render_docs(svc, kb)
        elif self._sub == "search":
            self._render_search()
        elif self._sub == "jobs":
            self._render_jobs(svc)
        else:
            self._render_eval()

    def _render_docs(self, svc, kb) -> None:
        docs = svc.list_docs(self._kb_id)
        box = W.card(self.inner)
        box.pack(fill="both", expand=True)
        if not docs:
            W.notice(box.inner, "这个库里还没有文档。点右上「上传文件」"
                                "或「新建文档」开始。", "info")
            return
        tb = W.Table(box.inner, [
            {"key": "t", "text": "标题", "width": 250},
            {"key": "o", "text": "来源", "width": 55},
            {"key": "k", "text": "类型", "width": 110},
            {"key": "s", "text": "字数", "width": 65, "anchor": "e"},
            {"key": "c", "text": "块", "width": 50, "anchor": "e"},
            {"key": "st", "text": "状态", "width": 70},
        ], height=13)
        STATUS = {"indexed": "已索引", "pending": "待索引",
                  "failed": "解析失败"}
        tb.set_rows([(
            d["title"], "内置" if d["origin"] == "builtin" else "用户",
            d["kind"] or "—", d["size"], d.get("chunk_count", 0),
            STATUS.get(d["status"], d["status"]),
        ) for d in docs])
        tb.pack(fill="both", expand=True)

        errs = [d for d in docs if d["status"] == "failed"]
        if errs:
            for d in errs[:3]:
                W.notice(box.inner, f"{d['title']}：{d['error'][:160]}", "err")

        ops = tk.Frame(box.inner, bg=T.CARD)
        ops.pack(fill="x", pady=(8, 0))
        W.label(ops, "选中一行后：", bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small")).pack(side="left", padx=(0, 6))

        def _sel() -> str:
            s = tb.tree.selection()
            return str(docs[int(s[0])]["id"]) if s else ""

        def do_del() -> None:
            did = _sel()
            if not did:
                return
            d = next((x for x in docs if x["id"] == did), None)
            if not d or not messagebox.askyesno(
                    "确认删除", f"删除文档「{d['title']}」？", parent=self):
                return
            svc.delete_doc(did)
            self._render_kbs()
            self._render()

        def do_ask() -> None:
            did = _sel()
            d = next((x for x in docs if x["id"] == did), None)
            if not d or self._on_ask is None:
                return
            self._on_ask(f"知识库《{kb['name']}》里关于「{d['title']}」讲了什么？")

        W.button(ops, "删除", do_del, small=True).pack(side="left")
        W.button(ops, "问这个文档", do_ask, small=True).pack(
            side="left", padx=(6, 0))
        W.label(box.inner,
                "支持 .md / .txt / .csv / .xlsx / .docx / .pdf（PDF 需有文本层）。"
                "上传后需「重建索引」才会被检索到。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small")).pack(anchor="w", pady=(8, 0))

    def _render_search(self) -> None:
        box = W.card(self.inner)
        box.pack(fill="x")
        r = tk.Frame(box.inner, bg=T.CARD)
        r.pack(fill="x")
        self.q_var = tk.StringVar()
        e = tk.Entry(r, textvariable=self.q_var, font=T.F.get("base"),
                     bg="#FFFFFF", fg=T.TEXT, relief="solid", bd=1,
                     highlightthickness=0, insertbackground=T.TEXT)
        e.pack(side="left", fill="x", expand=True, ipady=5)
        e.bind("<Return>", lambda ev: self._do_search())
        W.button(r, "检索", self._do_search, kind="primary",
                 small=True).pack(side="left", padx=(8, 0))
        W.label(box.inner,
                "检索是「BM25 关键词 + TF-IDF 向量」混合，再按关键词覆盖、"
                "标题命中、答案承载信号重排，最后 MMR 去冗余。"
                "得分与命中词都列出来，便于判断排序是否合理。",
                bg=T.CARD, fg=T.FAINT,
                font=T.F.get("small")).pack(anchor="w", pady=(8, 0))
        self.res_box = tk.Frame(self.inner, bg=T.BG)
        self.res_box.pack(fill="both", expand=True, pady=(10, 0))
        self._render_hits([])

    def _do_search(self, q: str = "") -> None:
        query = q or (self.q_var.get() if hasattr(self, "q_var") else "")
        query = (query or "").strip()
        if not query:
            return
        try:
            hits = self._svc_get().search(query, kb_id=self._kb_id, top_k=10)
        except Exception as exc:
            self._render_hits([])
            W.notice(self.res_box, f"检索失败：{exc}", "err")
            return
        self._render_hits(hits, query)

    def _render_hits(self, hits, query: str = "") -> None:
        for w in self.res_box.winfo_children():
            w.destroy()
        if not hits:
            box = W.card(self.res_box)
            box.pack(fill="x")
            W.label(box.inner, "没有命中。试试换一种说法，"
                               "或确认该库已建索引。", bg=T.CARD,
                    fg=T.MUTED, font=T.F.get("base")).pack(anchor="w")
            return
        box = W.card(self.res_box)
        box.pack(fill="both", expand=True)
        tb = W.Table(box.inner, [
            {"key": "n", "text": "#", "width": 36, "anchor": "e"},
            {"key": "b", "text": "段落", "width": 300},
            {"key": "s", "text": "得分", "width": 65, "anchor": "e"},
            {"key": "c", "text": "覆盖", "width": 60, "anchor": "e"},
            {"key": "m", "text": "命中词", "width": 180},
        ], height=9)
        tb.set_rows([(
            i + 1, h.breadcrumb or h.chunk_id, _fmt(h.score, 3),
            _fmt(h.coverage, 2), "、".join(h.matched[:5]) or "—",
        ) for i, h in enumerate(hits)])
        tb.pack(fill="both", expand=True)
        top = hits[0]
        pv = W.card(self.res_box, pad=12)
        pv.pack(fill="x", pady=(10, 0))
        W.label(pv.inner, f"第 1 名原文 · {top.breadcrumb or top.chunk_id}",
                bg=T.CARD, fg=T.TEXT, font=T.F.get("bold")).pack(anchor="w")
        W.label(pv.inner, (top.text or "")[:900], bg=T.CARD, fg=T.MUTED,
                font=T.F.get("small"), wrap=880).pack(anchor="w", pady=(4, 0))

    def _render_jobs(self, svc) -> None:
        jobs = svc.list_jobs(self._kb_id, limit=20)
        box = W.card(self.inner)
        box.pack(fill="both", expand=True)
        if not jobs:
            W.label(box.inner, "还没有索引任务。", bg=T.CARD, fg=T.MUTED,
                    font=T.F.get("base")).pack(anchor="w")
            return
        STATUS = {"queued": "排队", "running": "进行中", "done": "完成",
                  "failed": "失败", "cancelled": "已取消"}
        tb = W.Table(box.inner, [
            {"key": "t", "text": "创建时间", "width": 165},
            {"key": "k", "text": "类型", "width": 70},
            {"key": "s", "text": "状态", "width": 75},
            {"key": "p", "text": "进度", "width": 95, "anchor": "e"},
            {"key": "m", "text": "说明", "width": 300},
        ], height=10)
        tb.set_rows([(
            (j.created_at or "")[:19], j.kind,
            STATUS.get(j.status, j.status),
            f"{j.done}/{j.total} {j.percent}%", j.message,
        ) for j in jobs])
        tb.pack(fill="both", expand=True)

    def _render_eval(self) -> None:
        box = W.card(self.inner)
        box.pack(fill="x")
        W.label(box.inner, "RAG 评估集", bg=T.CARD, fg=T.TEXT,
                font=T.F.get("bold")).pack(anchor="w")
        W.notice(box.inner,
                 "从当前知识库的段落反推生成题目（标准答案就是该段落本身），"
                 "然后自动跑一轮检索评分。**它只验证「检索能否找到出处」，"
                 "不验证回答是否正确** —— hit@1 不等于问答准确率。", "info")
        W.button(box.inner, "生成并跑分", self._run_eval,
                 kind="primary").pack(anchor="w", pady=(10, 0))
        self.eval_box = tk.Frame(self.inner, bg=T.BG)
        self.eval_box.pack(fill="both", expand=True, pady=(10, 0))

    # ------------------------------------------------------------ 操作
    def _upload(self) -> None:
        files = filedialog.askopenfilenames(
            title="上传到知识库",
            filetypes=[("支持的文档", "*.md *.txt *.markdown *.csv *.xlsx "
                                    "*.docx *.pdf"),
                       ("所有文件", "*.*")])
        if not files:
            return
        svc = self._svc_get()
        try:
            r = svc.add_files(self._kb_id, files)
        except Exception as exc:
            messagebox.showwarning("上传失败", str(exc), parent=self)
            return
        msg = f"已上传 {r['added']} 个文件"
        if r.get("failed"):
            bad = [f"{i['file']}: {i.get('error','')}"
                   for i in r["items"] if not i.get("ok")]
            msg += f"，失败 {r['failed']} 个：\n" + "\n".join(bad[:5])
        messagebox.showinfo("上传结果", msg, parent=self)
        self._render_kbs()
        self._render()
        # 上传后自动建索引，省掉"传完还要点一次"
        if r.get("added"):
            self._start_index(rebuild=False)

    def _new_doc(self) -> None:
        win = tk.Toplevel(self)
        win.title("新建文档")
        win.configure(bg=T.BG)
        win.geometry("660x440")
        win.transient(self.winfo_toplevel())
        win.grab_set()
        body = tk.Frame(win, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        W.label(body, "标题", bg=T.BG, fg=T.MUTED,
                font=T.F.get("small")).pack(anchor="w")
        e_title = tk.Entry(body, font=T.F.get("base"), bg="#FFFFFF",
                           fg=T.TEXT, relief="solid", bd=1,
                           highlightthickness=0, insertbackground=T.TEXT)
        e_title.pack(fill="x", ipady=5, pady=(2, 8))
        W.label(body, "正文（支持 Markdown；用 ## 分节有利于检索）",
                bg=T.BG, fg=T.MUTED,
                font=T.F.get("small")).pack(anchor="w")
        txt = tk.Text(body, font=T.F.get("base"), bg="#FFFFFF", fg=T.TEXT,
                      relief="solid", bd=1, highlightthickness=0,
                      insertbackground=T.TEXT, wrap="word")
        txt.pack(fill="both", expand=True, pady=(2, 10))

        def save() -> None:
            title = e_title.get().strip()
            content = txt.get("1.0", "end").strip()
            if not title or not content:
                messagebox.showwarning("内容不完整", "标题与正文不能为空",
                                       parent=win)
                return
            r = self._svc_get().add_text(self._kb_id, title, content,
                                        auto_index=False)
            if not r.get("ok"):
                messagebox.showwarning("保存失败", r.get("error", ""),
                                       parent=win)
                return
            win.destroy()
            self._render_kbs()
            self._render()
            self._start_index(rebuild=False)

        btns = tk.Frame(body, bg=T.BG)
        btns.pack(fill="x")
        W.button(btns, "保存", save, kind="primary").pack(side="left")
        W.button(btns, "取消", win.destroy).pack(side="left", padx=8)

    def _rebuild(self) -> None:
        self._start_index(rebuild=True)

    def _start_index(self, *, rebuild: bool) -> None:
        if self._busy_job:
            messagebox.showinfo("请稍候", "已有索引任务在进行。", parent=self)
            return
        svc = self._svc_get()
        try:
            job = (svc.rebuild_index(self._kb_id) if rebuild
                   else svc.index_kb(self._kb_id))
        except Exception as exc:
            messagebox.showwarning("提交失败", str(exc), parent=self)
            return
        self._busy_job = job.job_id
        self.prog.pack(fill="x", side="bottom")
        self.prog_lbl.configure(text="索引中…")
        self._poll_job(job.job_id)

    def _poll_job(self, job_id: str) -> None:
        if not self._alive():
            return
        try:
            p = self._svc_get().job_progress(job_id)
        except Exception:
            p = None
        if p is None:
            self._busy_job = None
            self.prog.pack_forget()
            return
        self.prog_lbl.configure(
            text=f"{p.kind} · {p.status} · {p.done}/{p.total} {p.percent}%"
                 f" · {p.message[:70]}")
        if p.running:
            self.after(300, lambda: self._poll_job(job_id))
            return
        self._busy_job = None
        self.prog.pack_forget()
        self._render_kbs()
        if self._sub in ("docs", "jobs"):
            self._render()

    def _cancel_job(self) -> None:
        if not self._busy_job:
            return
        r = self._svc_get().cancel_job(self._busy_job)
        self.prog_lbl.configure(text=r.get("error") or "已请求取消…")

    def _run_eval(self) -> None:
        self._sub = "eval"
        self._switch("eval")
        self._clear()
        box = W.card(self.inner)
        box.pack(fill="x")
        W.label(box.inner, "正在生成评估集并跑分…", bg=T.CARD, fg=T.PRIMARY,
                font=T.F.get("base")).pack(anchor="w")

        def _work() -> None:
            try:
                from hc_agent.knowledge.eval import run_eval
                r = run_eval(self._svc_get(), self._kb_id, max_items=20)
            except Exception as exc:
                r = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._post(lambda: self._show_eval(r))

        threading.Thread(target=_work, daemon=True, name="kb-eval").start()

    def _show_eval(self, r: Dict[str, Any]) -> None:
        if not self._alive():
            return
        self._render()
        box = W.card(self.eval_box)
        box.pack(fill="x")
        if not r.get("ok"):
            W.notice(box.inner, f"评估失败：{r.get('error')}", "err")
            return
        row = tk.Frame(box.inner, bg=T.CARD)
        row.pack(fill="x")
        for label, val in (("题数", r.get("count")),
                           ("hit@1", r.get("hit@1")),
                           ("hit@3", r.get("hit@3")),
                           ("MRR", r.get("mrr"))):
            c = W.kpi(row, label, str(val))
            c.pack(side="left", fill="both", expand=True, padx=(0, 8))
        if r.get("note"):
            W.notice(box.inner, r["note"], "info")
        sm = r.get("samples") or []
        if sm:
            W.label(box.inner, "题目样例", bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold")).pack(anchor="w", pady=(8, 4))
            tb = W.Table(box.inner, [
                {"key": "q", "text": "问题", "width": 300},
                {"key": "e", "text": "标准答案（段落）", "width": 280},
                {"key": "r", "text": "命中排名", "width": 80,
                 "anchor": "e"},
            ], height=7)
            tb.set_rows([(x["question"], x["expected"][:60],
                          x["rank"] or "未命中") for x in sm])
            tb.pack(fill="x")
        if r.get("misses"):
            W.label(box.inner, "未在首位命中的题", bg=T.CARD, fg=T.TEXT,
                    font=T.F.get("bold")).pack(anchor="w", pady=(8, 4))
            for m in r["misses"][:5]:
                W.label(box.inner,
                        f"· {m['question']} → 实际第 {m['rank'] or '—'} 位："
                        f"{m['got'][:50]}", bg=T.CARD, fg=T.MUTED,
                        font=T.F.get("small"), wrap=860).pack(anchor="w")
