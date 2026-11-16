"""Stable shared GCM route; declared dependency names pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-menu-existing-switch-v01-lifecycle-v01-space-v01-lookup-v01.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='adfe34d0c0fd7c55b7a5c0c63b306c12f55607d3a1d28dd5ce33cf185641f97e':
    raise ValueError("Shared GCM implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
