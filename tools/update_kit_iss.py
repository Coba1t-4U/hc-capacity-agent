# -*- coding: utf-8 -*-
"""重写素材包 zip，把里面的 .iss 换成最新的 packaging/setup_current.iss。

zip 不支持原地替换单个条目，只能全量重写（保持 level 9，控制在 1000 MB 内）。
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

SRC = Path(r"C:\Users\Chen\Desktop\配网承载力智能体_安装包制作_精简版.zip")
NEW_ISS = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project\packaging\setup_current.iss")
TMP = SRC.with_name("_repack_tmp.zip")
ISS_ARC = "配网承载力智能体_安装包制作/配网承载力智能体_试用版.iss"

# .iss 里的 Source 指向 dist\HC-Agent，但包内结构是 files\01_程序包\HC-Agent，需改写
def adjust(iss_text: str) -> str:
    return iss_text.replace('"..\\dist\\HC-Agent"', '"files\\01_程序包\\HC-Agent"')


def main() -> int:
    if not SRC.is_file() or not NEW_ISS.is_file():
        print("缺文件")
        return 1
    if TMP.exists():
        TMP.unlink()

    new_iss = adjust(NEW_ISS.read_text(encoding="utf-8")).encode("utf-8")
    total = 0
    with zipfile.ZipFile(SRC) as src, \
            zipfile.ZipFile(TMP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as dst:
        for info in src.infolist():
            if info.filename == ISS_ARC:
                dst.writestr(ISS_ARC, new_iss, compress_type=zipfile.ZIP_DEFLATED)
                total += 1
                continue
            if info.is_dir():
                continue
            with src.open(info) as fh:
                dst.writestr(info.filename, fh.read(), compress_type=zipfile.ZIP_DEFLATED)
            total += 1
            if total % 20000 == 0:
                print(f"  {total} …", flush=True)
        # 若原包里没有 .iss，补一份
        if ISS_ARC not in src.namelist():
            dst.writestr(ISS_ARC, new_iss, compress_type=zipfile.ZIP_DEFLATED)
            print("  原包无 .iss，已补入")

    size = TMP.stat().st_size / 1024 / 1024
    print(f"重写完成：{total} 条目，{size:.1f} MB")
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
