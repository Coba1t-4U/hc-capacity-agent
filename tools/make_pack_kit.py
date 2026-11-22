# -*- coding: utf-8 -*-
"""生成「配网承载力智能体安装包制作」工作包（对齐天智5号那份的格式）。

产出目录：build/pack_kit/
    配网承载力智能体_安装包制作/
      ├─ README_打包说明.md
      ├─ 配网承载力智能体_试用版.iss
      ├─ scripts/
      │    ├─ build_setup.ps1
      │    └─ launch_agent.bat
      └─ files/
           ├─ README_放置文件说明.md
           └─ HC-Agent/            ← 从 dist 复制
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
KIT = ROOT / "build" / "pack_kit" / "配网承载力智能体_安装包制作"
DIST = ROOT / "dist" / "HC-Agent"
EXPIRE = "2026-12-31"
PASSWORD = "TZ5-HCAgent-2026"

README = f"""# 配网承载力智能体（天智五号）—— Inno 试用版安装包制作

把已构建好的程序目录（`files/HC-Agent/`）打包成**带加密 + 带固定到期日 + 完整自包含**的
Inno Setup 安装包，供其他单位试用。

## 已确定的参数

| 项 | 值 |
| --- | --- |
| 固定到期日 | **{EXPIRE}**（安装时拦截 + 每次启动拦截） |
| 安装密码 | **`{PASSWORD}`**（转交试用方时告知） |
| 打包范围 | `files/HC-Agent/`（PyInstaller 产物，自带 Python 运行时与 OpenDSS 库，约 211 MB） |
| 试用期 | 安装包内程序自带 **60 天试用**（无 lic.json 也能用，到期锁容量搜索） |

## 两项保护怎么实现

| 需求 | 实现位置 |
| --- | --- |
| 安装文件加密 | `.iss` 的 `[Setup]`：`Password` + `Encryption=yes`，装前须输密码，安装包内容加密 |
| 固定到期日 | `.iss` 的 `[Code]` `InitializeSetup`（安装时拦截）+ `scripts\\launch_agent.bat`（每次启动拦截） |
| 完整自包含 | `files/HC-Agent/` 内含 Python 运行时、OpenDSS、pandapower，目标机无需另装环境 |

## 目录结构

```
配网承载力智能体_安装包制作/
├─ 配网承载力智能体_试用版.iss   主安装脚本
├─ README_打包说明.md
├─ scripts/
│  ├─ build_setup.ps1            一键编译（调用 ISCC）
│  └─ launch_agent.bat           启动器（含到期校验）
└─ files/
   ├─ README_放置文件说明.md
   └─ HC-Agent/                  程序目录（exe + _internal + 文档 + 示例算例）
```

## 怎么打

1. 安装 Inno Setup 6（本机已装：`%LOCALAPPDATA%\\Programs\\Inno Setup 6`）
2. 在本目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\\build_setup.ps1
```

3. 产物：`release\\配网承载力智能体-1.1.0-试用版.exe`

## 交付给试用方时

1. 给出安装包与**安装密码**
2. 对方安装后直接可用 **60 天**（无需授权文件）
3. 60 天后需要 `lic.json`：对方在「配置 → 授权」页复制机器码发回，供方签发后导入
4. 安装包本身的到期日 {EXPIRE} 是**硬上限**，与 60 天试用叠加生效（先到者为准）

## 运行脚本用的环境（可选）

包内主程序自带运行时，**跑主程序不需要额外操作**。若还要用 脚本\\ 里的 Python 示例，
执行一次：

`powershell
powershell -ExecutionPolicy Bypass -File scripts\\setup_scripts_env.ps1
`

它会：

1. download_python.ps1 —— 从 python.org 下载便携版 3.12.10，解压到 %LOCALAPPDATA%\\HC-Agent-Kit，
   并把 ..\\pylibs 与 import site 写进 python312._pth（embeddable Python 忽略 PYTHONPATH，必须写 _pth）
2. download_opendss.ps1 —— 优先用 iles\\pylibs（若放了离线副本），否则从 PyPI 安装 opendssdirect
3. 跑一次 脚本\\01_环境自检.py 验证

之后双击 运行脚本示例.bat 即可跑示例潮流。

## 注意

- 换程序版本时，只需替换 `files/HC-Agent/` 整个目录，`.iss` 里的 `#define AppVersion` 同步改
- `lic.json` 不要放进 `files/`，那是按机器签发的，放进去会对所有试用方失效
"""

ISS = f"""; 配网承载力智能体（天智五号）试用版安装脚本
; 编译：scripts\\build_setup.ps1（内部调用 ISCC.exe）

#define AppName "配网承载力智能体·天智五号"
#define AppShortName "HC-Agent"
#define AppVersion "1.1.0"
#define AppPublisher "配网承载力智能体项目组"
#define SourceDir "files\\HC-Agent"
#define ExpireDate "{EXPIRE}"

[Setup]
AppId={{{{9B2E5C71-3D4A-4F82-A6E1-7C5D3B8F1E24}}}}
AppName={{#AppName}}
AppVersion={{#AppVersion}}
AppVerName={{#AppName}} {{#AppVersion}} 试用版
AppPublisher={{#AppPublisher}}
DefaultDirName={{localappdata}}\\Programs\\{{#AppShortName}}
DefaultGroupName={{#AppName}}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\\release
OutputBaseFilename=配网承载力智能体-{{#AppVersion}}-试用版
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={{#AppName}}
UninstallDisplayIcon={{app}}\\{{#AppShortName}}.exe
Password={PASSWORD}
Encryption=yes
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "{{#SourceDir}}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "scripts\\launch_agent.bat"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\{{#AppName}}"; Filename: "{{app}}\\launch_agent.bat"; WorkingDir: "{{app}}"
Name: "{{group}}\\卸载 {{#AppName}}"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\{{#AppName}}"; Filename: "{{app}}\\launch_agent.bat"; WorkingDir: "{{app}}"; Tasks: desktopicon

[Run]
Filename: "{{app}}\\launch_agent.bat"; Description: "立即启动"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
Type: filesandordirs; Name: "{{app}}\\runs"
Type: filesandordirs; Name: "{{app}}\\logs"

[Code]
function InitializeSetup(): Boolean;
var
  today: String;
begin
  Result := True;
  today := GetDateTimeString('yyyy/mm/dd', '-', ':');
  // 与 ExpireDate 对齐：2026-12-31
  if today > '2026/12/31' then
  begin
    MsgBox('本试用版安装包已于 2026-12-31 到期，无法继续安装。' + #13#10 +
           '如需继续试用，请联系供方获取新的安装包。', mbCriticalError, MB_OK);
    Result := False;
  end;
end;
end;
"""

LAUNCH = """@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 到期校验：固定上限 {EXPIRE}（与安装包一致，防止绕过安装环节）
for /f "tokens=1-3 delims=/ " %%a in ('powershell -NoProfile -Command "Get-Date -Format yyyy/MM/dd"') do (
  set YY=%%a& set MM=%%b& set DD=%%c
)
set TODAY=%YY%%MM%%DD%
if %TODAY% GTR 20261231 (
  echo.
  echo  本试用版已到期（{EXPIRE}），无法启动。
  echo  如需继续试用，请联系供方获取新版本。
  echo.
  pause
  exit /b 1
)

start "" "%~dp0HC-Agent.exe"
endlocal
"""
LAUNCH = LAUNCH.replace("{EXPIRE}", EXPIRE)
# 生成时去掉重复的 end;（编辑历史遗留）
ISS = ISS.replace("end;\nend;\n", "end;\n")

BUILD_PS1 = """# 一键编译配网承载力智能体试用版安装包
$ErrorActionPreference = 'Stop'
$kit = Split-Path -Parent $PSScriptRoot
$iscc = @(
  "$env:LOCALAPPDATA\\Programs\\Inno Setup 6\\ISCC.exe",
  "${env:ProgramFiles(x86)}\\Inno Setup 6\\ISCC.exe",
  "$env:ProgramFiles\\Inno Setup 6\\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
  Write-Host "未找到 ISCC.exe，请先安装 Inno Setup 6：https://jrsoftware.org/isdl.php" -ForegroundColor Red
  exit 1
}

$src = Join-Path $kit 'files\\HC-Agent'
if (-not (Test-Path $src)) {
  Write-Host "缺少程序目录：$src" -ForegroundColor Red
  exit 1
}
$n = (Get-ChildItem $src -Recurse -File | Measure-Object).Count
Write-Host "程序目录：$n 个文件"

Write-Host "开始编译…"
& $iscc (Join-Path $kit '配网承载力智能体_试用版.iss')
if ($LASTEXITCODE -ne 0) { Write-Host "编译失败" -ForegroundColor Red; exit 1 }

$out = Get-ChildItem (Join-Path $kit '..\\release') -Filter '*试用版.exe' | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host ("完成：" + $out.FullName) -ForegroundColor Green
Write-Host ("大小：" + [math]::Round($out.Length/1MB,1) + " MB")
Write-Host ("SHA256：" + (Get-FileHash $out.FullName -Algorithm SHA256).Hash)
"""

FILES_README = """# files 目录说明

- `HC-Agent/`：程序目录，来自项目构建产物 `dist\\HC-Agent/`，打包前请整体替换为最新构建。
- 不要把 `lic.json` 放进本目录——授权按机器签发，放进去会对所有试用方失效。
- 目录内应能看到：`HC-Agent.exe`、`_internal\\`、`文档\\`、`示例算例\\`、`启动 HC-Agent.bat`。
"""


def main() -> None:
    if KIT.exists():
        import os, stat
        for root, _d, files in os.walk(KIT):
            for fn in files:
                try:
                    os.chmod(os.path.join(root, fn), stat.S_IWRITE)
                except Exception:
                    pass
        shutil.rmtree(KIT, ignore_errors=True)
    (KIT / "scripts").mkdir(parents=True)
    (KIT / "files").mkdir(parents=True)

    (KIT / "README_打包说明.md").write_text(README, encoding="utf-8")
    (KIT / "配网承载力智能体_试用版.iss").write_text(ISS, encoding="utf-8")
    (KIT / "scripts" / "launch_agent.bat").write_text(
        LAUNCH.replace("\n", "\r\n"), encoding="utf-8")
    (KIT / "scripts" / "build_setup.ps1").write_text(BUILD_PS1, encoding="utf-8")
    (KIT / "files" / "README_放置文件说明.md").write_text(FILES_README, encoding="utf-8")

    # 环境脚本（下载 Python / 下载 OpenDSS / 一键配置）+ 示例脚本
    kit_scripts = ROOT / "packaging" / "kit_scripts"
    if kit_scripts.is_dir():
        for item in kit_scripts.rglob("*"):
            if item.is_file():
                rel = item.relative_to(kit_scripts)
                tgt = KIT / rel
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, tgt)
        print(f"  已并入环境脚本 {sum(1 for _ in kit_scripts.rglob(chr(42)) if _.is_file())} 个")

    dst = KIT / "files" / "HC-Agent"
    shutil.copytree(DIST, dst)
    n = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"工作包已生成：{KIT}")
    print(f"程序目录文件数：{n}")


if __name__ == "__main__":
    main()
