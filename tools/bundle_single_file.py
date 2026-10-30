"""bundle_single_file.py — 把 data.json 内联进 HTML，生成可双击打开的单文件版。

用途：需要在无服务器、无网络的环境下交付或演示时使用。
页面本身不引用任何 CDN/字体/外部脚本，内联数据后即可完全离线运行。

用法：
  python tools/bundle_single_file.py [--html web/index.html]
                                     [--data web/data.json]
                                     [--out web/dashboard_standalone.html]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 把 boot() 里的 fetch 分支换成直接使用内联数据。
# 保留原有的错误提示逻辑作为兜底（例如 HTML 被截断时）。
INLINE_HOOK = """
/* ---- 单文件离线版：数据已内联，跳过 fetch ---- */
window.__INLINE_DATA__ = __DATA_JSON__;

/* ---- 单文件离线版：数据已内联，跳过 fetch ---- */
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成单文件离线版监控台")
    ap.add_argument("--html", default="web/index.html")
    ap.add_argument("--data", default="web/data.json")
    ap.add_argument("--out", default="web/dashboard_standalone.html")
    args = ap.parse_args()

    html_p = Path(args.html)
    data_p = Path(args.data)
    out_p = Path(args.out)
    if not html_p.exists():
        print(f"ERROR: 缺少 {html_p}", file=sys.stderr)
        return 1
    if not data_p.exists():
        print(f"ERROR: 缺少 {data_p}（先运行 tools/export_dashboard.py）",
              file=sys.stderr)
        return 1

    html = html_p.read_text(encoding="utf-8")
    # 直接内联为 JS 字面量；转义 </ 以免提前结束脚本块
    payload = data_p.read_text(encoding="utf-8")
    payload = payload.replace("</", "<\\/")

    # 在 </head> 前插入内联数据
    html = html.replace(
        "</head>",
        f'<script>window.__INLINE_DATA__ = {payload};</script>\n</head>', 1)

    # 把 boot() 的数据获取改为：有内联数据就用内联数据，否则 fetch。
    # 注意必须保持 try/catch 结构完整——早先用 "if (true) {...} else {...}"
    # 改写会切断后面的 catch，页面只剩页头、内容区为空（实测踩过）。
    old = """  try {
    const r = await fetch("data.json", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    DATA = await r.json();
  } catch (e) {"""
    new = """  try {
    if (window.__INLINE_DATA__) {
      DATA = window.__INLINE_DATA__;
    } else {
      const r = await fetch("data.json", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      DATA = await r.json();
    }
  } catch (e) {"""
    if old not in html:
        print("ERROR: 未找到预期的 boot() 数据获取结构，页面可能已变更。\n"
              "       请同步更新 bundle_single_file.py 中的 old 片段。",
              file=sys.stderr)
        return 1
    html = html.replace(old, new, 1)

    out_p.write_text(html, encoding="utf-8")
    print(f"wrote {out_p} ({out_p.stat().st_size} bytes)")
    print("双击该文件即可离线查看，无需服务器。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
