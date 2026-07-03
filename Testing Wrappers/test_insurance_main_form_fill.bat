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

rem --- Scenario: dealer 100003 Bajaj ND Cover + Rim Safeguard (no RSA) ---
rem To test 100001 (with RSA): set INSURANCE_TEST_DEALER_ID=100001 before python line below.
set INSURANCE_TEST_DEALER_ID=100003
set INSURANCE_TEST_CUSTOMER_ID=70
set INSURANCE_TEST_VEHICLE_ID=94
set INSURANCE_TEST_STAGING_ID=2e7091bc-b3c0-4f5d-a5af-2d2e5759cce0
set INSURANCE_TEST_SUBFOLDER=7878793294_290626
set INSURANCE_TEST_INSURER=BAJAJ GENERAL INSURANCE LIMITED
set INSURANCE_TEST_USE_DB=1

echo Repo: %CD%

echo.

echo Generate Insurance — sale 7878793294_290626

echo   dealer=%INSURANCE_TEST_DEALER_ID%  customer_id=%INSURANCE_TEST_CUSTOMER_ID%  vehicle_id=%INSURANCE_TEST_VEHICLE_ID%

echo   staging_id=%INSURANCE_TEST_STAGING_ID%

echo   VIN=MBLHAW481T9F01047  mobile=7878793294

echo   insurer=BAJAJ GENERAL INSURANCE LIMITED  addon preset: ND Cover, Rim Safeguard (no RSA)

echo.

echo DB mode: form_insurance_view + staging from auto_ai (sales_master.dealer_id must match).

echo OCR folder: ocr_output\%INSURANCE_TEST_DEALER_ID%\%INSURANCE_TEST_SUBFOLDER% (copy from 100001 if missing)

echo If Rim Safeguard wrong: run Testing Wrappers\fix_insurance_test_staging_addon_100003.sql on auto_ai

echo Optional: set INSURANCE_TEST_PAUSE_BEFORE_EXIT=0

echo.

python "%~dp0test_insurance_main_form_fill.py"

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% neq 0 echo Exit code %EXITCODE%

pause

exit /b %EXITCODE%
