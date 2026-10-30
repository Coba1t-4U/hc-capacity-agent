"""启动桌面版（在 PyCharm 里直接右键 Run 这个文件即可）。

等价于 `python -m hc_agent.desktop.app`。
"""
from hc_agent.desktop.app import main

if __name__ == "__main__":
    raise SystemExit(main())
