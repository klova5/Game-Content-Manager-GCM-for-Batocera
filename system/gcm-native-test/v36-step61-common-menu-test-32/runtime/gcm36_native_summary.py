import hashlib
from pathlib import Path
import runpy

bridge = Path("/userdata/system/gcm-native-test/step61-common-content/gcm-platform-native-bridge.py")
if bridge.is_symlink() or hashlib.sha256(bridge.read_bytes()).hexdigest() != "7263d407168d02de3b55a1db9b7aa85a4f0469f658e8cc4fc124fddc8f6b767c":
    raise ValueError("Common native bridge changed")
runpy.run_path(str(bridge), run_name="__main__")
