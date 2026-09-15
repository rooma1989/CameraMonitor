"""Launch the built EXE from a different working directory and require success."""
from pathlib import Path
import subprocess
import sys
import tempfile

exe = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='camera-monitor-smoke-') as folder:
    result = Path(folder) / 'result.txt'
    subprocess.run([str(exe), '--packaging-smoke-test', str(result)],
                   cwd=folder, check=True, timeout=120)
    if not result.exists() or result.read_text(encoding='utf-8') != 'ok':
        raise SystemExit('Packaged app did not complete its startup check')
print('Packaged startup, H.264 decoder and Windows vault backend passed')
