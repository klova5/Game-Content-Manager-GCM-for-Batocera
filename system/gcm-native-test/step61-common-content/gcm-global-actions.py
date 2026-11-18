"""Stable shared GCM route; declared dependency names pinned."""
import hashlib
from pathlib import Path
import runpy
path=Path(__file__).resolve().parent / 'gcm-global-content-action-job-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py'
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='22c90f7124050e0f5ff02ade9c992191a5f3d927facf2d7b10b47641da43fc1b':
    raise ValueError("Shared GCM implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
