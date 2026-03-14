@echo off
REM Daily startup: Git update, then start backend (uvicorn) and client (npm run dev).
REM Place this file in the project root. To create daily_startup.exe, use a Bat-to-EXE converter.

set "ROOT=%~dp0"
cd /d "%ROOT%"

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
start "MyAuto Backend" cmd /k "cd /d "%ROOT%" && call venv\Scripts\activate.bat && cd backend && uvicorn app.main:app --reload --port 8000"

timeout /t 2 /nobreak >nul

echo === Starting Client (npm run dev) in new window ===
start "MyAuto Client" cmd /k "cd /d "%ROOT%client" && npm run dev"

echo.
echo Backend and Client started. Close this window when done.
pause
