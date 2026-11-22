# -*- coding: utf-8 -*-
"""重打素材包：加入已编译的安装包 exe，并多剔一类开发产物，控制在 1000 MB 内。

新增剔除（相对上一版）：*.tsbuildinfo、*.pdb、node_modules 内的 *.md
保留：.js/.mjs/.cjs/.ts 源码、.node/.dll 二进制、package.json 等
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SRC = Path(r"C:\Users\Chen\Desktop\配网承载力智能体_安装包制作_精简版.zip")
TMP = SRC.with_name("_repack_final.zip")
EXE = Path(r"C:\Users\Chen\Desktop\配网承载力智能体-1.1.0-试用版.exe")
DSH = Path(r"C:\Users\Chen\Desktop\配网承载力智能体\配网承载力智能体Agent\deepseek-harness")
KIT_PREFIX = "配网承载力智能体_安装包制作/"
DSH_PREFIX = KIT_PREFIX + "files/03_底层框架源码/deepseek-harness/"

DROP_SUFFIX = (".map", ".d.ts", ".d.mts", ".d.cts", ".tsbuildinfo", ".pdb")
DROP_DIRS = {"test", "tests", "__tests__", "coverage", "example", "examples"}


def keep(rel: str) -> bool:
    low = rel.lower()
    if low.endswith(DROP_SUFFIX):
        return False
    parts = low.split("/")
    if set(parts[:-1]) & DROP_DIRS:
        return False
    # node_modules 内的 markdown 文档（项目自身的 md 保留）
    if "node_modules" in parts and low.endswith(".md"):
        return False
    # node_modules 内的 TypeScript 源码（运行时用 .js）
    if "node_modules" in parts and low.endswith((".ts", ".mts", ".cts")):
        return False
    return True


def main() -> int:
    for p in (SRC, EXE, DSH):
        if not p.exists():
            print("缺输入：", p)
            return 1
    if TMP.exists():
        TMP.unlink()

    n_kit = n_added = n_dropped = 0
    with zipfile.ZipFile(SRC) as src, \
            zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        # 1) 原包里的 kit 部分（跳过 DSH 与旧程序，后面按新规则重加）
        for info in src.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if name.startswith(DSH_PREFIX):
                continue
            with src.open(info) as fh:
                dst.writestr(name, fh.read(), compress_type=zipfile.ZIP_DEFLATED)
            n_kit += 1
            if n_kit % 20000 == 0:
                print(f"  kit {n_kit} …", flush=True)

        # 2) 安装包 exe 放进 kit 根目录
        dst.write(EXE, KIT_PREFIX + EXE.name)
        print("  已加入安装包:", EXE.name, flush=True)

        # 3) DSH 源码按新规则重加
        for p in DSH.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(DSH).as_posix()
            if not keep(rel):
                n_dropped += 1
                continue
            try:
                dst.write(p, DSH_PREFIX + rel)
                n_added += 1
            except OSError:
                n_dropped += 1
            if n_added % 20000 == 0:
                print(f"  DSH +{n_added} …", flush=True)

    size = TMP.stat().st_size / 1024 / 1024
    print(f"kit {n_kit}，DSH 加入 {n_added}，剔除 {n_dropped}")
    print(f"新 zip: {size:.1f} MB")
    if size >= 1000:
        print("仍超 1000 MB，保留原包")
        TMP.unlink()
        return 2
    SRC.unlink()
    TMP.rename(SRC)
    print("已替换原包")
    return 0


if __name__ == "__main__":
    sys.exit(main())
