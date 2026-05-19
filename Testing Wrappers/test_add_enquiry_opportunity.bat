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
echo Optional: set ENQUIRY_TEST_SKIP_CONTACT_FIND=1     (skip Find/Go; position Siebel manually first)
echo Optional: set ENQUIRY_TEST_PLAYWRIGHT_LOG_DIR=path   (default: Testing Wrappers\playwright_dms_logs)
echo Optional: set ENQUIRY_TEST_PLAYWRIGHT_TRACE=1       (Playwright Trace Viewer .zip next to the .txt log)
echo Optional: set ENQUIRY_TEST_POST_LOGIN_WAIT_SEC=15
echo Optional: set ENQUIRY_TEST_PAUSE_BEFORE_EXIT=0
echo Optional: set ENQUIRY_TEST_MOBILE_PHONE=...  ENQUIRY_TEST_CITY=...  (see test_add_enquiry_opportunity.py)
echo.
python "%~dp0test_add_enquiry_opportunity.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
