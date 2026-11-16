"""Frontend game-path bridge for copy-only background jobs."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-platform-copy-job-measured-v01.py":
        "f8eee2b8cbc629c1cf7ec1fd89f6aed4cc9124e269722fffa54a86533b791dd8",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Native copy dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def command(action, rom, emulator, kind, token):
    if emulator != "shadps4" or kind not in ("update", "dlc"):
        raise ValueError("Managed-copy adapter unavailable for this scope")
    if action not in ("start", "status"):
        raise ValueError("Unsupported copy action")
    job = load("gcm-platform-copy-job-measured-v01.py")
    job.validate_token(token)
    identity = load("gcm-ps4-base-identity.py").identify(rom)
    return [
        sys.executable, "-B", str(ROOT / "gcm-platform-copy-job-measured-v01.py"),
        action, "--rom", identity["metadata_path"],
        "--type", kind, "--token", token,
    ]

def run(action, rom, emulator, kind, token):
    child = subprocess.run(
        command(action, rom, emulator, kind, token),
        stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=30)
    lines = [
        line for line in child.stdout.splitlines()
        if line.startswith(("COPY\t", "STATUS\t", "ERROR\t"))
    ]
    if child.returncode or any(line.startswith("ERROR\t") for line in lines):
        detail = next(
            (line[6:] for line in lines if line.startswith("ERROR\t")),
            "Managed-copy command failed")
        raise ValueError(detail)
    if not any(line.startswith("COPY\tSTATUS\t") for line in lines):
        raise ValueError("Missing managed-copy job status")
    return lines

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--type", required=True, choices=("update", "dlc"))
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    try:
        for line in run(args.action, args.rom, args.emulator,
                        args.type, args.token):
            print(line)
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        message = "".join(
            c if ord(c) >= 32 and c != chr(127) else " "
            for c in str(exc)
        )[:240]
        print("ERROR\t" + message)
        return 2

if __name__ == "__main__":
    sys.exit(main())
