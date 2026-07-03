#Requires -Version 5.1
<#
.SYNOPSIS
  Pre-stage Playwright Chromium for Dealer Saathi 0.9.55 before running the main NSIS installer.

.DESCRIPTION
  Copies the bundled playwright-browsers tree into the Saathi data root (default D:\Saathi)
  and creates scanner/logs folders the NSIS installer expects. Then verifies the revision
  using the bundled job_runner.exe (same as Dealer Saathi Setup 0.9.55.exe).

  Run this on a dealer PC *before* "Dealer Saathi Setup 0.9.55.exe" when the network is slow.
  If browsers are already present with the correct revision, the NSIS installer skips the download.

.PARAMETER SaathiDataRoot
  Stable data root. Default D:\Saathi (used when the app is installed under D:\Saathi\Dealer Saathi).

.EXAMPLE
  .\Install-Playwright-Chromium.ps1
  .\Install-Playwright-Chromium.ps1 -SaathiDataRoot "D:\Saathi"
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string] $SaathiDataRoot = "D:\Saathi"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceBrowsers = Join-Path $PackRoot "playwright-browsers"
$JobRunner = Join-Path $PackRoot "sidecar\job_runner.exe"
$TargetRoot = [System.IO.Path]::GetFullPath($SaathiDataRoot)
$TargetBrowsers = Join-Path $TargetRoot "playwright-browsers"

function Write-Step([string] $Message) {
    Write-Host "[Saathi Playwright] $Message"
}

if (-not (Test-Path -LiteralPath $SourceBrowsers)) {
    throw "Missing pack folder: $SourceBrowsers"
}
if (-not (Test-Path -LiteralPath $JobRunner)) {
    throw "Missing sidecar job_runner.exe: $JobRunner"
}

$chrome = Get-ChildItem -LiteralPath $SourceBrowsers -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $chrome) {
    throw "Pack playwright-browsers does not contain chrome.exe. Rebuild the pack on a fast network."
}

Write-Step "Pack revision folder: $($chrome.Directory.Parent.Name)"
Write-Step "Target Saathi data root: $TargetRoot"

$dirs = @(
    $TargetRoot,
    $TargetBrowsers,
    (Join-Path $TargetRoot "scanner\landing"),
    (Join-Path $TargetRoot "scanner\processed"),
    (Join-Path $TargetRoot "logs")
)
foreach ($d in $dirs) {
    if (-not (Test-Path -LiteralPath $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Write-Step "Created $d"
    }
}

Write-Step "Copying playwright-browsers (~300MB) ..."
& robocopy.exe $SourceBrowsers $TargetBrowsers /E /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -ge 8) {
    throw "robocopy failed with exit code $robocopyExit"
}

Write-Step "Verifying Chromium revision with bundled job_runner.exe ..."
$env:PLAYWRIGHT_BROWSERS_PATH = $TargetBrowsers
if ($env:SAATHI_BASE_DIR) {
    Remove-Item Env:\SAATHI_BASE_DIR -ErrorAction SilentlyContinue
}

$verify = Start-Process -FilePath $JobRunner -ArgumentList @(
    "--install-playwright-browsers",
    $TargetRoot
) -Wait -PassThru -NoNewWindow -WorkingDirectory $PackRoot

if ($verify.ExitCode -ne 0) {
    throw "job_runner verification failed (exit $($verify.ExitCode)). See $TargetRoot\logs\sidecar.log"
}

$targetChrome = Get-ChildItem -LiteralPath $TargetBrowsers -Recurse -Filter "chrome.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
Write-Step "Ready: $($targetChrome.FullName)"
Write-Step "You can now run 'Dealer Saathi Setup 0.9.55.exe'. The Playwright download step should finish immediately."

exit 0
