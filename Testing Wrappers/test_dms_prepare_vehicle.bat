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

echo Defaults: Key 7870 / Chassis 19977 / Engine 18800 / Battery 418145

echo Manual login ON by default — type credentials in the browser; automation waits up to 120s.

echo Optional: set VEHICLE_TEST_MANUAL_LOGIN=0   (use cached/env auto-login instead)

echo Optional: set VEHICLE_TEST_POST_LOGIN_WAIT_SEC=20   (extra settle after login; default 5)

echo Optional: set VEHICLE_TEST_PAUSE_BEFORE_EXIT=0

echo.

python "%~dp0test_dms_prepare_vehicle.py"

set EXITCODE=%ERRORLEVEL%

echo.

if %EXITCODE% neq 0 echo Exit code %EXITCODE%

pause

exit /b %EXITCODE%

