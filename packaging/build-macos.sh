#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m PyInstaller --noconfirm --clean packaging/CameraMonitor.spec
codesign --verify --deep --strict 'dist/Camera Monitor.app'
