# -*- coding: utf-8 -*-
"""组装「从零装环境」版安装包制作工作包（对齐天智5号那份）。

结构：
    files/01_程序包/HC-Agent        程序本体（211 MB）
    files/02_环境包/python-*.exe     官方安装器（25.7 MB）
    files/02_环境包/wheels/          离线 wheel（90 MB）
    files/02_环境包/pylibs/          OpenDSS 绑定（123 MB）
    files/02_环境包/pylibs_pandapower/  pandapower 依赖（315 MB）
    scripts/install_and_deploy.ps1   静默装 Python + 装 wheel + 部署
    scripts/launch_agent.bat         启动器（含到期校验）
"""
from __future__ import annotations

import shutil
from pathlib import Path

WS = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace")
ROOT = WS / "hc_agent_project"
OUT = ROOT / "build" / "offline" / "配网承载力智能体_安装包制作"
DIST = ROOT / "dist" / "HC-Agent"
ENV = ROOT / "build" / "offline" / "files" / "02_环境包"   # 已下载内容
PYLIBS = WS / "opendss_demo" / "pylibs"
PP_LIBS = WS / "pylibs_pandapower"
EXPIRE = "2026-12-31"

INSTALL_PS1 = r"""# 静默安装 Python 并部署离线依赖
# 由 .iss 的 [Run] 调用，或手工执行
param(
  [string]$KitRoot = $PSScriptRoot + '\..',
  [string]$Target  = "$env:LOCALAPPDATA\HC-Agent-Kit"
)
$ErrorActionPreference = 'Stop'
$env1 = Join-Path $KitRoot 'files\02_环境包'

function Log($m) { Write-Host "[env] $m" }

# 1) Python
$pyDir  = Join-Path $Target 'python-3.12.10'
$pyExe  = Join-Path $pyDir 'python.exe'
if (Test-Path $pyExe) {
  Log "Python 已存在：$pyDir"
} else {
  $inst = Get-ChildItem $env1 -Filter 'python-*-amd64.exe' | Select-Object -First 1
  if (-not $inst) { throw "缺少 Python 安装器" }
  Log "安装 Python：$($inst.Name)"
  $args = @('/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_test=0',
            'Include_launcher=0', "TargetDir=$pyDir")
  Start-Process -FilePath $inst.FullName -ArgumentList $args -Wait
  if (-not (Test-Path $pyExe)) { throw "Python 安装失败：$pyExe 不存在" }
}

# 2) 离线 wheel
$wheels = Join-Path $env1 'wheels'
if (Test-Path $wheels) {
  Log "安装离线 wheel（--no-index）"
  & $pyExe -m pip install --no-index --find-links $wheels --quiet `
      numpy scipy pandas networkx pydantic python-docx openpyxl python-dotenv requests pypdf cryptography
  if ($LASTEXITCODE -ne 0) { Log "warn: 部分 wheel 安装失败（可继续，主程序自带运行时）" }
}

# 3) OpenDSS 绑定与 pandapower 依赖
foreach ($pair in @(@('pylibs', 'pylibs'), @('pylibs_pandapower', 'pylibs_pandapower'))) {
  $src = Join-Path $env1 $pair[0]
  $dst = Join-Path $Target $pair[1]
  if (Test-Path $src) {
    Log "部署 $($pair[0]) -> $dst"
    New-Item -ItemType Directory -Path $dst -Force | Out-Null
    Copy-Item (Join-Path $src '*') $dst -Recurse -Force
  }
}

# 4) 自检
$env:PYTHONPATH = (Join-Path $Target 'pylibs') + ';' + (Join-Path $Target 'pylibs_pandapower')
try {
  & $pyExe -c "import numpy, opendssdirect as d; print('OK numpy', numpy.__version__); print('OK dss', d.Basic.Version()[:60])"
  Log "环境自检通过"
} catch {
  Log "自检异常：$($_.Exception.Message)"
}
Log "完成。脚本可用：$pyExe"
"""

LAUNCH_BAT = r"""@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
for /f "tokens=1-3 delims=/ " %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy/MM/dd"') do (
  set YY=%%a& set MM=%%b& set DD=%%c
)
if %YY%%MM%%DD% GTR 20261231 (
  echo  本试用版已到期（2026-12-31），请联系供方。
  pause
  exit /b 1
)
start "" "%~dp0HC-Agent.exe"
"""

README = f"""# 配网承载力智能体 · 安装包制作（从零装环境版）

按天智5号那份的结构：主程序 + **Python 官方安装器** + **离线 wheels** + **OpenDSS 绑定**，
目标机断网也能从零装好环境。

## 参数

| 项 | 值 |
| --- | --- |
| 到期日 | **{EXPIRE}**（安装拦截 + 启动拦截） |
| 安装密码 | `TZ5-HCAgent-2026` |
| 试用期 | 程序内置 60 天 |

## 目录

```
配网承载力智能体_安装包制作/
├─ 配网承载力智能体_试用版.iss
├─ README_打包说明.md
├─ scripts/
│  ├─ install_and_deploy.ps1    静默装 Python + 离线 wheel + 部署依赖
│  ├─ launch_agent.bat          启动器（到期校验）
│  └─ build_setup.ps1           一键编译
├─ 脚本/                        示例脚本（自检 / 潮流）
└─ files/
   ├─ 01_程序包/HC-Agent/       程序本体
   └─ 02_环境包/
      ├─ python-3.12.10-amd64.exe
      ├─ wheels/                离线 wheel
      ├─ pylibs/                OpenDSS 绑定
      └─ pylibs_pandapower/     pandapower 依赖
```

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File scripts\\build_setup.ps1
```

## 目标机安装后

1. 主程序可直接用（自带运行时，60 天试用）
2. 需要跑脚本时：`scripts\\install_and_deploy.ps1` 装好 Python 与依赖
"""


def main() -> None:
    if OUT.exists():
        import os, stat
        for r, _d, fs in os.walk(OUT):
            for f in fs:
                try:
                    os.chmod(os.path.join(r, f), stat.S_IWRITE)
                except Exception:
                    pass
        shutil.rmtree(OUT, ignore_errors=True)

    (OUT / "files" / "01_程序包").mkdir(parents=True)
    (OUT / "files" / "02_环境包").mkdir(parents=True)
    (OUT / "scripts").mkdir(parents=True)
    (OUT / "脚本").mkdir(parents=True)

    (OUT / "README_打包说明.md").write_text(README, encoding="utf-8")
    (OUT / "scripts" / "install_and_deploy.ps1").write_text(
        INSTALL_PS1.replace("\n", "\r\n"), encoding="utf-8-sig")
    (OUT / "scripts" / "launch_agent.bat").write_text(
        LAUNCH_BAT.replace("\n", "\r\n"), encoding="gbk")

    print("  复制程序包 …")
    shutil.copytree(DIST, OUT / "files" / "01_程序包" / "HC-Agent")

    dst_env = OUT / "files" / "02_环境包"
    print("  复制环境包（Python/wheels/pylibs/pandapower）…")
    for name in ("python-3.12.10-amd64.exe", "wheels"):
        src = ENV / name
        if src.is_dir():
            shutil.copytree(src, dst_env / name)
        elif src.is_file():
            shutil.copy2(src, dst_env / name)
    # pandapower 依赖目录存在超长路径，shutil 会 WinError 3；改用 robocopy 并跳过 __pycache__
    import subprocess
    for src, name in ((PYLIBS, "pylibs"), (PP_LIBS, "pylibs_pandapower")):
        if src.is_dir():
            dst = dst_env / name
            dst.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["robocopy", str(src), str(dst), "/E", "/XD", "__pycache__",
                                "/NFL", "/NDL", "/NJH", "/NJS", "/NP"],
                               capture_output=True, text=True)
            if r.returncode >= 8:
                raise SystemExit(f"robocopy 失败 {name}: {r.stdout[-300:]}")
            print(f"  {name} 复制完成（robocopy rc={r.returncode}）")

    # 复用已有的 .iss 与示例脚本
    for rel in ("配网承载力智能体_试用版.iss", "scripts/build_setup.ps1"):
        s = ROOT / "packaging" / rel
        if s.is_file():
            shutil.copy2(s, OUT / rel)
    kit_scripts = ROOT / "packaging" / "kit_scripts"
    if (kit_scripts / "脚本").is_dir():
        for f in (kit_scripts / "脚本").iterdir():
            shutil.copy2(f, OUT / "脚本" / f.name)

    files = sum(1 for _ in OUT.rglob("*") if _.is_file())
    size = sum(_.stat().st_size for _ in OUT.rglob("*") if _.is_file()) / 1024 / 1024
    print(f"组装完成：{OUT}")
    print(f"  {files} 个文件，{size:.0f} MB")


if __name__ == "__main__":
    main()
