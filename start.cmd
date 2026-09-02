@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\dev.ps1"
if errorlevel 1 (
  echo.
  echo CiteMind failed to start. See the message above.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:3000"
endlocal
