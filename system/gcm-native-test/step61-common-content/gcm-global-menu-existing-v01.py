"""Stable shared GCM route; declared dependency names pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-menu-existing-switch-v01-lifecycle-v01-space-v01-lookup-v01.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='4aa336226807ab375e2e4e56a4ee9bbe53323f5c1213006c078ed05cfe1a877a':
    raise ValueError("Shared GCM implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
