# -*- coding: utf-8 -*-
"""给「安装包制作」工作包补三样环境脚本：下载 Python、下载 OpenDSS、一键配好环境。

产物写入 packaging/kit_scripts/（进仓库），make_pack_kit 会把它拷进工作包。
"""
from pathlib import Path

ROOT = Path(r"C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project")
OUT = ROOT / "packaging" / "kit_scripts"

DL_PY = r"""# 下载并部署便携版 Python（供运行脚本用，不影响主程序）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\download_python.ps1
param(
  [string]$Version = '3.12.10',
  [string]$Target  = "$env:LOCALAPPDATA\HC-Agent-Kit"
)
$ErrorActionPreference = 'Stop'
$pyDir = Join-Path $Target "python-$Version"
if (Test-Path (Join-Path $pyDir 'python.exe')) {
  Write-Host "已存在：$pyDir（跳过下载）" -ForegroundColor Yellow
} else {
  $url = "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip"
  $zip = Join-Path $env:TEMP "python-$Version-embed-amd64.zip"
  Write-Host "下载 $url"
  Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
  New-Item -ItemType Directory -Path $pyDir -Force | Out-Null
  Expand-Archive -Path $zip -DestinationPath $pyDir -Force
  Remove-Item $zip -Force
  Write-Host "已解压：$pyDir" -ForegroundColor Green
}

# embeddable Python 用 ._pth 控制 sys.path，且忽略 PYTHONPATH：
# 把 pylibs 写进去，脚本才能 import opendssdirect
$pth = Join-Path $pyDir 'python312._pth'
$lines = @('python312.zip', '.', '..\pylibs', '', 'import site')
Set-Content -Path $pth -Value $lines -Encoding ASCII
Write-Host "已写入 $pth"

& (Join-Path $pyDir 'python.exe') -c "import sys; print('Python', sys.version.split()[0], 'OK')"
"""

DL_DSS = r"""# 部署 OpenDSS 的 Python 绑定（优先本地 files\pylibs，其次 pip 下载）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\download_opendss.ps1
param(
  [string]$Target = "$env:LOCALAPPDATA\HC-Agent-Kit"
)
$ErrorActionPreference = 'Stop'
$kitRoot = Split-Path -Parent $PSScriptRoot
$pylibs  = Join-Path $Target 'pylibs'
$local   = Join-Path $kitRoot 'files\pylibs'

New-Item -ItemType Directory -Path $pylibs -Force | Out-Null

if (Test-Path (Join-Path $local 'opendssdirect')) {
  Write-Host "使用本地副本：$local"
  Copy-Item (Join-Path $local '*') $pylibs -Recurse -Force
} else {
  $py = Join-Path $Target 'python-3.12.10\python.exe'
  if (-not (Test-Path $py)) { Write-Host "先运行 download_python.ps1" -ForegroundColor Red; exit 1 }
  Write-Host "本地无副本，改为从 PyPI 下载 opendssdirect（含 dss-capi）"
  & $py -m pip install --no-warn-script-location --target $pylibs opendssdirect
}

$py = Join-Path $Target 'python-3.12.10\python.exe'
& $py -c "import opendssdirect as dss; print('opendssdirect OK, DSS:', dss.Basic.Version()[:40])"
"""

SETUP = r"""# 一键配置脚本运行环境：Python + OpenDSS + 自检
# 用法：powershell -ExecutionPolicy Bypass -File scripts\setup_scripts_env.ps1
$ErrorActionPreference = 'Stop'
$kitRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== 1/3 部署 Python ===" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'download_python.ps1')

Write-Host "`n=== 2/3 部署 OpenDSS 绑定 ===" -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'download_opendss.ps1')

Write-Host "`n=== 3/3 运行自检脚本 ===" -ForegroundColor Cyan
$py = Join-Path $env:LOCALAPPDATA 'HC-Agent-Kit\python-3.12.10\python.exe'
$demo = Join-Path $kitRoot '脚本\01_环境自检.py'
if (Test-Path $demo) { & $py $demo } else { Write-Host "未找到 脚本\01_环境自检.py（可跳过）" -ForegroundColor Yellow }

Write-Host "`n完成。以后运行脚本：" -ForegroundColor Green
Write-Host "  $py 脚本\02_示例潮流.py"
"""

RUN_DEMO_BAT = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\HC-Agent-Kit\python-3.12.10\python.exe
set PYLIB=%LOCALAPPDATA%\HC-Agent-Kit\pylibs
if not exist "%PY%" (
  echo 未找到 Python，请先运行：
  echo    powershell -ExecutionPolicy Bypass -File scripts\setup_scripts_env.ps1
  pause
  exit /b 1
)
set PYTHONPATH=%PYLIB%
"%PY%" "脚本\01_环境自检.py"
echo.
"%PY%" "脚本\02_示例潮流.py"
echo.
pause
"""

SCRIPT1 = '''# -*- coding: utf-8 -*-
"""环境自检：确认 Python 与 OpenDSS 绑定可用。"""
import platform
import sys


def main() -> None:
    print("Python:", sys.version.split()[0], f"({platform.machine()})")
    print("解释器:", sys.executable)
    try:
        import numpy
        print("numpy:", numpy.__version__)
    except Exception as exc:
        print("numpy: 不可用 ->", type(exc).__name__)
    try:
        import opendssdirect as dss
        print("opendssdirect: 已加载")
        print("DSS 引擎:", dss.Basic.Version()[:80])
    except Exception as exc:
        print("opendssdirect: 不可用 ->", type(exc).__name__)
        print("请先运行 scripts\\\\setup_scripts_env.ps1")
        return
    print("\\n结论：环境可用。")


if __name__ == "__main__":
    main()
'''

SCRIPT2 = '''# -*- coding: utf-8 -*-
"""三相潮流示例：两节点算例，验证 OpenDSS 可求解。"""
import opendssdirect as dss

DECK = """
Clear
New Circuit.Demo basekv=10 pu=1.0 phases=3
New Linecode.LC nphases=3 basefreq=50 r1=0.17 x1=0.38 normamps=300
New Line.L1 phases=3 bus1=sourcebus bus2=B1 length=2 units=km linecode=LC
New Load.LD phases=3 bus1=B1 kW=500 kvar=160 model=1
Set VoltageBases=[10]
CalcVoltageBases
Set ControlMode=Static
Set Mode=Snap
Solve
"""


def main() -> None:
    for line in DECK.strip().splitlines():
        cmd = line.strip()
        if cmd:
            dss.Text.Command(cmd)
    print("收敛:", dss.Solution.Converged(), "| 迭代:", dss.Solution.Iterations())
    for name in dss.Circuit.AllBusNames():
        dss.Circuit.SetActiveBus(name)
        pu = dss.Bus.puVmagAngle()[0::2]
        if pu:
            print(f"  母线 {name:<10} {dss.Bus.kVBase():>6.2f} kV  {min(pu):.4f} pu")


if __name__ == "__main__":
    main()
'''


def main() -> None:
    (OUT / "scripts").mkdir(parents=True, exist_ok=True)
    (OUT / "脚本").mkdir(parents=True, exist_ok=True)
    for name, text in (("download_python.ps1", DL_PY),
                       ("download_opendss.ps1", DL_DSS),
                       ("setup_scripts_env.ps1", SETUP)):
        (OUT / "scripts" / name).write_text(text.replace("\n", "\r\n"), encoding="utf-8-sig")
    (OUT / "运行脚本示例.bat").write_text(RUN_DEMO_BAT.replace("\n", "\r\n"), encoding="gbk")
    (OUT / "脚本" / "01_环境自检.py").write_text(SCRIPT1, encoding="utf-8")
    (OUT / "脚本" / "02_示例潮流.py").write_text(SCRIPT2, encoding="utf-8")
    n = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"已生成环境脚本模板：{OUT}（{n} 个文件）")


if __name__ == "__main__":
    main()
