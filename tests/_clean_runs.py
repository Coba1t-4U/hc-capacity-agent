"""Clear read-only attributes then remove a run directory (Windows-safe)."""
import shutil
import stat
import sys
from pathlib import Path

for name in sys.argv[1:]:
    r = Path(name)
    if not r.exists():
        continue
    for p in r.rglob("*"):
        try:
            if p.is_dir():
                p.chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            else:
                p.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
    try:
        r.chmod(stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except OSError:
        pass
    shutil.rmtree(r, ignore_errors=True)
    print(f"removed {r}")
