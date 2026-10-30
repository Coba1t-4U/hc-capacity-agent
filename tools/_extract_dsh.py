# -*- coding: utf-8 -*-
"""解包 deepseek-harness 源码 tarball（Windows 友好）。

两个 Windows 坑：
  1. 仓库里有**符号链接**（.claude/skills -> ../.agents/skills），
     bsdtar 在非管理员/未开开发者模式下创建不了，会直接报
     "Invalid argument" 并中断整个解包；
  2. monorepo 路径很深，超过 260 字符的 MAX_PATH 会失败。

做法：用 Python tarfile 逐个成员解，符号链接跳过并记录（之后再按副本补），
普通文件加 \\\\?\\ 前缀绕过长路径限制。
"""
from __future__ import annotations

import os
import sys
import tarfile
from pathlib import Path

SRC = Path(os.environ.get("TEMP", ".")) / "dsh-src.tar.gz"
DST = Path.home() / "Desktop" / "deepseek-harness-开源版"


def longpath(p: Path) -> str:
    s = str(p.resolve()) if p.exists() else str(p)
    if os.name == "nt" and not s.startswith("\\\\?\\") and len(s) > 240:
        return "\\\\?\\" + s
    return s


def main() -> int:
    if not SRC.is_file():
        print(f"tarball 不存在：{SRC}")
        return 2
    DST.mkdir(parents=True, exist_ok=True)

    n_files = n_dirs = n_skip = 0
    symlinks: list[tuple[str, str]] = []
    failures: list[tuple[str, str]] = []

    with tarfile.open(SRC, "r:gz") as tf:
        members = tf.getmembers()
        print(f"归档成员总数: {len(members)}")
        for m in members:
            parts = m.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue                      # 顶层目录本身
            rel = parts[1]
            target = DST / rel
            if m.issym() or m.islnk():
                symlinks.append((rel, m.linkname))
                n_skip += 1
                continue
            try:
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    n_dirs += 1
                    continue
                if not m.isfile():
                    n_skip += 1               # 设备节点等，不需要
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    continue
                with open(longpath(target), "wb") as fh:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                n_files += 1
            except Exception as exc:          # 单个文件失败不应中断整体
                failures.append((rel, f"{type(exc).__name__}: {exc}"))
            if n_files % 2000 == 0 and n_files:
                print(f"  … 已解出 {n_files} 个文件")

    print(f"\n完成：文件 {n_files} 个，目录 {n_dirs} 个，跳过 {n_skip} 个（符号链接/特殊项）")
    if symlinks:
        print(f"\n符号链接 {len(symlinks)} 个（未创建，需手工处理）：")
        for name, link in symlinks[:12]:
            print(f"  {name}  ->  {link}")
    if failures:
        print(f"\n失败 {len(failures)} 个：")
        for name, why in failures[:10]:
            print(f"  {name}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
