# -*- coding: utf-8 -*-
"""zip 完成后收尾：解压 → 编译安装包 → 放桌面 → 清理临时与下载内容。

保留：桌面上的 zip（素材包）与编译出的 setup.exe
清理：解压目录、build/offline 工作包、TEMP 里的下载件
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

DESK = Path.home() / "Desktop"
ZIP = DESK / "配网承载力智能体_安装包制作_离线版.zip"
ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
ISCC = Path(r"C:\Users\Chen\AppData\Local\Programs\Inno Setup 6\ISCC.exe")


def main() -> int:
    if not ZIP.is_file():
        print("找不到 zip：", ZIP)
        return 1
    if not ISCC.is_file():
        print("找不到 ISCC：", ISCC)
        return 1

    work = Path(tempfile.mkdtemp(prefix="hckit_"))
    print("解压到", work, flush=True)
    shutil.unpack_archive(str(ZIP), str(work))

    roots = [p for p in work.iterdir() if p.is_dir()]
    kit = roots[0] if roots else work
    iss = next(kit.glob("*.iss"), None)
    if iss is None:
        print("解压后找不到 .iss")
        return 1
    print("安装脚本：", iss.name, flush=True)

    print("编译中（约 40–90 秒）…", flush=True)
    r = subprocess.run([str(ISCC), str(iss)], cwd=str(kit), capture_output=True, text=True)
    tail = (r.stdout or "")[-400:]
    print(tail, flush=True)
    if r.returncode != 0:
        print("编译失败：", (r.stderr or "")[-400:])
        return 1

    out_dir = kit / "release"
    exes = sorted(out_dir.glob("*.exe")) if out_dir.is_dir() else []
    if not exes:
        exes = sorted(kit.rglob("*试用版.exe"))
    if not exes:
        print("编译成功但找不到产物")
        return 1

    src = exes[-1]
    dst = DESK / src.name
    shutil.copy2(src, dst)
    print(f"已放桌面：{dst.name}  {dst.stat().st_size/1024/1024:.1f} MB", flush=True)

    # 清理
    print("清理临时目录…", flush=True)
    shutil.rmtree(work, ignore_errors=True)
    for d in (ROOT / "build" / "offline", ROOT / "build" / "pack_kit"):
        if d.exists():
            import os, stat
            for root, _dirs, files in os.walk(d):
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), stat.S_IWRITE)
                    except Exception:
                        pass
            shutil.rmtree(d, ignore_errors=True)
            print("  已删", d.relative_to(ROOT), flush=True)

    for pat in ("python-3.12*-amd64.exe", "hc_req*.txt", "hc_wheels_req*.txt"):
        for f in Path(tempfile.gettempdir()).glob(pat):
            try:
                f.unlink()
            except Exception:
                pass

    print("完成。桌面保留：zip 素材包 + 编译出的 setup.exe", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
