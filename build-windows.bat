@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import sysconfig; assert sysconfig.get_platform() == 'win-amd64', 'Python 3.12 x64 required'"
if errorlevel 1 goto python_missing
if not exist .venv-win\Scripts\python.exe (
  py -3.12 -m venv .venv-win
  if errorlevel 1 goto fail
)
.venv-win\Scripts\python.exe -c "import sysconfig; assert sysconfig.get_platform() == 'win-amd64'"
if errorlevel 1 goto fail
.venv-win\Scripts\python.exe -m pip install --disable-pip-version-check -r requirements.txt -r packaging\requirements-build.txt
if errorlevel 1 goto fail
set QT_QPA_PLATFORM=offscreen
.venv-win\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto fail
set QT_QPA_PLATFORM=
.venv-win\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist-win --workpath build-win packaging\CameraMonitor-windows.spec
if errorlevel 1 goto fail
.venv-win\Scripts\python.exe packaging\verify-windows-exe.py dist-win\CameraMonitor.exe
if errorlevel 1 goto fail
.venv-win\Scripts\python.exe packaging\smoke-windows-exe.py dist-win\CameraMonitor.exe
if errorlevel 1 goto fail
echo Build ready: dist-win\CameraMonitor.exe
echo Please test device discovery, playback and saved passwords on Windows 11.
pause
exit /b 0
:python_missing
echo Install Python 3.12 x64 with the Python Launcher, then run this file again.
goto fail
:fail
echo Build failed. See the error above; do not distribute incomplete output.
pause
exit /b 1
