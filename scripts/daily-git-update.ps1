# Daily Git update: add, commit (with date), push
# Run from project root: .\scripts\daily-git-update.ps1

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $projectRoot ".git"))) {
    Write-Host "Not a git repo (no .git in $projectRoot). Exiting."
    exit 1
}

Set-Location $projectRoot

$dateStr = Get-Date -Format "yyyy-MM-dd"
Write-Host "Daily Git update for $dateStr" -ForegroundColor Cyan

git add .
$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host "No changes to commit. Working tree clean."
    exit 0
}

git commit -m "Daily update: $dateStr"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Commit failed (e.g. nothing to commit or merge conflict)." -ForegroundColor Yellow
    exit $LASTEXITCODE
}

git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "Push failed. Check remote and auth (e.g. token)." -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "Done: changes committed and pushed to origin main." -ForegroundColor Green
