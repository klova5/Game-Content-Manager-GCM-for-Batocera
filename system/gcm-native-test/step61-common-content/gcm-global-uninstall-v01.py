"""Stable shared action route; immutable implementation pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-uninstall-v03.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='85f51a5d41f8499407ce58cfc62e2dcacf6d7faab9b9e837359dc1216e3875bc':
    raise ValueError("Shared action implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
