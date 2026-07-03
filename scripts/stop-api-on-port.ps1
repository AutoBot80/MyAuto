# Stop processes listening on a TCP port and orphan uvicorn --reload workers.
# Used by 0_Local_Dev_Startup.bat before starting a fresh uvicorn.
param(
    [int] $Port = 8000
)

$ErrorActionPreference = "SilentlyContinue"

function Stop-ProcessTree {
    param([int] $ProcessId)
    if ($ProcessId -le 0) { return }
    $alive = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $alive) { return }
    Write-Host "Stopping PID $ProcessId ..."
    taskkill /F /T /PID $ProcessId 2>$null | Out-Null
}

$pids = [System.Collections.Generic.HashSet[int]]::new()

try {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { [void]$pids.Add([int]$_.OwningProcess) }
} catch {
    # Windows PowerShell 5 fallback
    netstat -ano | Select-String ":$Port\s" | ForEach-Object {
        if ($_ -match "LISTENING\s+(\d+)\s*$") {
            [void]$pids.Add([int]$Matches[1])
        }
    }
}

# Orphan uvicorn reload workers (parent died; netstat may still show ghost parent PIDs).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
        $_.CommandLine -like '*spawn_main*' -and $_.CommandLine -like '*parent_pid=*'
    } |
    ForEach-Object { [void]$pids.Add([int]$_.ProcessId) }

if ($pids.Count -eq 0) {
    Write-Host "Port $Port is free (no listeners or orphan workers found)."
    exit 0
}

foreach ($procId in ($pids | Sort-Object)) {
    Stop-ProcessTree -ProcessId $procId
}

Start-Sleep -Seconds 1

$stillListening = $false
try {
    $stillListening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue).Count -gt 0
} catch {
    $stillListening = [bool](netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING")
}

if ($stillListening) {
    Write-Host ""
    Write-Host "WARNING: Port $Port may still be in use. Close MyAuto Backend windows or reboot if needed." -ForegroundColor Yellow
    exit 1
}

Write-Host "Port $Port is free."
exit 0
