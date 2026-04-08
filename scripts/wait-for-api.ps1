# Wait until something listens on localhost:port (e.g. uvicorn). Used by 0_daily_startup.bat
param(
    [int] $Port = 8000,
    [int] $MaxSeconds = 90
)
$ErrorActionPreference = "SilentlyContinue"
for ($i = 0; $i -lt $MaxSeconds; $i++) {
    $t = New-Object System.Net.Sockets.TcpClient
    try {
        $t.Connect("127.0.0.1", $Port)
        $t.Close()
        Write-Host "API is listening on http://127.0.0.1:$Port/"
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
