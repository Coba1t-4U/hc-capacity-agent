# -*- coding: utf-8 -*-
"""命令行入口：导出成品评估报告（逻辑在 hc_agent/report_export.py）。

界面上的「下载成品报告」按钮调用的是同一个函数，两条路径不会各写一套。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hc_agent import report_export  # noqa: E402

if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "ACCEPT" / "FD002"
    out = report_export.export(run)
    print(f"已生成：{out}")
    print(f"大小：{out.stat().st_size / 1024:.0f} KB")