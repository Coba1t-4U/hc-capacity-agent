"""一键跑完全部自检（部分套件需要 dist/ 会失败，属预期）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = sorted((ROOT / "tests").glob("*.py"))
SKIP = {"__init__.py"}

total_ok = total_all = 0
failed = []
for t in TESTS:
    if t.name in SKIP:
        continue
    r = subprocess.run([sys.executable, str(t)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    line = next((ln.strip() for ln in reversed(r.stdout.splitlines())
                 if "结果：" in ln), "")
    print(f"{t.name:<28} exit={r.returncode}  {line}")
    if r.returncode != 0:
        failed.append(t.name)
    import re
    m = re.search(r"(\d+)\s*通过", line)
    n = re.search(r"/\s*(\d+)\s*总计", line)
    if m:
        total_ok += int(m.group(1))
    if n:
        total_all += int(n.group(1))

print()
print(f"合计 {total_ok} 通过 / {total_all - total_ok} 失败 / {total_all} 项")
if failed:
    print("失败套件：", "、".join(failed))
raise SystemExit(1 if failed else 0)
