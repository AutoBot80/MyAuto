#Requires -Version 5.1
<#
.SYNOPSIS
  Rebuild Playwright-Chromium-Pack-0.9.55 from a machine that already has Saathi 0.9.55 installed.

.DESCRIPTION
  Copies D:\Saathi\playwright-browsers and job_runner.exe into this pack folder.
  Run on HQ with good internet after: job_runner.exe --install-playwright-browsers D:\Saathi

.PARAMETER SaathiDataRoot
  Default D:\Saathi

.PARAMETER PackRoot
  Output folder. Default: this script's directory.
#>
[CmdletBinding()]
param(
    [string] $SaathiDataRoot = "D:\Saathi",
    [string] $PackRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$ErrorActionPreference = "Stop"
$srcBrowsers = Join-Path $SaathiDataRoot "playwright-browsers"
$srcJobRunner = Join-Path $SaathiDataRoot "Dealer Saathi\resources\sidecar\job_runner.exe"
$destBrowsers = Join-Path $PackRoot "playwright-browsers"
$destSidecar = Join-Path $PackRoot "sidecar"

if (-not (Test-Path -LiteralPath $srcBrowsers)) {
    throw "Missing $srcBrowsers. Run job_runner --install-playwright-browsers first."
}
if (-not (Test-Path -LiteralPath $srcJobRunner)) {
    throw "Missing $srcJobRunner. Install Dealer Saathi 0.9.55 first."
}

New-Item -ItemType Directory -Force -Path $destSidecar | Out-Null
if (Test-Path -LiteralPath $destBrowsers) {
    Remove-Item -LiteralPath $destBrowsers -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $destBrowsers | Out-Null

Write-Host "Copying playwright-browsers ..."
& robocopy.exe $srcBrowsers $destBrowsers /E /R:2 /W:2 | Out-Null
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -ge 8) { throw "robocopy failed: $robocopyExit" }

Copy-Item -LiteralPath $srcJobRunner -Destination (Join-Path $destSidecar "job_runner.exe") -Force
$mb = [math]::Round((Get-ChildItem $PackRoot -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Pack rebuilt at $PackRoot ($mb MB)"
