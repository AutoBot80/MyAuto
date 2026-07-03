@echo off
setlocal EnableExtensions
REM Portal warm + login/autofill test (DMS + MISP + CPA)
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
if not exist "%CD%\backend\app" (
  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app
  pause
  exit /b 1
)
echo PYTHONPATH=%PYTHONPATH%
echo.
echo ========================================================================
echo  DMS + MISP + CPA — warm open then login/autofill gate on each portal
echo ========================================================================
echo Requires backend\.env: DMS_BASE_URL, INSURANCE_BASE_URL, ALLIANCE_CPA_PORTAL_URL
echo DMS login: browser autofill first (same login gate as Create Invoice / Fill DMS).
echo   demo/demo in .env is ignored; non-demo DMS_LOGIN_* is env fallback only after autofill.
echo DMS timing-only (no login): test_dms_warm_timing.bat
echo Browser stays open until you press Enter in this window.
echo.
python "%~dp0test_dms_warm_browser.py" --sites all --with-login --visible --debug %*
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
