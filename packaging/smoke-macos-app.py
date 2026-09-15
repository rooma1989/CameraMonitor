"""Check a frozen macOS application from a clean working directory."""
import subprocess
import sys
import tempfile
from pathlib import Path

app = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='camera-smoke-') as directory:
    marker = Path(directory) / 'result.txt'
    subprocess.run([str(app / 'Contents/MacOS/CameraMonitor'),
                    '--packaging-smoke-test', str(marker)],
                   cwd=directory, check=True, timeout=60)
    if not marker.exists() or marker.read_text() != 'ok':
        raise RuntimeError('Packaged application did not complete startup check')
print('macOS packaged application startup OK')
