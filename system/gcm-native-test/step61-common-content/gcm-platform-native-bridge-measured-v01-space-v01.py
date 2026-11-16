"""Native menu queries through the shared content service."""
import argparse
import contextlib
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-content-service-measured-v01-space-v01.py":
        "b2c44a43d70a7c7959447a1d0570d58d015d1f82b0f0fe9f933d7b9b660c3d0f",
    "gcm-platform-native-protocol.py":
        "492f11bd426cefbaccbd0ff2245ac2a3515831d39bec7eaf92f55db857736621",
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-ps3-base-identity.py":
        "f8741b41b64ce50fd2d754b88893a7e1227564680e789cf0f0ed90a81971330a",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Native bridge dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def query(rom, emulator, kinds):
    systems = {"shadps4": "ps4", "rpcs3": "ps3"}
    if emulator not in systems:
        raise ValueError("Native query adapter unavailable")
    system = systems[emulator]
    if any(kind not in ("update", "dlc") for kind in kinds):
        raise ValueError("Unsupported content type")
    if system == "ps3" and "dlc" in kinds:
        raise ValueError("PS3 DLC inventory adapter unavailable")
    reader = load("gcm-ps4-base-identity.py" if system == "ps4"
                  else "gcm-ps3-base-identity.py")
    before = reader.identify(rom)
    base = before["metadata_path"] if system == "ps4" else before["base"]
    service = load("gcm-platform-content-service-measured-v01-space-v01.py")
    scope = service.discover([system])["systems"][system]
    if scope["problems"]:
        raise ValueError("Discovery reported problems")
    sources = [item["path"] for item in scope["candidates"]]
    reports = []
    for kind in kinds:
        report = service.candidates(base, sources, system, kind)
        if report["application_id"] not in (None, before["application_id"]):
            raise ValueError("Inventory and selected game disagree")
        report["application_id"] = before["application_id"]
        reports.append(report)
    if reader.identify(rom) != before:
        raise ValueError("Selected game changed during native query")
    return reports

def render(rom, emulator, kind=None):
    protocol = load("gcm-platform-native-protocol.py")
    if kind is not None:
        return protocol.rows(query(rom, emulator, [kind])[0])
    if emulator == "rpcs3":
        report = query(rom, emulator, ["update"])[0]
        lines = protocol.rows(report)
        available = sum(line.split("\t")[1] == "AVAILABLE" for line in lines)
        return [
            f"SUMMARY\t{available} AVAILABLE / {len(lines)-available} DETECTED"
            "\tUNASSESSED"
        ]
    update, dlc = query(rom, emulator, ["update", "dlc"])
    return [protocol.summary(update, dlc)]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True,
                        choices=("shadps4", "rpcs3"))
    parser.add_argument("--type", choices=("update", "dlc"))
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(sys.stderr):
            lines = render(args.rom, args.emulator, args.type)
        for line in lines:
            print(line)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        message = "".join(
            c if ord(c) >= 32 and c != chr(127) else " "
            for c in str(exc)
        )[:240]
        print("ERROR\t" + message)
        return 2

if __name__ == "__main__":
    sys.exit(main())
