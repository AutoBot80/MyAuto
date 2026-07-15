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

rem --- KHOOB KALA / nominee relation Son, age 45 (dealer 100001) ---
rem Override from prod Add Sales when sale is not in local auto_ai DB:
rem   set INSURANCE_TEST_CUSTOMER_ID=<id>
rem   set INSURANCE_TEST_VEHICLE_ID=<id>
rem   set INSURANCE_TEST_STAGING_ID=<uuid>
rem   set INSURANCE_TEST_SUBFOLDER=9772098406_DDMMYY
set INSURANCE_TEST_DEALER_ID=100001
set INSURANCE_TEST_CUSTOMER_ID=0
set INSURANCE_TEST_VEHICLE_ID=0
set INSURANCE_TEST_STAGING_ID=
set INSURANCE_TEST_SUBFOLDER=
set INSURANCE_TEST_VIN=MBLHAW436THA20236
set INSURANCE_TEST_CHASSIS_NUM=MBLHAW436THA20236
set INSURANCE_TEST_ENGINE_NUM=T4463
set INSURANCE_TEST_MOBILE_NUMBER=9772098406
set INSURANCE_TEST_NOMINEE_RELATIONSHIP=Son
set INSURANCE_TEST_NOMINEE_AGE=45
set INSURANCE_TEST_EXPECTED_FINANCER=Hinduja Leyland Finance
set INSURANCE_TEST_INSURER=BAJAJ GENERAL INSURANCE LIMITED
set INSURANCE_TEST_CPI_REQD=Y
set INSURANCE_TEST_USE_DB=1

echo Repo: %CD%

echo.

echo Generate Insurance — KHOOB KALA (nominee relation Son, age 45)

echo   dealer=%INSURANCE_TEST_DEALER_ID%  customer_id=%INSURANCE_TEST_CUSTOMER_ID%  vehicle_id=%INSURANCE_TEST_VEHICLE_ID%

echo   VIN=%INSURANCE_TEST_VIN%  engine=%INSURANCE_TEST_ENGINE_NUM%  mobile=%INSURANCE_TEST_MOBILE_NUMBER%

echo   nominee_relationship=%INSURANCE_TEST_NOMINEE_RELATIONSHIP%  nominee_age=%INSURANCE_TEST_NOMINEE_AGE%  expected_financier=%INSURANCE_TEST_EXPECTED_FINANCER%

echo   insurer=BAJAJ GENERAL INSURANCE LIMITED  addons: ND Cover, Rim Safeguard, RSA

echo.

echo DB mode: form_insurance_view + staging when IDs resolve; nominee_relationship forced to Son, age 45.

echo Financier trace logs: customer_master, form_insurance_view, staging, OCR, built value.

echo Optional: set INSURANCE_TEST_PAUSE_BEFORE_EXIT=0

echo.

python "%~dp0test_insurance_main_form_fill.py"

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% neq 0 echo Exit code %EXITCODE%

pause

exit /b %EXITCODE%
