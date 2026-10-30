"""``python -m hc_agent.desktop`` — 启动原生桌面应用。"""

from __future__ import annotations

import sys

from hc_agent.desktop.app import main

if __name__ == "__main__":
    sys.exit(main())
