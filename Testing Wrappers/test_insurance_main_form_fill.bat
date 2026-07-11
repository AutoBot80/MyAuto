@echo off

setlocal EnableExtensions

cd /d "%~dp0.."

set "PYTHONPATH=%CD%\backend"

if not exist "%CD%\backend\app\services\fill_hero_insurance_service.py" (

  echo ERROR: backend\app\services\fill_hero_insurance_service.py not found. Expected repo layout: My Auto.AI\backend\...

  pause

  exit /b 1

)

if not exist "%CD%\backend\.env" (

  echo WARNING: backend\.env not found. INSURANCE_BASE_URL may be missing.

  echo.

)

rem --- Puneet Kumar / Shriram Finance Ltd. financier focus (dealer 100001) ---
rem Override from prod Add Sales when sale is not in local auto_ai DB:
rem   set INSURANCE_TEST_CUSTOMER_ID=<id>
rem   set INSURANCE_TEST_VEHICLE_ID=<id>
rem   set INSURANCE_TEST_STAGING_ID=<uuid>
rem   set INSURANCE_TEST_SUBFOLDER=8209031977_DDMMYY
set INSURANCE_TEST_DEALER_ID=100001
set INSURANCE_TEST_CUSTOMER_ID=0
set INSURANCE_TEST_VEHICLE_ID=0
set INSURANCE_TEST_STAGING_ID=
set INSURANCE_TEST_SUBFOLDER=
set INSURANCE_TEST_VIN=MBLHAW431T9E44739
set INSURANCE_TEST_CHASSIS_NUM=MBLHAW431T9E44739
set INSURANCE_TEST_ENGINE_NUM=03038
set INSURANCE_TEST_MOBILE_NUMBER=8209031977
set INSURANCE_TEST_EXPECTED_FINANCER=Shriram Finance Ltd.
set INSURANCE_TEST_INSURER=BAJAJ GENERAL INSURANCE LIMITED
set INSURANCE_TEST_CPI_REQD=Y
set INSURANCE_TEST_USE_DB=1

echo Repo: %CD%

echo.

echo Generate Insurance — PUNEET KUMAR (financier trace)

echo   dealer=%INSURANCE_TEST_DEALER_ID%  customer_id=%INSURANCE_TEST_CUSTOMER_ID%  vehicle_id=%INSURANCE_TEST_VEHICLE_ID%

echo   VIN=%INSURANCE_TEST_VIN%  engine=%INSURANCE_TEST_ENGINE_NUM%  mobile=%INSURANCE_TEST_MOBILE_NUMBER%

echo   expected_financier=%INSURANCE_TEST_EXPECTED_FINANCER%

echo   insurer=BAJAJ GENERAL INSURANCE LIMITED  addons: ND Cover, Rim Safeguard, RSA

echo.

echo DB mode: form_insurance_view + staging when IDs resolve; else patched dict with Shriram Finance Ltd.

echo Financier trace logs: customer_master, form_insurance_view, staging, OCR, built value.

echo Optional: set INSURANCE_TEST_PAUSE_BEFORE_EXIT=0

echo.

python "%~dp0test_insurance_main_form_fill.py"

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% neq 0 echo Exit code %EXITCODE%

pause

exit /b %EXITCODE%
