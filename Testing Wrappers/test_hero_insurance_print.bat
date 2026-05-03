@echo off
setlocal EnableExtensions
REM Double-click launcher: repo root = parent of this folder (My Auto.AI)
cd /d "%~dp0.."
if not exist "%CD%\backend\app\services\fill_hero_insurance_service.py" (
  echo ERROR: backend\app\services\fill_hero_insurance_service.py not found. Expected repo layout: My Auto.AI\backend\...
  pause
  exit /b 1
)
if not exist "%CD%\backend\.env" (
  echo WARNING: backend\.env not found. INSURANCE_BASE_URL may be missing.
  echo.
)
echo Repo: %CD%
echo Running: python "%~dp0test_hero_misp_print_policy.py"
echo.
python "%~dp0test_hero_misp_print_policy.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
