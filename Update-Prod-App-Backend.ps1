<#
.SYNOPSIS
  Auto-commit tracked changes (version bump), push main to GitHub, then deploy to EC2 via SSM.

.DESCRIPTION
  Run from the repo root. Right-click this file > "Run with PowerShell"
  (or: powershell -ExecutionPolicy Bypass -File .\Prod-ec2-deploy.ps1)

  Commits: git add -u then git commit -m with the next vX.Y.ZZ after "git fetch origin --tags --force":
  highest local tag matching vX.Y.Z, patch + 1 (same rules as Update-Prod-App-Electron.ps1).
  If no such tags exist yet, starts at v0.5.01. Writes backend/VERSION from that version.
  Use -SkipCommit to push only (no local commit).

  Prerequisites:
  - AWS CLI v2 installed and configured (default profile or AWS_PROFILE)
  - IAM permissions: ec2:DescribeInstances, ssm:SendCommand, ssm:GetCommandInvocation
  - EC2: SSM Agent, IAM role with AmazonSSMManagedInstanceCore, app at /opt/saathi

  See also: deploy/ec2/DEPLOY.md

  At start (after branch check), runs "git fetch origin --tags --force" so local tags
  match the remote (overwrites local tag refs if they pointed at different commits).

.PARAMETER SkipPush
  Skip local git push (use if you already pushed).

.PARAMETER SkipCommit
  Skip auto-commit (git add -u + version bump commit). Push/deploy only what is already committed.

.PARAMETER Region
  AWS region (default: ap-south-1).

.PARAMETER InstanceTag
  Value of the EC2 Name tag to target (default: saathi-app).
#>

param(
    [switch] $SkipPush,
    [switch] $SkipCommit,
    [string] $Region = "ap-south-1",
    [string] $InstanceTag = "saathi-app"
)

$ErrorActionPreference = "Stop"

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
}

function Get-MaxSemVerFromGitTags {
    <#
      Returns highest X.Y.Z from local tags matching ^v\d+\.\d+\.\d+$, or $null if none.
    #>
    $lines = @(git tag -l "v*" 2>&1)
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    $bestMaj = -1
    $bestMin = -1
    $bestPat = -1
    foreach ($line in $lines) {
        $t = [string]$line.Trim()
        if ($t -match '^v(\d+)\.(\d+)\.(\d+)$') {
            $maj = [int]$Matches[1]
            $min = [int]$Matches[2]
            $pat = [int]$Matches[3]
            if ($maj -gt $bestMaj -or ($maj -eq $bestMaj -and $min -gt $bestMin) -or ($maj -eq $bestMaj -and $min -eq $bestMin -and $pat -gt $bestPat)) {
                $bestMaj = $maj
                $bestMin = $min
                $bestPat = $pat
            }
        }
    }
    if ($bestMaj -lt 0) {
        return $null
    }
    return "$bestMaj.$bestMin.$bestPat"
}

function Get-NextSemVerFromGitTags {
    <#
      Max vX.Y.Z tag + 1 patch; if no such tags, returns 0.5.1
    #>
    $max = Get-MaxSemVerFromGitTags
    if ($null -eq $max) {
        return "0.5.1"
    }
    if ($max -match '^(\d+)\.(\d+)\.(\d+)$') {
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

# Resolve repo root (script location)
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

Write-Host "Prod-ec2-deploy - repo: $RepoRoot" -ForegroundColor Yellow

# --- Git: must be repo on main (for commit + push) ---
$branch = git rev-parse --abbrev-ref HEAD 2>$null
if ($LASTEXITCODE -ne 0 -or -not $branch) {
    Write-Fail "Not a git repository or git failed."
    exit 1
}
if ($branch -ne "main") {
    Write-Fail "Current branch is '$branch'. Checkout main first."
    exit 1
}

Write-Step "Fetch tags from origin"
git fetch origin --tags --force 2>&1 | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Fail "git fetch origin --tags --force failed."
    exit 1
}
Write-Ok "Tags synced from origin"

# --- Phase 0.5: Auto-commit tracked changes (git add -u) with bumped version message ---
if (-not $SkipCommit) {
    Write-Step "Phase 0.5: Auto-commit (tracked files, version message)"
    git diff-index --quiet HEAD --
    $hasTrackedChanges = $LASTEXITCODE -ne 0
    if (-not $hasTrackedChanges) {
        Write-Ok "Nothing to commit (working tree matches HEAD for tracked files)"
    } else {
        $maxTagVer = Get-MaxSemVerFromGitTags
        $nextSemVer = Get-NextSemVerFromGitTags
        if ($null -ne $maxTagVer) {
            Write-Host "Latest tag version: $maxTagVer -> next $nextSemVer" -ForegroundColor DarkGray
        } else {
            Write-Host "No vX.Y.Z tags yet; using first release $nextSemVer" -ForegroundColor DarkGray
        }
        $verMsg = Get-TagFromVersion $nextSemVer
        $semver = $verMsg.TrimStart("v")
        $versionPath = Join-Path $RepoRoot "backend\VERSION"
        $utf8Ver = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($versionPath, $semver, $utf8Ver)
        Write-Host "Wrote backend/VERSION -> $semver" -ForegroundColor DarkGray
        git add "backend/VERSION"
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git add backend/VERSION failed."
            exit 1
        }
        git add -u
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git add -u failed."
            exit 1
        }
        git commit -m $verMsg
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "git commit failed."
            exit 1
        }
        Write-Ok "Committed: $verMsg"
    }
} else {
    Write-Step "Phase 0.5: Skipped (-SkipCommit)"
}

# --- Phase 1: Git push ---
if (-not $SkipPush) {
    Write-Step "Phase 1: Git push to origin/main"
    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git push failed."
        exit 1
    }
    Write-Ok "Pushed origin main"
} else {
    Write-Step "Phase 1: Skipped (-SkipPush)"
}

# --- Phase 2: Find EC2 instance ---
Write-Step ('Phase 2: Find running EC2 (Name={0}, region={1})' -f $InstanceTag, $Region)
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Write-Fail "AWS CLI not found in PATH. Install AWS CLI v2."
    exit 1
}

$describe = aws ec2 describe-instances `
    --region $Region `
    --filters "Name=tag:Name,Values=$InstanceTag" "Name=instance-state-name,Values=running" `
    --query "Reservations[].Instances[].InstanceId" `
    --output text 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Fail "aws ec2 describe-instances failed: $describe"
    exit 1
}

$ids = @($describe -split '\s+' | Where-Object { $_ -match '^i-' })
if ($ids.Count -eq 0) {
    Write-Fail "No running EC2 instance with Name tag '$InstanceTag' in $Region."
    exit 1
}

$InstanceId = $ids[0]
if ($ids.Count -gt 1) {
    Write-Host ('Multiple instances found; using first: {0} [total: {1}]' -f $InstanceId, $ids.Count) -ForegroundColor DarkYellow
}
Write-Ok "InstanceId=$InstanceId"

# --- Phase 3: SSM deploy ---
Write-Step "Phase 3: SSM remote deploy (git pull, pip, restart saathi-api)"

# One logical script on the instance (bash). SSM AWS-RunShellScript expects "commands" as an array of lines.
$remoteLines = @(
    'set -e'
    'cd /opt/saathi'
    'git fetch origin main'
    'git pull origin main'
    'source backend/venv/bin/activate'
    'pip install -q -r backend/requirements.txt'
    'sudo systemctl restart saathi-api'
    'sleep 3'
    'curl -sS -f http://127.0.0.1:8000/health'
    'echo "remote deploy finished OK"'
)

# Full request JSON avoids Windows quoting issues with --parameters file://...
$cliInputObj = [ordered]@{
    DocumentName   = "AWS-RunShellScript"
    InstanceIds    = @($InstanceId)
    Parameters     = @{ commands = $remoteLines }
}
$cliInputJson = $cliInputObj | ConvertTo-Json -Depth 10 -Compress

$cliInputFile = Join-Path $env:TEMP "prod-ec2-deploy-cli-$(Get-Random).json"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($cliInputFile, $cliInputJson, $utf8NoBom)

try {
    $cliInputUri = "file://" + ($cliInputFile -replace '\\', '/')
    $sendJson = aws ssm send-command --region $Region --cli-input-json $cliInputUri --output json 2>&1

    if ($LASTEXITCODE -ne 0) {
        Write-Fail "aws ssm send-command failed: $sendJson"
        exit 1
    }
    $sendObj = $sendJson | ConvertFrom-Json
    $CommandId = $sendObj.Command.CommandId
    if (-not $CommandId) {
        Write-Fail "No CommandId in response: $sendJson"
        exit 1
    }
} finally {
    Remove-Item -Path $cliInputFile -ErrorAction SilentlyContinue
}

Write-Host "CommandId=$CommandId - waiting for completion..."

$maxWaitSec = 300
$elapsed = 0
$status = "InProgress"
while ($elapsed -lt $maxWaitSec -and $status -in @("InProgress", "Pending", "Delayed")) {
    Start-Sleep -Seconds 2
    $elapsed += 2
    $inv = aws ssm get-command-invocation `
        --region $Region `
        --command-id $CommandId `
        --instance-id $InstanceId `
        --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "get-command-invocation (retry): $inv" -ForegroundColor DarkGray
        continue
    }
    $invObj = $inv | ConvertFrom-Json
    $status = $invObj.Status
}

$final = aws ssm get-command-invocation `
    --region $Region `
    --command-id $CommandId `
    --instance-id $InstanceId `
    --output json 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Fail "Could not read command result: $final"
    exit 1
}

$finalObj = $final | ConvertFrom-Json
Write-Host ""
Write-Host "--- Remote stdout ---" -ForegroundColor DarkCyan
Write-Host ($finalObj.StandardOutputContent)
Write-Host "--- Remote stderr ---" -ForegroundColor DarkYellow
Write-Host ($finalObj.StandardErrorContent)

if ($finalObj.Status -eq "Success") {
    Write-Ok "SSM command Status=Success"
    exit 0
}

Write-Fail "SSM command Status=$($finalObj.Status)"
exit 1
