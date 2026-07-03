@echo off

setlocal EnableExtensions

REM Double-click: My Orders mobile+Order# search, drill order, Apply Campaign + Create Invoice

cd /d "%~dp0.."

set "PYTHONPATH=%CD%\backend"

if not exist "%CD%\backend\app" (

  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app

  pause

  exit /b 1

)

echo PYTHONPATH=%PYTHONPATH%

echo Defaults: Mobile 9351244099 / First name SHRUTI / Order# empty (all matching rows)
echo.
echo Optional: set CREATE_INVOICE_TEST_ORDER_NUMBER=11870-02-SVSO-0726-800 to restrict to one row
echo Optional: set CREATE_INVOICE_TEST_FIRST_NAME=SHRUTI for duplicate-mobile name+date guard
echo Optional: set CREATE_INVOICE_TEST_SKIP_PRINT_REPORTS=1 to skip Run Report PDF downloads

echo.

echo REQUIRED in backend\.env for real Siebel: DMS_BASE_URL, DMS_MODE=real, login fields

echo REQUIRED for auto Create Invoice click: ENVIRONMENT=prod  (otherwise Apply Campaign only)

echo.

echo Optional overrides:

echo   set CREATE_INVOICE_TEST_MOBILE=9351244099

echo   set CREATE_INVOICE_TEST_FIRST_NAME=SHRUTI

echo   set CREATE_INVOICE_TEST_SKIP_PRINT_REPORTS=1

echo   set CREATE_INVOICE_TEST_ORDER_NUMBER=

echo   set CREATE_INVOICE_TEST_FULL_CHASSIS=...   (17-char VIN if outcome is pending)

echo   set CREATE_INVOICE_TEST_LINE_ITEM_DISCOUNT=...

echo   set CREATE_INVOICE_TEST_FINANCIER_NAME=...

echo   set CREATE_INVOICE_TEST_FORCE_ALLOCATED_PATH=1   (skip VIN attach; order already allocated)

echo   set CREATE_INVOICE_TEST_POST_LOGIN_WAIT_SEC=15

echo   set CREATE_INVOICE_TEST_INVOICE_POLL_SEC=120

echo   set CREATE_INVOICE_TEST_PAUSE_BEFORE_EXIT=0

echo.

python "%~dp0test_dms_create_invoice.py"

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% neq 0 echo Exit code %EXITCODE%

pause

exit /b %EXITCODE%

