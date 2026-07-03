@echo off
setlocal EnableExtensions
cd /d "%~dp0.."
set "PYTHONPATH=%CD%\backend"
python "%~dp0test_dms_add_enquiry.py"
pause
