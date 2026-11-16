"""Shared scanner route with reported per-file identification problems."""
import hashlib
from pathlib import Path
import runpy
path=Path('/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-full-scan-with-problems-v01.py')
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='f4f510f9c45a056545aac402d9f4df4b8fb594b86e3480739df8d4a4eb20be6d':
    raise ValueError("Scanner implementation changed")
if __name__=="__main__":runpy.run_path(str(path),run_name="__main__")
