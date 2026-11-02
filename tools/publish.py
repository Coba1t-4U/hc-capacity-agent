# -*- coding: utf-8 -*-
"""publish.py — 把母本工程的代码同步到 GitHub 克隆（同时也是 PyCharm 工程）。

## 为什么需要它

本机有两份代码：

    A. 母本（source of truth）
       Documents\\deepseek-harness\\default-workspace\\hc_agent_project
       有 runs / dss_runs / dist 等大产物，**没有 git**

    B. PyCharm 工程 + GitHub 克隆
       Desktop\\配网承载力智能体Agent
       只有该进仓库的代码，有 .git，remote 指向 GitHub

改代码发生在 A，而"进 PyCharm、上 GitHub"发生在 B。这个脚本负责把 A 的**代码**
（不是产物）同步到 B，再提交推送 —— 避免每次手工复制漏文件。

## 用法

    python tools/publish.py                    # 同步 + 提交 + 推送（自动写提交信息）
    python tools/publish.py -m "修复 xxx"       # 指定提交信息
    python tools/publish.py --dry-run          # 只显示会同步/提交什么，不落盘
    python tools/publish.py --no-push          # 只提交不推送

退出码：0 成功（含"无变化"）、1 失败。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]          # 母本工程根
REMOTE = "git@github.com:Coba1t-4U/hc-capacity-agent.git"

#: 克隆可能待的位置。用户会自己整理桌面（这是他的地盘），所以这里**不写死**：
#: 按顺序找，第一个是合法 git 仓库的即采用。新增位置时只改这张表。
CLONE_CANDIDATES = [
    Path.home() / "Desktop" / "配网承载力" / "配网承载力智能体Agent",
    Path.home() / "Desktop" / "配网承载力智能体Agent",
]


def _detect_clone() -> Path:
    """找到 GitHub 克隆（= PyCharm 工程）所在目录。

    优先返回**已是合法 git 仓库**的候选；都不合法时返回第一个候选路径，
    交给后面的\"自动重新克隆\"逻辑落地。
    """
    for p in CLONE_CANDIDATES:
        if (p / ".git" / "objects").is_dir():
            return p
    for p in CLONE_CANDIDATES:
        if p.is_dir():
            return p
    return CLONE_CANDIDATES[0]


DST = _detect_clone()

#: 需要同步的目录（与仓库实际跟踪范围一致）
DIRS = ["hc_agent", "skills", "rules", "tests", "tools", "docs",
        "knowledge", "packaging", "web", "integrations"]

#: 需要同步的单文件
FILES = ["pyproject.toml", "README.md", "AGENTS.md", "requirements.lock",
         "requirements.txt", "NEXT_SESSION.md", ".gitignore",
         "启动桌面版.py", "一键自检.py", "local_pylibs.json",
         # PyCharm 工程脚手架：也必须由母本统一下发，否则克隆重建时会丢
         ".pycharm_sdk.json", "README_如何在PyCharm中打开.md",
         "注册PyCharm解释器.cmd", "配网承载力智能体Agent.iml"]

#: 需要整体同步的子目录（含 .idea 下的工程配置）
DIRS = DIRS + [".idea"]

#: hc_agent_ui 只同步源码与启动器（node_modules / profile 是本地运行时）
UI_ITEMS = ["assets", "package.json", "start-native.bat", "start-desktop.bat",
            "main.js", "preload.js", "launch.js"]

#: 永不同步（产物、缓存、密钥、用户数据）
SKIP_NAMES = {"__pycache__", ".venv", "node_modules", "profile", "dist", "_desktop_shots",
              "build", "dss_runs", "release", "logs", ".git", ".idea"}
SKIP_FILES = {"llm.json", "chat.sqlite", "chat.sqlite-shm", "chat.sqlite-wal",
              "dss_cases.json", "engine_mode.json"}


def find_git() -> str:
    """优先用便携版 MinGit，其次 PATH 里的 git。"""
    cand = Path.home() / "AppData/Local/Programs/MinGit/cmd/git.exe"
    if cand.is_file():
        return str(cand)
    found = shutil.which("git")
    if not found:
        raise SystemExit("找不到 git：请安装 Git 或确认 MinGit 路径")
    return found


def iter_source_files(root: Path):
    """遍历母本里**该进仓库**的文件，返回相对路径。"""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = set(rel.parts)
        if parts & SKIP_NAMES:
            continue
        if rel.name in SKIP_FILES or rel.suffix in (".pyc", ".log"):
            continue
        yield rel


def _same_content(a: bytes, b: bytes) -> bool:
    """内容是否等价。

    **换行差异不算变化**：克隆是按 core.autocrlf=true 检出的（工作区 CRLF），
    而母本是 LF。直接按字节比较会得出"两百多个文件都变了"的假象，
    同步时把整棵树重写一遍，git diff 也会变成噪声。
    """
    if a == b:
        return True
    crlf, lf = b"\r\n", b"\n"
    return a.replace(crlf, lf) == b.replace(crlf, lf)


def sync_subset(rel: Path, dry: bool) -> bool:
    """把单个文件从母本复制到克隆。返回是否发生内容变化。"""
    src = SRC / rel
    dst = DST / rel
    if not src.is_file():
        return False
    try:
        if dst.is_file() and _same_content(dst.read_bytes(), src.read_bytes()):
            return False
    except Exception:
        pass
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return True


def in_scope(rel: Path) -> bool:
    """只同步 DIRS / FILES / UI_ITEMS 之内的路径（其余是大产物，跳过）。"""
    top = rel.parts[0]
    if top in DIRS:
        return True
    if top == "hc_agent_ui":
        rest = rel.parts[1:]
        return bool(rest) and rest[0] in UI_ITEMS
    if top == "runs":
        # 黄金运行要带（测试依赖），其余 runs 产物不带
        return len(rel.parts) > 1 and rel.parts[1] == "ACCEPT"
    return str(rel).replace("\\", "/") in FILES


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同步母本代码到 GitHub 克隆并推送")
    ap.add_argument("-m", "--message", default="", help="提交信息")
    ap.add_argument("--dry-run", action="store_true", help="只演示，不落盘")
    ap.add_argument("--no-push", action="store_true", help="只提交不推送")
    args = ap.parse_args(argv)

    git = find_git()

    # 克隆目录被删除时自动重建。
    # 本机已两次出现「桌面两个目录被整体删除、回收站也清空」（2026-10-30 23:41、
    # 2026-10-31 20:5x）。母本在 Documents 下不受影响，所以只要能从 GitHub
    # 重克隆，发布链路就不该因为目录消失而中断。
    if not DST.is_dir():
        print(f"克隆不存在，自动重建：{DST}")
        if args.dry_run:
            print("（--dry-run：不执行克隆）")
            return 0
        DST.parent.mkdir(parents=True, exist_ok=True)
        code, out = run([git, "clone", "--depth", "1", REMOTE, str(DST)], DST.parent)
        if code != 0:
            print("[失败] 重新克隆失败：\n" + out[-800:])
            print("       检查：SSH 通道（~/.ssh/config → ssh.github.com:443）、权限")
            return 1
        print("  已重新克隆：" + str(DST))

    # ---- 1) 同步
    changed = []
    for rel in iter_source_files(SRC):
        if not in_scope(rel):
            continue
        if sync_subset(rel, args.dry_run):
            changed.append(rel)
    print(f"同步：{len(changed)} 个文件有变化")
    for rel in changed[:20]:
        print(f"  · {rel}")
    if len(changed) > 20:
        print(f"  … 另有 {len(changed) - 20} 个")

    # ---- 1.5) 漂移检测：克隆里被 git 跟踪、但母本里没有的文件。
    # 这些通常是「直接在 PyCharm 里新建/修改」的文件：下次同步不会带上它们，
    # 克隆一旦重建（本机已发生两次）就会丢。必须回写母本。
    code, out = run([git, "-c", "core.quotepath=false", "ls-files"], DST)
    tracked = [ln.strip() for ln in out.splitlines() if ln.strip()]
    drift = [f for f in tracked if not (SRC / f).is_file()]
    if drift:
        print(f"漂移警告：{len(drift)} 个受控文件只存在于克隆（母本缺失）")
        for f in drift[:10]:
            print(f"  ! {f}")
        print("  处理：把它们回写到母本，否则下次同步/重建会丢。")

    # ---- 2) 提交
    code, out = run([git, "status", "--porcelain"], DST)
    pending = [ln for ln in out.splitlines() if ln.strip()]
    print(f"待提交：{len(pending)} 项")
    if not pending:
        print("无变化，无需提交。")
        return 0
    if args.dry_run:
        for ln in pending[:20]:
            print(f"  {ln}")
        print("（--dry-run：未提交、未推送）")
        return 0

    run([git, "add", "-A"], DST)
    msg = args.message.strip() or "同步母本代码"
    code, out = run([git, "-c", "core.safecrlf=false", "commit", "-q", "-m", msg], DST)
    if code != 0:
        print("[失败] 提交失败：\n" + out[-800:])
        return 1
    code, out = run([git, "log", "--oneline", "-1"], DST)
    print("已提交：" + out.strip())

    # ---- 3) 推送
    if args.no_push:
        print("（--no-push：未推送）")
        return 0
    code, out = run([git, "push"], DST)
    if code != 0:
        print("[失败] 推送失败：\n" + out[-800:])
        return 1
    print("已推送：" + out.strip().splitlines()[-1] if out.strip() else "已推送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
