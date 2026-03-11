# Daily startup: Git update, then start uvicorn and npm run dev in new windows.
# Run from project root: .\scripts\daily_startup.ps1
# To build daily_startup.exe, use the .bat with a Bat-to-EXE converter (see Documentation).

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

Set-Location $projectRoot

# 1. Daily Git update
$dateStr = Get-Date -Format "yyyy-MM-dd"
Write-Host "=== Daily Git update ===" -ForegroundColor Cyan
git add .
$status = git status --porcelain
if (-not [string]::IsNullOrWhiteSpace($status)) {
    git commit -m "Daily update: $dateStr"
    git push origin main 2>$null
    Write-Host "Git: committed and pushed." -ForegroundColor Green
} else {
    Write-Host "Git: no changes to commit." -ForegroundColor Gray
}

# 2. Start Backend (uvicorn) in new window
Write-Host "`n=== Starting Backend (uvicorn) ===" -ForegroundColor Cyan
$backendCmd = "cd /d `"$projectRoot`" && call venv\Scripts\activate.bat && cd backend && uvicorn app.main:app --reload --port 8000"
Start-Process cmd -ArgumentList "/k", $backendCmd -WindowStyle Normal

Start-Sleep -Seconds 2

# 3. Start Client (npm run dev) in new window
Write-Host "=== Starting Client (npm run dev) ===" -ForegroundColor Cyan
$clientCmd = "cd /d `"$projectRoot\client`" && npm run dev"
Start-Process cmd -ArgumentList "/k", $clientCmd -WindowStyle Normal

Write-Host "`nBackend and Client started in separate windows." -ForegroundColor Green
