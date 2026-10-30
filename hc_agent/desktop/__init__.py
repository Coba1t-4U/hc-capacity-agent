"""hc_agent.desktop — 原生桌面客户端。

**这是原生应用，不是网页**：
  * 界面由 Tk（Tkinter，Python 标准库）绘制，进程内没有 Chromium / Electron；
  * 不加载 HTML，不存在"页面"这个概念；
  * 计算后端作为**受管子进程**运行，桌面端是它的监督者
    （因此看门狗"退出码 70 请求重启"的契约在这里才有意义）。

设计令牌与 `hc_agent/static/styles.css` 同源，均对齐「天枢一号」桌面端。
"""

__all__ = ["app", "theme", "widgets", "backend", "pages"]
__version__ = "1.0.0"
