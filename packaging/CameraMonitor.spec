import re
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root=Path(SPECPATH).parent
# 版本号只有 camera_monitor/__init__.py 一处，避免发版时漏改
version=re.search(r"__version__\s*=\s*'([^']+)'",
                  (root/'camera_monitor'/'__init__.py').read_text(encoding='utf-8')).group(1)
metadata=[]
for package in ('keyring','jaraco.classes','jaraco.context','jaraco.functools'):
    metadata += copy_metadata(package)
a=Analysis([str(root/'packaging'/'launcher.py')],pathex=[str(root)],
    binaries=[],datas=metadata+[(str(root/"camera_monitor"/"assets"/"app-icon.png"),"camera_monitor/assets")],hiddenimports=['keyring.backends.macOS'],
    hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['tkinter','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick','keyring.backends.Windows'],
    noarchive=False,optimize=0)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='CameraMonitor',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
    console=False,disable_windowed_traceback=False,argv_emulation=False,
    target_arch='arm64',codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='CameraMonitor')
app=BUNDLE(coll,name='Camera Monitor.app',bundle_identifier='com.cameramonitor.desktop',
    icon=str(root/'camera_monitor'/'assets'/'app-icon.icns'),version=version,info_plist={
        'CFBundleDisplayName':'Camera Monitor',
        'CFBundleShortVersionString':version,
        'LSMinimumSystemVersion':'14.0',
        'NSHighResolutionCapable':True,
        'NSLocalNetworkUsageDescription':'搜索并连接同一局域网中的摄像头，显示实时监控画面。',
        'LSApplicationCategoryType':'public.app-category.utilities',
    })
