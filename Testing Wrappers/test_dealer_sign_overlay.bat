@echo off
setlocal EnableExtensions
REM Test headless dealer signature overlay on PDFs in a sale folder
cd /d "%~dp0.."
if not exist "%CD%\backend\app\services\dealer_sign_overlay.py" (
  echo ERROR: backend\app\services\dealer_sign_overlay.py not found. Expected repo layout: My Auto.AI\backend\
  pause
  exit /b 1
)
echo Repo: %CD%
echo Default folder: D:\Saath\Dealer Saathi\Uploaded scans\100001\7296967153_290426
echo Optional: python "%~dp0test_dealer_sign_overlay.py" --folder "YOUR_PATH" --dealer-id 100001 --signature "D:\Saath\100001_sign.jpg"
echo.
python "%~dp0test_dealer_sign_overlay.py" %*
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
