@echo off
setlocal EnableExtensions
REM Double-click: My Orders mobile search, drill invoice/order, scrape s_2_l Round Off Amount
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
if not exist "%CD%\backend\app" (
  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app
  pause
  exit /b 1
)
echo PYTHONPATH=%PYTHONPATH%
echo Default mobile: 9785562020
echo Optional: set TOTAL_COST_TEST_MOBILE=9785562020
echo Optional: set TOTAL_COST_TEST_POST_LOGIN_SEC=15
echo Optional: set TOTAL_COST_TEST_INVOICE_POLL_SEC=120
echo Optional: set TOTAL_COST_TEST_PAUSE_BEFORE_EXIT=0
echo.
python "%~dp0test_my_orders_total_cost_scrape.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
