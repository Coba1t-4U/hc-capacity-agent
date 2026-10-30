"""Validate the dashboard's inline JavaScript by parsing it with Node.

Serving HTTP 200 does not prove the page's script runs, and a broken
single-file bundle can silently render only the header. This checks:
  1. the inline script parses as JavaScript;
  2. every element id referenced by $() exists in the HTML;
  3. for a standalone bundle, the inline data is present and the boot()
     try/catch structure is intact.

Usage: python tools/check_dashboard.py [more html files...]
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TARGETS = sys.argv[1:] or ["web/index.html"]
NODE = (r"C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime"
        r"\dependencies\node\bin\node.exe")
if not Path(NODE).exists():
    NODE = "node"

failed = False


def check(path: str) -> None:
    global failed
    p = Path(path)
    if not p.exists():
        print(f"[skip] {path} 不存在")
        return
    src = p.read_text(encoding="utf-8")
    print(f"\n=== {path} ({len(src)} bytes) ===")

    blocks = re.findall(r"<script>(.*?)</script>", src, re.S)
    if not blocks:
        print("  FAIL: 没有 <script> 块")
        failed = True
        return

    ok = True
    for i, js in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(js)
            tmp = fh.name
        r = subprocess.run(
            [NODE, "-e",
             f"new Function(require('fs').readFileSync({tmp!r},'utf8'))"],
            capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  FAIL: 第 {i+1} 个 <script> 语法错误：")
            print("   ", (r.stderr or "").strip().splitlines()[-1] if r.stderr
                  else "unknown")
            ok = failed = True
    if ok:
        print(f"  OK: {len(blocks)} 个 <script> 块语法正确")

    # DOM id 引用一致性
    ids = set(re.findall(r'id="([^"]+)"', src))
    refs = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', src))
    missing = sorted(refs - ids)
    if missing:
        print(f"  FAIL: JS 引用了不存在的元素 id: {missing}")
        failed = True
    else:
        print(f"  OK: JS 引用的 {len(refs)} 个元素 id 均存在")

    # 单文件版专项检查
    if "dashboard_standalone" in p.name:
        if "window.__INLINE_DATA__ = " not in src:
            print("  FAIL: 缺少内联数据")
            failed = True
        else:
            print("  OK: 内联数据存在")
        if "if (window.__INLINE_DATA__)" not in src:
            print("  FAIL: boot() 未接入内联数据分支（页面将只渲染页头）")
            failed = True
        else:
            print("  OK: boot() 已接入内联数据分支")
        # try/catch 完整性
        if src.count("try {") and src.count("} catch") < src.count("try {"):
            print("  FAIL: try/catch 结构不完整")
            failed = True


for t in TARGETS:
    check(t)

print("\n" + ("全部检查通过" if not failed else "存在未通过项"))
sys.exit(1 if failed else 0)
