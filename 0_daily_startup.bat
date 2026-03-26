@echo off
REM Daily startup: Git update, then start backend (uvicorn) and client (npm run dev).
REM Place this file in the project root. To create daily_startup.exe, use a Bat-to-EXE converter.

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo === Closing previous MyAuto windows (if any) ===
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Backend*" >nul 2>&1
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Watcher*" >nul 2>&1
taskkill /F /T /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq MyAuto Client*" >nul 2>&1
timeout /t 1 /nobreak >nul
echo Old windows closed.
echo.

echo === Daily Git update ===
git add .
git status
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"') do set "DATE=%%i"
git commit -m "Daily update: %DATE%" 2>nul
echo Pushing all latest changes to remote...
git push origin main 2>nul
if %errorlevel% neq 0 (
  git push origin master 2>nul
)
echo Git update done.
echo.

echo === Starting Backend (uvicorn) in new window ===
start "MyAuto Backend" cmd /k "title MyAuto Backend && cd /d "%ROOT%backend" && call ..\venv\Scripts\activate.bat && python -m uvicorn app.main:app --reload --reload-dir app --port 8000"

echo === Starting Watcher in new window ===
start "MyAuto Watcher" cmd /k "title MyAuto Watcher && cd /d "%ROOT%backend" && call ..\venv\Scripts\activate.bat && python run_watcher.py && pause"

timeout /t 2 /nobreak >nul

echo === Starting Client (npm run dev) in new window ===
start "MyAuto Client" cmd /k "title MyAuto Client && cd /d "%ROOT%client" && npm run dev"

echo.
echo Backend and Client started. Close this window when done.
pause
