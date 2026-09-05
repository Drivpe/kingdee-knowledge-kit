# start-service.ps1 — 参数化启动金蝶知识检索服务(匿名)
# 用法: powershell -ExecutionPolicy Bypass -File start-service.ps1 [-Port 4097] [-InstallRoot <dir>] [-Restart]
param(
    [int]$Port = 4097,
    [string]$InstallRoot = (Join-Path $env:USERPROFILE ".kingdee-kit"),
    [switch]$Restart
)
$ErrorActionPreference = "Continue"
$svc = Join-Path $InstallRoot "service\kingdee-ksearch-service.py"
if (-not (Test-Path $svc)) { Write-Host "service not found: $svc" -ForegroundColor Red; exit 1 }

# Python 探测: py -3 → python
$pyCmd = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
else { Write-Host "Python 3.8+ not found" -ForegroundColor Red; exit 1 }

if ($Restart) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
            Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Milliseconds 800
}

Start-Process -FilePath $pyCmd -ArgumentList ($pyArgs + @($svc, "$Port")) -WindowStyle Hidden
foreach ($i in 1..24) {
    Start-Sleep -Milliseconds 500
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($h.anonymous) { Write-Host "service ready on :$Port ($($h.service))" -ForegroundColor Green; exit 0 }
    } catch {}
}
Write-Host "service not ready on :$Port" -ForegroundColor Red
exit 1
