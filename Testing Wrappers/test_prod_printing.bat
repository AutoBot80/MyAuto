@echo off
setlocal EnableExtensions
REM Double-click launcher: repo root = parent of this folder (My Auto.AI)
cd /d "%~dp0.."
if not exist "%CD%\electron\package.json" (
  echo ERROR: electron\package.json not found. Expected repo layout: My Auto.AI\electron\
  pause
  exit /b 1
)
echo Repo: %CD%
echo Print folder: C:\Users\arya_\OneDrive\Desktop\My Auto.AI\Uploaded scans\100001\9784542030_250426
echo PDF: 9784542030_Insurance_27042026.pdf
echo.
python "%~dp0test_prod_printing.py"
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
