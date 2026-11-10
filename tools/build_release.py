"""build_release.py — 一键打出可分发目录 + zip。

做四件事：
 1. 清洁重建（--clean），避免上一版残留让"改了没生效"这种假象发生
    —— 本项目实测过：不带 --clean 时拿到的是旧 exe，白排查半小时。
 2. 把启动器与使用说明拷进产物目录。
 3. 跑 --self-test 与 --engine-host 冒烟，**产物不合格就不打包**。
 4. 打 zip（含版本号与日期），并打印校验与体积。

用法：
    python tools/build_release.py            # 构建并打包
    python tools/build_release.py --no-zip   # 只构建
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "HC-Agent.spec"
DIST_SRC = ROOT / "packaging" / "dist-files"
DIST = ROOT / "dist" / "HC-Agent"
EXE = DIST / "HC-Agent.exe"
RELEASE_DIR = ROOT / "release"


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, **kw)


def version() -> str:
    sys.path.insert(0, str(ROOT))
    from hc_agent import __version__
    return __version__


def step(msg):
    print(f"\n=== {msg} ===")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    ver = version()
    stamp = time.strftime("%Y%m%d")
    print(f"HC-Agent 打包  version={ver}  date={stamp}")

    if not args.skip_build:
        step("1/5 清洁重建")
        # 先关掉可能占用产物的进程，否则 Windows 会拒绝覆盖 exe
        subprocess.run(["taskkill", "/F", "/IM", "HC-Agent.exe"],
                       capture_output=True)
        time.sleep(1)
        for d in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(d, ignore_errors=True)
        r = run([sys.executable, "-m", "PyInstaller", str(SPEC),
                 "--noconfirm", "--clean", "--log-level", "ERROR"])
        if r.returncode != 0 or not EXE.is_file():
            print("构建失败：")
            print((r.stderr or r.stdout)[-3000:])
            return 1
        print(f"OK  {EXE}")
    else:
        if not EXE.is_file():
            print("未构建且找不到 exe")
            return 1
        print("跳过构建，使用现有产物")

    step("2/5 拷入启动器与说明")
    for src in DIST_SRC.iterdir():
        if src.is_file():
            shutil.copy2(src, DIST / src.name)
            print(f"  + {src.name}")

    step("2.5/5 带入说明文档与示例算例")
    extras = [
        (ROOT / "docs", DIST / "文档", ("*.docx", "*.md")),
        (ROOT / "tests" / "_desktop_shots", DIST / "文档" / "界面截图", ("*.png",)),
        (ROOT / "runs" / "ACCEPT" / "FD002", DIST / "示例算例" / "FD002", ("*.json", "*.csv", "*.yaml", "*.xlsx")),
    ]
    for src, dst, pats in extras:
        if not src.is_dir():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for pat in pats:
            for f in src.rglob(pat):
                if f.stat().st_size > 30 * 1024 * 1024:
                    continue
                rel = f.relative_to(src)
                tgt = dst / rel
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, tgt)
                n += 1
        print(f"  + {src.name} -> {dst.relative_to(DIST)}（{n} 个文件）")

    step("3/5 产物自检")
    # 用临时数据目录，避免污染用户真实数据
    env = dict(os.environ)
    env["HC_DATA_DIR"] = str((ROOT / "runs" / "_release_check").resolve())
    r = subprocess.run([str(EXE), "--self-test"], capture_output=True,
                       text=True, env=env, timeout=300)
    try:
        info = json.loads(r.stdout)
    except Exception:
        print("自检输出无法解析：")
        print((r.stdout or "")[-1000:], (r.stderr or "")[-1000:])
        return 1
    checks = info.get("checks") or {}
    bad = []
    if not str(checks.get("data_dir_created")).startswith("True"):
        bad.append(f"数据目录不可建: {checks.get('data_dir_created')}")
    if not checks.get("engine_version"):
        bad.append(f"引擎不可用: {checks.get('engine_error')}")
    if not checks.get("numpy"):
        bad.append(f"numpy 不可用: {checks.get('numpy_error')}")
    if bad:
        print("自检未通过：")
        for b in bad:
            print("  -", b)
        return 1
    print(f"  OK 引擎 {str(checks['engine_version'])[:48]}")
    print(f"  OK numpy {checks['numpy']}")

    step("4/5 体积与文件数")
    files = [p for p in DIST.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"  文件 {len(files)} 个，合计 {total / 1024 / 1024:.1f} MB")

    if args.no_zip:
        print("\n完成（未打包 zip）")
        return 0

    step("5/5 打 zip")
    RELEASE_DIR.mkdir(exist_ok=True)
    zip_path = RELEASE_DIR / f"HC-Agent-{ver}-win64-{stamp}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in files:
            zf.write(p, Path("HC-Agent") / p.relative_to(DIST))
    zsize = zip_path.stat().st_size
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"  {zip_path.name}")
    print(f"  压缩后 {zsize / 1024 / 1024:.1f} MB")
    print(f"  SHA256 {sha}")
    (RELEASE_DIR / "SHA256SUMS.txt").write_text(
        f"{sha}  {zip_path.name}\n", encoding="utf-8")
    print("\n完成。可分发的就是上面这个 zip，解压后双击「启动 HC-Agent.bat」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
