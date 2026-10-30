"""chat_runtime.py — 问答的"后台运行器"：切页、切会话都不影响它算完。

## 原来的问题

提问是在 `ChatView` 里起线程、算完调 `view.deliver()`。于是：

* **切到别的页面** → ChatPage 控件被销毁 → 答案投到一个已死的视图上，消息丢了；
* **切到另一个会话** → 答案会插到**当前显示的那个会话**里，张冠李戴；
* 用户也看不到"这条还在算"的状态。

## 现在的做法

把计算提到 App 层，结果按 **session_id** 落到数据库（`chat.sqlite`）。

```
用户提问 ──▶ ChatRuntime.submit(session_id, q)
                │
                ├─ 立刻把用户消息写库（谁都不能丢）
                ├─ 后台线程算（qa / DeepSeek）
                ├─ 算完把答案写库（按 session_id）
                └─ 如果当前正挂着**同一个** session 的视图 → 顺带推一条气泡
                        （视图不在就算了，库里有，回来看得到）
```

这样：

| 场景 | 结果 |
|---|---|
| 提问后切到"运行总览"再切回对话 | 答案在，因为早就写库了 |
| 提问后切到另一个会话 | 答案进**原来那个会话**，不串台 |
| 切页期间算完 | 回对话页时会话列表有 ⏳→完成，内容在 |
| 关掉应用前算完 | 一样写库 |
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Set


class ChatRuntime:
    """管所有"正在算"的问答，按会话隔离。"""

    def __init__(self, history_getter: Callable[[], Any]):
        #: 传 getter 而不是实例：历史库是懒加载的，App 启动时可能还没有
        self._history_getter = history_getter
        self._lock = threading.RLock()
        #: session_id -> 正在算的问题
        self._inflight: Dict[str, str] = {}
        #: session_id -> 当前挂着的视图（弱引用语义：用 winfo_exists 判活）
        self._sinks: Dict[str, Any] = {}
        #: 状态变化回调（刷新侧栏用）
        self._on_change: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------ 基本状态
    def set_on_change(self, cb: Optional[Callable[[], None]]) -> None:
        self._on_change = cb

    def _notify(self) -> None:
        cb = self._on_change
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def inflight(self, session_id: str) -> bool:
        with self._lock:
            return bool(session_id) and session_id in self._inflight

    def inflight_question(self, session_id: str) -> str:
        with self._lock:
            return self._inflight.get(session_id, "")

    def busy_sessions(self) -> Set[str]:
        with self._lock:
            return set(self._inflight)

    # ------------------------------------------------------------ 视图绑定
    def bind_view(self, session_id: str, view) -> None:
        """当前正在显示某个会话的视图。答案算完会顺便推给它。

        一个视图**只属于一个会话**：切换会话时必须把旧登记清掉，
        否则"A 会话算完的答案"会被推进"正在显示 B 的视图"，
        看着就像答案串台了（实测踩过）。
        """
        with self._lock:
            for sid, v in list(self._sinks.items()):
                if v is view and sid != session_id:
                    self._sinks.pop(sid, None)
            self._sinks[session_id] = view

    def unbind_view(self, view) -> None:
        """视图销毁时调用，避免往死控件上投。"""
        with self._lock:
            for sid, v in list(self._sinks.items()):
                if v is view:
                    self._sinks.pop(sid, None)

    def _sink_for(self, session_id: str):
        with self._lock:
            v = self._sinks.get(session_id)
        if v is None:
            return None
        try:
            if not v.winfo_exists():
                return None
        except Exception:
            return None
        return v

    # ------------------------------------------------------------ 提交
    def submit(self, session_id: str, question: str) -> bool:
        """提交一个问题。返回 False 表示同一会话已有问题在算。"""
        q = (question or "").strip()
        if not q:
            return False
        history = self._history_getter()
        if history is None:
            return False
        sid = session_id or ""
        if not sid:
            try:
                sid = (history.ensure_session() or {}).get("id", "")
            except Exception:
                sid = ""
        if not sid:
            return False

        with self._lock:
            if sid in self._inflight:
                return False
            self._inflight[sid] = q

        # 用户消息**先写库**：即使后面算崩了，提问也不丢
        try:
            history.add_message(sid, "user", q)
        except Exception:
            pass
        self._notify()

        def _work() -> None:
            payload: Dict[str, Any]
            try:
                from hc_agent import llm
                from hc_agent.qa import Facts, Responder
                # 每次提问都重读事实：用户可能刚跑完新任务
                facts = Facts()
                cfg = llm.LLMConfig.from_env()
                if cfg.enabled:
                    ans = llm.ask(q, facts, cfg=cfg)
                else:
                    ans = Responder(facts).answer(q).to_dict()
                    ans["engine"] = "rule"
                # 引擎选择：OpenDSS 原样返回；双引擎补一段交叉校核；
                # pandapower 模式只给正序平衡结果（不产出容量）。
                try:
                    from hc_agent.engines import selector as _eng_sel
                    ans = _eng_sel.apply_mode(ans)
                except Exception:
                    pass
                payload = dict(ans)
            except BaseException as exc:
                import traceback
                payload = {
                    "text": (f"回答时出错：{type(exc).__name__}: {exc}\n"
                             f"```\n{traceback.format_exc()[-400:]}\n```"),
                    "error": True,
                    "engine": "",
                }
            # 答案写库（按原会话 id，不受当前显示会话影响）
            try:
                history.add_message(
                    sid, "assistant", payload.get("text", ""),
                    engine=payload.get("engine", ""),
                    source=payload.get("source", ""),
                    report_path=payload.get("report_path", ""),
                    report_label=payload.get("report_label", ""))
            except Exception:
                pass
            with self._lock:
                self._inflight.pop(sid, None)
            # 如果这个会话正显示着，顺带推一条气泡
            sink = self._sink_for(sid)
            if sink is not None:
                try:
                    payload = dict(payload)
                    payload["_persisted"] = True     # 已写库，视图别再存一遍
                    sink.deliver(payload)
                except Exception:
                    pass
            self._notify()

        threading.Thread(target=_work, daemon=True,
                         name=f"qa-{sid[:8]}").start()
        return True
