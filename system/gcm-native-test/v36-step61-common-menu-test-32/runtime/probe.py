"""Read-only emulator registry probe."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
registry = json.loads(
    (ROOT / "adapter-registry.json").read_text()
)

print("===== GCM V3.6 ADAPTER REGISTRY =====")

for entry in registry["emulators"]:
    binary = entry["binary"]
    config = entry["config"]

    executable_found = (
        binary is not None and Path(binary).exists()
    )

    config_found = (
        config is not None and Path(config).is_dir()
    )

    print()
    print(entry["id"], "/", entry["system"])
    print("  Executable:",
          "FOUND" if executable_found else "UNCONFIRMED")
    print("  Config root:",
          "FOUND" if config_found else "UNCONFIRMED")
    print("  Adapter implementation: PENDING")

print()
print("No content installed or modified")
