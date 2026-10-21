@echo off
setlocal
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 pwsh.exe is required. No execution policy will be changed.
  exit /b 1
)
pwsh.exe -NoLogo -NoProfile -File "%~dp0scripts\local-runtime.ps1" -Action Check %*
exit /b %errorlevel%
