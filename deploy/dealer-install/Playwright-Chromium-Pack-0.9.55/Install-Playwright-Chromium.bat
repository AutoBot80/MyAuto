@echo off
setlocal
cd /d "%~dp0"
echo.
echo Dealer Saathi 0.9.55 - Playwright Chromium pre-install
echo Target data root: D:\Saathi  (edit this .bat or use the .ps1 -SaathiDataRoot to change)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Playwright-Chromium.ps1" -SaathiDataRoot "D:\Saathi"
if errorlevel 1 (
  echo.
  echo Install failed. Check D:\Saathi\logs\sidecar.log if it exists.
  pause
  exit /b 1
)
echo.
echo Success. Run Dealer Saathi Setup 0.9.55.exe next.
pause
exit /b 0
