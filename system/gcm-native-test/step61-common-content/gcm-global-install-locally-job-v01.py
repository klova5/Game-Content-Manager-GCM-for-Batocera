"""Stable shared action route; immutable implementation pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-install-locally-job-v02.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='b31210c436b32db9ed365c89b930fe5a7ac6fcacf17cefc3bd515569ebaface7':
    raise ValueError("Shared action implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
