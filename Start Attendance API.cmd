@echo off
setlocal
title Employee Attendance and Analytics API
cd /d "%~dp0"
echo Starting the attendance API. Keep this window open.
echo.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "run_local_system.py" %*
) else (
  echo Setup required. From this folder run:
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo Then run this launcher again.
  pause
  exit /b 1
)
if errorlevel 1 pause
