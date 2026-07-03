@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
if not exist "%CD%\backend\app" (
  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app
  pause
  exit /b 1
)
echo PYTHONPATH=%PYTHONPATH%
echo Defaults: Narayan / 9587946074 / Splendor+ BLA (View Customers screenshot)
echo Manual login ON by default — type credentials in the browser; automation waits up to 120s.
echo Optional: set CUSTOMER_TEST_MANUAL_LOGIN=0   (use cached/env auto-login instead)
echo Optional: set CUSTOMER_TEST_POST_LOGIN_WAIT_SEC=20   (extra settle after login; default 15)
echo Optional: set CUSTOMER_TEST_PAUSE_BEFORE_EXIT=0
echo.
python "%~dp0test_dms_prepare_customer.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
