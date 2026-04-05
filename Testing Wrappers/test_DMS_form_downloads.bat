@echo off
setlocal EnableExtensions
REM Double-click launcher: repo root = parent of this folder (My Auto.AI)
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
if not exist "%CD%\backend\app" (
  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app
  pause
  exit /b 1
)
echo PYTHONPATH=%PYTHONPATH%
echo.
python "%~dp0test_DMS_form_downloads.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
