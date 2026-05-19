@echo off
REM Daily startup: start backend (uvicorn), watcher, and client (npm run dev). Git sync: use scripts\daily-git-update.ps1 or Option 1/2 in Documentation\git-daily-workflow.md.
REM Requires Python venv at "%ROOT%venv" (see Documentation\git-daily-workflow.md).
REM If Vite shows ECONNREFUSED :8000, open "MyAuto Backend" and read the error (venv path, import, port in use).

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo === Closing previous MyAuto windows (if any) ===
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Backend*" >nul 2>&1
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Watcher*" >nul 2>&1
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Client*" >nul 2>&1
timeout /t 1 /nobreak >nul
echo Old windows closed.
echo.

echo === Starting Backend (uvicorn) in new window ===
start "MyAuto Backend" cmd /k "title MyAuto Backend && cd /d "%ROOT%backend" && call ..\venv\Scripts\activate.bat && python -m uvicorn app.main:app --reload --reload-dir app --port 8000"

echo === Starting Watcher in new window ===
start "MyAuto Watcher" cmd /k "title MyAuto Watcher && cd /d "%ROOT%backend" && call ..\venv\Scripts\activate.bat && python run_watcher.py && pause"

echo === Waiting for API on port 8000 (avoids Vite proxy ECONNREFUSED while uvicorn starts) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\wait-for-api.ps1"

echo === Starting Client (npm run dev) in new window ===
start "MyAuto Client" cmd /k "title MyAuto Client && cd /d "%ROOT%client" && npm run dev"

echo.
echo Backend and Client started. Close this window when done.
pause
