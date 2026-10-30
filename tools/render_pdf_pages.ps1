# 用 Windows.Data.Pdf 把扫描版 PDF 的指定页渲染成 PNG
param(
    [Parameter(Mandatory=$true)][string]$PdfPath,
    [Parameter(Mandatory=$true)][string]$Pages,
    [Parameter(Mandatory=$true)][string]$OutDir,
    [int]$Width = 1400
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

# --- 取 AsTask 重载
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
$asTaskAction = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction'
})[0]

function Await-Op($op, [Type]$type) {
    $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    return $t.Result
}
function Await-Action($act) {
    $t = $asTaskAction.Invoke($null, @($act))
    $t.Wait(-1) | Out-Null
}

[void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
[void][Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType=WindowsRuntime]
[void][Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$file = Await-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync($PdfPath)) ([Windows.Storage.StorageFile])
$doc  = Await-Op ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
Write-Host ("总页数: " + $doc.PageCount)

foreach ($p in ($Pages -split "," | ForEach-Object { [int]$_ })) {
    if ($p -lt 1 -or $p -gt $doc.PageCount) { continue }
    $page = $doc.GetPage($p - 1)
    $opt = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
    $opt.DestinationWidth = [uint32]$Width
    $stream = [Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
    Await-Action ($page.RenderToStreamAsync($stream, $opt))

    # 读回字节
    $size = [int]$stream.Size
    $stream.Seek(0)
    $reader = [Windows.Storage.Streams.DataReader]::new($stream.GetInputStreamAt(0))
    Await-Op ($reader.LoadAsync([uint32]$size)) ([uint32]) | Out-Null
    $bytes = New-Object byte[] $size
    $reader.ReadBytes($bytes)
    $reader.Dispose(); $stream.Dispose(); $page.Dispose()

    $name = [System.IO.Path]::GetFileNameWithoutExtension($PdfPath)
    $out = Join-Path $OutDir ("{0}_p{1:D3}.png" -f $name, $p)
    [System.IO.File]::WriteAllBytes($out, $bytes)
    Write-Host ("  p{0} -> {1} ({2:N0} B)" -f $p, (Split-Path $out -Leaf), $bytes.Length)
}
Write-Host "完成"
