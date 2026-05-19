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
echo Optional: set CHALLAN_TEST_ORDER_NUMBER=11870-02-SVSO-0526-408
echo Optional: set CHALLAN_TEST_POST_LOGIN_WAIT_SEC=15  (seconds after open before My Orders; default 2)
echo Optional: set CHALLAN_TEST_PAUSE_BEFORE_EXIT=0    (skip "Press Enter" so script exits immediately)
echo.
python "%~dp0test_challan_order_resume_my_orders.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
