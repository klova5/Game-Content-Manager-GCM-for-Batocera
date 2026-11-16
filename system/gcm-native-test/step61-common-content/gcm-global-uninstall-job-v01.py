"""Stable shared action route; immutable implementation pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-uninstall-job-v03.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='e23a72312ecf0cd8aaf0841bb0577a0d86aacee88b3949cb28ee63b22691c85d':
    raise ValueError("Shared action implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
