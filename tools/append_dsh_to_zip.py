# -*- coding: utf-8 -*-
"""把 DSH 底层框架源码追加进安装包制作 zip（不复制到暂存目录，直接写 zip）。

122,913 个文件 / 2.3 GB，耗时长，放后台跑。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ZIP = Path(r"C:\Users\Chen\Desktop\配网承载力智能体_安装包制作_离线版.zip")
DSH = Path(r"C:\Users\Chen\Desktop\配网承载力智能体\配网承载力智能体Agent\deepseek-harness")
PREFIX = "配网承载力智能体_安装包制作/files/03_底层框架源码/deepseek-harness/"

SKIP_DIRS: set[str] = set()   # 全部纳入（含 node_modules，共 12 万个文件）


def main() -> None:
    if not DSH.is_dir():
        raise SystemExit(f"找不到 DSH 源码：{DSH}")
    if not ZIP.is_file():
        raise SystemExit(f"找不到 zip：{ZIP}")

    total = sum(1 for _ in DSH.rglob("*") if _.is_file())
    print(f"待追加 {total} 个文件", flush=True)

    added = 0
    skipped = 0
    with zipfile.ZipFile(ZIP, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        existing = set(z.namelist())
        for p in DSH.rglob("*"):
            if not p.is_file():
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(DSH).parts):
                skipped += 1
                continue
            rel = p.relative_to(DSH).as_posix()
            arc = PREFIX + rel
            if arc in existing:
                skipped += 1
                continue
            try:
                z.write(p, arc)
                added += 1
            except (OSError, zipfile.BadZipFile) as exc:
                print(f"  跳过 {rel}: {type(exc).__name__}", flush=True)
                skipped += 1
                continue
            if added % 5000 == 0:
                print(f"  已追加 {added} …", flush=True)

    size = ZIP.stat().st_size / 1024 / 1024
    print(f"完成：追加 {added}，跳过 {skipped}")
    print(f"zip 现在 {size:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
