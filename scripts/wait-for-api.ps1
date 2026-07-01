# Wait until something listens on localhost:port (e.g. uvicorn). Used by 0_Local_Dev_Startup.bat
param(
    [int] $Port = 8000,
    [int] $MaxSeconds = 90,
    [string] $RepoRoot = "",
    [switch] $VerifyHealth
)
$ErrorActionPreference = "SilentlyContinue"
for ($i = 0; $i -lt $MaxSeconds; $i++) {
    $t = New-Object System.Net.Sockets.TcpClient
    try {
        $t.Connect("127.0.0.1", $Port)
        $t.Close()
        Write-Host "API is listening on http://127.0.0.1:$Port/"
        if ($VerifyHealth) {
            try {
                $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
                $expectedCommit = ""
                if ($RepoRoot -and (Test-Path -LiteralPath $RepoRoot)) {
                    $expectedCommit = (& git -C $RepoRoot rev-parse --short HEAD 2>$null | Out-String).Trim()
                }
                if ($expectedCommit -and $health.git_commit -and $health.git_commit -ne $expectedCommit) {
                    Write-Host ""
                    Write-Host "WARNING: /health reports git_commit=$($health.git_commit) but workspace HEAD=$expectedCommit." -ForegroundColor Yellow
                    Write-Host "A stale API may still be serving port $Port. Re-run stop-api-on-port.ps1 or close MyAuto Backend windows." -ForegroundColor Yellow
                } else {
                    Write-Host "Health OK (version $($health.version), commit $($health.git_commit))."
                }
            } catch {
                Write-Host "WARNING: Port $Port is open but /health did not respond." -ForegroundColor Yellow
            }
        }
        exit 0
    }
    catch {
        # port not open yet
    }
    Start-Sleep -Seconds 1
}
Write-Host ""
Write-Host "WARNING: Nothing accepted connections on port $Port within ${MaxSeconds}s." -ForegroundColor Yellow
Write-Host "Open the MyAuto Backend window: check venv (project-root\venv\Scripts\activate.bat), Python errors, or port $Port already in use." -ForegroundColor Yellow
exit 0
