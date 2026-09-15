@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)
.venv\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m camera_monitor.app
if errorlevel 1 goto fail
exit /b 0
:fail
pause
exit /b 1
