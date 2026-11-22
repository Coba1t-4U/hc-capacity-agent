# -*- coding: utf-8 -*-
"""把素材包 zip 里的 files/01_程序包/HC-Agent 换成最新 dist。

只替换该目录下的条目，其余原样拷贝（保持 level 9，控制在 1000 MB 内）。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SRC = Path(r"C:\Users\Chen\Desktop\配网承载力智能体_安装包制作_精简版.zip")
TMP = SRC.with_name("_repack_dist.zip")
DIST = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project\dist\HC-Agent")
PREFIX = "配网承载力智能体_安装包制作/files/01_程序包/HC-Agent/"


def main() -> int:
    if not SRC.is_file() or not DIST.is_dir():
        print("缺输入")
        return 1
    if TMP.exists():
        TMP.unlink()

    n_copy = n_skip = n_new = 0
    with zipfile.ZipFile(SRC) as src, \
            zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for info in src.infolist():
            if info.is_dir():
                continue
            if info.filename.startswith(PREFIX):
                n_skip += 1
                continue
            with src.open(info) as fh:
                dst.writestr(info.filename, fh.read(), compress_type=zipfile.ZIP_DEFLATED)
            n_copy += 1
            if n_copy % 20000 == 0:
                print(f"  拷贝 {n_copy} …", flush=True)

        for p in DIST.rglob("*"):
            if p.is_file():
                dst.write(p, PREFIX + p.relative_to(DIST).as_posix())
                n_new += 1

    size = TMP.stat().st_size / 1024 / 1024
    print(f"原包拷贝 {n_copy}，替换程序文件 {n_skip} → 新写 {n_new}")
    print(f"新 zip: {size:.1f} MB")
    if size >= 1000:
        print("超过 1000 MB，保留原包")
        TMP.unlink()
        return 2
    SRC.unlink()
    TMP.rename(SRC)
    print("已替换原包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
