<#
.SYNOPSIS
  Build the Electron desktop app (client + sidecar + installer), auto-commit, push, and tag for release.

.DESCRIPTION
  Run from the repo root. Right-click this file > "Run with PowerShell"
  (or: powershell -ExecutionPolicy Bypass -File .\Deploy-Prod-Electron.ps1)

  Steps:
    Phase 1 - Build client (npm run build:client)
    Phase 2 - Build sidecar (npm run build:sidecar via PyInstaller)
    Phase 3 - Build Electron installer (npm run build:electron)
    Phase 4 - Auto-commit tracked changes with bumped version message
    Phase 5 - Git push to origin/main
    Phase 6 - Tag the commit (vX.Y.ZZ) and push the tag

  The tag push triggers the GitHub Actions "Electron Release" workflow which
  publishes the installer to GitHub Releases for auto-update.

  Version bumping: reads the current "version" from electron/package.json,
  increments the patch number (0.5.0 -> 0.5.1 -> 0.5.2), updates the file,
  and uses that as the commit message and git tag.

  Prerequisites:
  - Node.js / npm in PATH
  - Python 3.12+ in PATH (for PyInstaller sidecar build)
  - GH_TOKEN env var set if you want auto-update token baked into the local build

.PARAMETER SkipBuild
  Skip all build phases (client, sidecar, electron). Commit + push + tag only.

.PARAMETER SkipCommit
  Skip auto-commit. Push + tag only what is already committed.

.PARAMETER SkipPush
  Skip git push and tag push.

.PARAMETER SkipTag
  Skip creating and pushing the git tag (build + commit + push, but no release trigger).

.PARAMETER BuildOnly
  Run builds only — no commit, push, or tag. Useful for local testing.
#>

param(
    [switch] $SkipBuild,
    [switch] $SkipCommit,
    [switch] $SkipPush,
    [switch] $SkipTag,
    [switch] $BuildOnly
)

# Never let PowerShell auto-terminate on stderr output from npm/git/python.
# All error handling is done via $LASTEXITCODE checks.
$ErrorActionPreference = "Continue"

function Write-Step {
    param([string] $Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
}

function Write-Ok {
    param([string] $Message)
    Write-Host "OK: $Message" -ForegroundColor Green
}

function Write-Fail {
    param([string] $Message)
    Write-Host "FAIL: $Message" -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

function Get-ElectronVersion {
    $pkgPath = Join-Path (Join-Path $script:RepoRoot "electron") "package.json"
    $pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json
    return $pkg.version
}

function Set-ElectronVersion {
    param([string] $NewVersion)
    $pkgPath = Join-Path (Join-Path $script:RepoRoot "electron") "package.json"
    $raw = Get-Content $pkgPath -Raw
    $updated = $raw -replace '"version"\s*:\s*"[^"]*"', "`"version`": `"$NewVersion`""
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($pkgPath, $updated, $utf8NoBom)
}

function Get-BumpedVersion {
    param([string] $Current)
    if ($Current -match '^(\d+)\.(\d+)\.(\d+)$') {
        $maj = [int]$Matches[1]
        $min = [int]$Matches[2]
        $pat = [int]$Matches[3] + 1
        return "$maj.$min.$pat"
    }
    return "0.5.1"
}

function Get-TagFromVersion {
    param([string] $SemVer)
    if ($SemVer -match '^(\d+)\.(\d+)\.(\d+)$') {
        $maj = [int]$Matches[1]
        $min = [int]$Matches[2]
        $pat = [int]$Matches[3]
        if ($pat -le 99) {
            $patStr = "{0:D2}" -f $pat
        } else {
            $patStr = "$pat"
        }
        return "v$maj.$min.$patStr"
    }
    return "v0.5.01"
}

# -------------------------------------------------------------------

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

# Load GH_TOKEN from .env if not already set in the environment
if (-not $env:GH_TOKEN) {
    $dotEnv = Join-Path $RepoRoot ".env"
    if (Test-Path $dotEnv) {
        Get-Content $dotEnv | ForEach-Object {
            if ($_ -match '^\s*GH_TOKEN\s*=\s*(.+)$') {
                $val = $Matches[1].Trim()
                $val = $val.Trim('"').Trim("'")
                $env:GH_TOKEN = $val
            }
        }
        if ($env:GH_TOKEN) {
            Write-Host "Loaded GH_TOKEN from .env" -ForegroundColor DarkGreen
        }
    }
}

$ElectronDir = Join-Path $RepoRoot "electron"

Write-Host "Deploy-Prod-Electron - repo: $RepoRoot" -ForegroundColor Yellow

# --- Sanity: must be on main ---
$branch = (git rev-parse --abbrev-ref HEAD 2>&1) | Out-String
$branch = $branch.Trim()
if ($LASTEXITCODE -ne 0 -or -not $branch) {
    Write-Fail "Not a git repository or git failed."
}
if ($branch -ne "main") {
    Write-Fail "Current branch is '$branch'. Checkout main first."
}

# --- Version bump ---
$currentVer = Get-ElectronVersion
$nextVer = Get-BumpedVersion $currentVer
$tag = Get-TagFromVersion $nextVer

Write-Host "Current version: $currentVer" -ForegroundColor DarkGray
Write-Host "Next version:    $nextVer  (tag: $tag)" -ForegroundColor Yellow
Write-Host ""

if (-not $BuildOnly) {
    Set-ElectronVersion $nextVer
    Write-Ok "electron/package.json version -> $nextVer"
} else {
    $nextVer = $currentVer
    $tag = Get-TagFromVersion $currentVer
    Write-Host "BuildOnly - keeping version $currentVer" -ForegroundColor DarkGray
}

# =================== BUILD PHASES ===================

if (-not $SkipBuild) {
    # --- Phase 0.5: Inject update token ---
    $ghToken = $env:GH_TOKEN
    $tokenFile = Join-Path $ElectronDir "resources\update-token.json"
    if ($ghToken) {
        Write-Step "Injecting GH_TOKEN into update-token.json"
        New-Item -ItemType Directory -Force -Path (Split-Path $tokenFile) | Out-Null
        @{ token = $ghToken } | ConvertTo-Json | Set-Content -Path $tokenFile -Encoding UTF8
        Write-Ok "Token injected ($($ghToken.Length) chars)"
    } else {
        Write-Host "WARNING: GH_TOKEN not set - auto-update will be disabled in this build." -ForegroundColor Yellow
        Write-Host '  Set it with:  $env:GH_TOKEN = "ghp_..."  before running this script.' -ForegroundColor DarkYellow
    }

    # --- Phase 1: Build client ---
    Write-Step "Phase 1: Build client (React/Vite)"
    Push-Location $ElectronDir
    npm run build:client 2>&1 | ForEach-Object { Write-Host $_ }
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Write-Fail "npm run build:client failed (exit code $rc)." }
    Write-Ok "Client built"

    # --- Phase 2: Build sidecar ---
    Write-Step "Phase 2: Build sidecar (PyInstaller)"
    Push-Location $ElectronDir
    npm run build:sidecar 2>&1 | ForEach-Object { Write-Host $_ }
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Write-Fail "npm run build:sidecar failed (exit code $rc)." }
    Write-Ok "Sidecar built"

    # --- Phase 3: Build Electron installer ---
    Write-Step "Phase 3: Build Electron installer (NSIS)"
    Push-Location $ElectronDir
    npm run build:electron 2>&1 | ForEach-Object { Write-Host $_ }
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Write-Fail "npm run build:electron failed (exit code $rc)." }
    Write-Ok "Electron installer built"
} else {
    Write-Step "Phases 1-3: Skipped (-SkipBuild)"
}

if ($BuildOnly) {
    Write-Host ""
    Write-Ok "Build complete (-BuildOnly). No commit/push/tag."
    Read-Host "Press Enter to close"
    exit 0
}

# =================== GIT PHASES ===================

# --- Phase 4: Auto-commit ---
if (-not $SkipCommit) {
    Write-Step "Phase 4: Auto-commit (tracked files)"
    git add -u 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Fail "git add -u failed." }

    git diff --cached --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        git commit -m $tag 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Write-Fail "git commit failed." }
        Write-Ok "Committed: $tag"
    } else {
        Write-Ok "Nothing to commit (working tree matches HEAD for tracked files)"
    }
} else {
    Write-Step "Phase 4: Skipped (-SkipCommit)"
}

# --- Phase 5: Git push ---
if (-not $SkipPush) {
    Write-Step "Phase 5: Git push to origin/main"
    git push origin main 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Fail "git push failed." }
    Write-Ok "Pushed origin/main"
} else {
    Write-Step "Phase 5: Skipped (-SkipPush)"
}

# --- Phase 6: Tag and push tag ---
if (-not $SkipTag -and -not $SkipPush) {
    Write-Step "Phase 6: Tag $tag and push"
    $existingTag = (git tag -l $tag 2>&1) | Out-String
    if ($existingTag.Trim()) {
        Write-Host "Tag $tag already exists locally - skipping tag create." -ForegroundColor DarkYellow
    } else {
        git tag $tag 2>&1 | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { Write-Fail "git tag $tag failed." }
    }
    git push origin $tag 2>&1 | ForEach-Object { Write-Host $_ }
    if ($LASTEXITCODE -ne 0) { Write-Fail "git push origin $tag failed." }
    Write-Ok "Tag $tag pushed - GitHub Actions will build and publish the release."
} else {
    Write-Step "Phase 6: Skipped (-SkipTag or -SkipPush)"
}

Write-Host ""
Write-Ok "Done! Version $nextVer ($tag)"
Read-Host "Press Enter to close"
