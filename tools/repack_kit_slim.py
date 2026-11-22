# -*- coding: utf-8 -*-
"""用已解出的 kit 目录 + 过滤后的 DSH 源码，重打一个 <1000 MB 的素材包。

kit 目录由 .NET 从旧 zip 解出（旧 zip 的分隔符不规范，Python 读不了）。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

KIT_DIR = Path(r"C:\Users\Chen\AppData\Local\Temp\hckit_extract")
OUT_ZIP = Path(r"C:\Users\Chen\Desktop\配网承载力智能体_安装包制作_精简版.zip")
DSH = Path(r"C:\Users\Chen\Desktop\配网承载力智能体\配网承载力智能体Agent\deepseek-harness")
KIT_PREFIX = "配网承载力智能体_安装包制作/"
DSH_PREFIX = KIT_PREFIX + "files/03_底层框架源码/deepseek-harness/"

DROP_SUFFIX = (".map", ".d.ts", ".d.mts", ".d.cts")
DROP_DIRS = {"test", "tests", "__tests__", "coverage", "example", "examples"}


def keep(rel: str) -> bool:
    low = rel.lower()
    if low.endswith(DROP_SUFFIX):
        return False
    parts = set(low.split("/")[:-1])
    return not (parts & DROP_DIRS)


def main() -> int:
    if not KIT_DIR.is_dir():
        print("找不到 kit 目录")
        return 1
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()

    kit_root = KIT_DIR / "配网承载力智能体_安装包制作"
    n_kit = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for p in kit_root.rglob("*"):
            if p.is_file():
                dst.write(p, KIT_PREFIX + p.relative_to(kit_root).as_posix())
                n_kit += 1
        print(f"kit: {n_kit} 个", flush=True)

        added = dropped = 0
        for p in DSH.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(DSH).as_posix()
            if not keep(rel):
                dropped += 1
                continue
            try:
                dst.write(p, DSH_PREFIX + rel)
                added += 1
            except OSError:
                dropped += 1
            if added % 20000 == 0:
                print(f"  DSH +{added} …", flush=True)

    size = OUT_ZIP.stat().st_size / 1024 / 1024
    print(f"DSH: 加入 {added}，剔除 {dropped}")
    print(f"新 zip: {size:.1f} MB")
    return 0 if size < 1000 else 2


if __name__ == "__main__":
    sys.exit(main())
