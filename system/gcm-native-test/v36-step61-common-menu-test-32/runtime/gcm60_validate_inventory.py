#!/usr/bin/env python3
import hashlib
import importlib.util
from pathlib import Path

path = Path("/userdata/system/gcm-native-test/step60-shared-switch/citron-installed-integrity.py")
expected = "987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc"
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
    raise SystemExit("Global installed-inventory validator changed")
spec = importlib.util.spec_from_file_location("global_inventory_validation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
snapshot = module.validate()
print("PASS: Global verified inventory valid; records:", len(snapshot["records"]))
