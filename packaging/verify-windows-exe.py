"""Reject non-Windows or non-x64 artifacts before distribution."""
import struct
import sys
from pathlib import Path

path = Path(sys.argv[1])
with path.open('rb') as stream:
    if stream.read(2) != b'MZ':
        raise SystemExit('Not a Windows executable')
    stream.seek(0x3c)
    offset = struct.unpack('<I', stream.read(4))[0]
    stream.seek(offset)
    if stream.read(4) != b'PE\0\0':
        raise SystemExit('Invalid PE signature')
    machine = struct.unpack('<H', stream.read(2))[0]
    if machine != 0x8664:
        raise SystemExit(f'Expected AMD64/x64, got {machine:#x}')
print(f'Windows x64 executable verified: {path.name} ({path.stat().st_size:,} bytes)')
