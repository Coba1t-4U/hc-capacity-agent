# =============================================================================
#  配网承载力与主配微协同优化 Agent — 运行监控台（刷新并查看）
#
#  作用：先把 runs/ 下的最新运行产物重新导出为 web/data.json，再启动本地
#        静态服务器并打开页面，因此看到的始终是最新数据。
#
#  为什么需要本地服务器：页面用 fetch() 读取同目录的 data.json，浏览器的
#        file:// 同源策略会拒绝该请求，必须走 http。
#        只想快速看一眼、不需要最新数据时，请用桌面上另一个快捷方式
#        「配网承载力监控台（离线查看）」——那是数据已内联的单文件版。
#
#  停止：关闭本窗口，或按 Ctrl+C。
#
#  说明：用 PowerShell 而非 .bat，是因为 cmd.exe 按 ANSI/OEM 代码页解析批处理
#        文件，UTF-8 的中文会变成乱码并被当成命令执行。PowerShell 原生按
#        UTF-8 读取，中文注释与提示都可靠。
# =============================================================================

$ErrorActionPreference = 'Stop'

$Proj = 'C:\Users\Chen\Documents\deepseek-harness\default-workspace\hc_agent_project'
$Port = 8778

# ---- 定位 Python（优先 DSH 自带运行时，其次 PATH） ----
$Py = 'C:\Users\Chen\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}

Write-Host ''
Write-Host '  ============================================================' -ForegroundColor Cyan
Write-Host '   配网承载力与主配微协同优化 Agent  -  运行监控台' -ForegroundColor Cyan
Write-Host '  ============================================================' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path (Join-Path $Proj 'web\index.html'))) {
    Write-Host "  [错误] 找不到工程目录：" -ForegroundColor Red
    Write-Host "  $Proj"
    Read-Host '  按回车退出'
    exit 1
}

# ---- 重新导出最新运行产物 ----
# 必须用**绝对路径**：快捷方式的工作目录是 web\，若用相对路径 'runs\ACCEPT'，
# 会被解析成 web\runs\ACCEPT（不存在），导出器会静默写出 0 个算例的空数据包
# 且退出码仍为 0 —— 页面打开后一片空白却没有任何报错。此处用绝对路径并显式
# 校验算例数，把这类静默失败变成可见的警告。
$DataOut = Join-Path $Proj 'web\data.json'
$RunsIn  = Join-Path $Proj 'runs\ACCEPT'
$ExportOk = $false
if (Test-Path $Py) {
    Write-Host '  正在从 runs\ 导出最新运行产物…' -ForegroundColor Gray
    try {
        & $Py (Join-Path $Proj 'tools\export_dashboard.py') `
              --root $RunsIn --out $DataOut | Out-Null
        if ($LASTEXITCODE -eq 0 -and (Test-Path $DataOut)) {
            # 校验确实导出了算例，避免"空数据包 + 退出码 0"的静默失败
            try {
                $pkg = Get-Content $DataOut -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($pkg.case_count -gt 0) {
                    $ExportOk = $true
                    Write-Host ("  数据导出完成：{0} 个算例（{1}）" -f `
                                $pkg.case_count, ($pkg.cases.id -join ', ')) `
                                -ForegroundColor Green
                } else {
                    Write-Host '  [警告] 导出结果为空（0 个算例），请检查 runs\ACCEPT 是否有运行产物。' -ForegroundColor Yellow
                }
            } catch {
                Write-Host "  [警告] 数据包解析失败：$($_.Exception.Message)" -ForegroundColor Yellow
            }
        } else {
            Write-Host "  [警告] 导出失败（退出码 $LASTEXITCODE）。" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [警告] 导出异常：$($_.Exception.Message)" -ForegroundColor Yellow
    }
    if (-not $ExportOk) {
        Write-Host '         将沿用上一次的数据包继续打开。' -ForegroundColor Yellow
    }
} else {
    Write-Host '  [警告] 未找到 Python，跳过刷新，直接打开已有数据。' -ForegroundColor Yellow
}

# ---- 端口占用检查：已被占用时直接复用，不再起第二个服务 ----
$busy = $null
try { $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue } catch { }
$url = "http://127.0.0.1:$Port/"
if ($busy) {
    Write-Host "  端口 $Port 已有服务在运行，直接复用。" -ForegroundColor Gray
    Start-Process $url
    Write-Host ''
    Read-Host '  已打开浏览器。按回车关闭本窗口（不影响已运行的服务）'
    exit 0
}

Write-Host ''
Write-Host "  数据源 : $Proj\runs\ACCEPT"
Write-Host "  地址   : $url" -ForegroundColor Green
Write-Host ''
Write-Host '  浏览器将自动打开；关闭本窗口即停止服务。' -ForegroundColor Gray
Write-Host ''

Start-Process $url

# http.server 是阻塞的；用 Python 自带的静态服务器即可
& $Py -m http.server $Port --directory (Join-Path $Proj 'web') --bind 127.0.0.1

Write-Host ''
Write-Host '  服务已停止。' -ForegroundColor Gray
Read-Host '  按回车退出'
