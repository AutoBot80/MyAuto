@echo off
setlocal EnableExtensions
REM Print / Queue RTO test — env set here (no manual set before double-click)
set "SAATHI_BASE_DIR=D:\Saathi"
set "PRINT_RTO_JWT=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzaGFzaGFuayIsImRlYWxlcl9pZCI6MTAwMDAxLCJuYW1lIjoiU2hhc2hhbmsgQXJ5YSIsInJvbGVzIjpbIk9XTkVSIl0sImFkbWluIjp0cnVlLCJ0aWxlX3BvcyI6dHJ1ZSwidGlsZV9ydG8iOnRydWUsInRpbGVfc2VydmljZSI6dHJ1ZSwidGlsZV9kZWFsZXIiOnRydWUsImlhdCI6MTc3OTUxMTAwMCwiZXhwIjoxNzc5NTM5ODAwfQ.umpRFHb3endbQ-86BB4kleiJOmzY-igG3mLEoa5jrhg"
REM Optional: one-line token in test_print_queue_rto.jwt.local (gitignored) overrides empty placeholder above

cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
if not exist "%CD%\backend\app" (
  echo ERROR: backend\app not found. Expected repo layout: My Auto.AI\backend\app
  pause
  exit /b 1
)
if not exist "%CD%\electron\sidecar\job_runner.py" (
  echo ERROR: electron\sidecar\job_runner.py not found.
  pause
  exit /b 1
)
echo Repo: %CD%
echo SAATHI_BASE_DIR=%SAATHI_BASE_DIR%
echo Default sale: %SAATHI_BASE_DIR%\Uploaded scans\100001\9057397169_210526
echo Pull/push: skipped by default. Use --pull or --push to sync with server.
echo JWT: edit PRINT_RTO_JWT= in this bat or use test_print_queue_rto.jwt.local
echo Prints 3 docs after gate pass: auto-clicks Print and closes PDF (use --skip-print to skip)
echo Optional: --silent-print   ^|   --skip-queue   ^|   --pull   ^|   --push
echo.
python "%~dp0test_print_queue_rto.py" %*
set EXITCODE=%ERRORLEVEL%
echo.
if %EXITCODE% neq 0 echo Exit code %EXITCODE%
pause
exit /b %EXITCODE%
