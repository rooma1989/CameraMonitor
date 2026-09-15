"""Build on Windows with CPython 3.12 x64; produces a single portable EXE."""
import sys
import struct
import sysconfig
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

if sys.platform != 'win32' or struct.calcsize('P') != 8 or sysconfig.get_platform() != 'win-amd64':
    raise SystemExit('Build requires Windows and x64 Python (not ARM64).')

root = Path(SPECPATH).parent
metadata = []
for package in ('keyring', 'jaraco.classes', 'jaraco.context', 'jaraco.functools'):
    metadata += copy_metadata(package)
a = Analysis(
    [str(root / 'packaging' / 'launcher.py')], pathex=[str(root)],
    binaries=[], datas=metadata,
    hiddenimports=['keyring.backends.Windows', 'win32ctypes.pywin32.win32cred'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'keyring.backends.macOS', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='CameraMonitor', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    uac_admin=False,
)
