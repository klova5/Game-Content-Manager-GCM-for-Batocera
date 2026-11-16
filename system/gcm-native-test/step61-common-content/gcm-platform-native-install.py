"""Native PS4 update installation start/status bridge."""
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
    "gcm-platform-native-copy.py":
        "c8ed6cad5b78d9bb16c3b6efc3ddb11b17c778a00038b5e0ded7ca90bbdb04a1",
    "gcm-platform-install-job.py":
        "2464c65d11dedc713b8f2a136cf956021a27f5bf1a431e2253be48a058fe8281",
    "gcm-ps4-install-command.py":
        "819879439d91bc94b2eea32e6d8c0a19cdc47ba91125069e9f7dcf6a578d1515",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Native installation dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def command(action, rom, emulator, kind, token):
    if emulator != "shadps4" or kind != "update":
        raise ValueError("Native installation unavailable for this scope")
    if action not in ("start", "status"):
        raise ValueError("Unsupported installation action")
    job = load("gcm-platform-install-job.py")
    source = Path(job.validate_token(token))
    identity = load("gcm-ps4-base-identity.py").identify(rom)
    managed = (
        Path("/userdata/system/game-content/ps4")
        / identity["application_id"] / "updates" / source.name
    )
    load("gcm-ps4-install-command.py").resolve(rom, managed)
    if action == "start" and source != managed:
        load("gcm-platform-native-copy.py")
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "gcm-platform-native-copy.py"),
             "status", "--rom", str(rom), "--emulator", emulator,
             "--type", kind, "--token", token],
            capture_output=True, text=True, timeout=30)
        fields = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0] == "COPY":
                if parts[1] in fields:
                    raise ValueError("Duplicate managed-copy status field")
                fields[parts[1]] = parts[2]
        if (
            result.returncode != 0
            or fields.get("STATUS") != "READY"
            or fields.get("LOCAL") != str(managed)
        ):
            raise ValueError("Current READY managed copy required before installation")
    return [
        sys.executable, "-B", str(ROOT / "gcm-platform-install-job.py"),
        action, "--rom", str(rom), "--token", str(managed).encode().hex(),
        "--type", "update",
    ]

def run(action, rom, emulator, kind, token):
    result = subprocess.run(
        command(action, rom, emulator, kind, token),
        capture_output=True, text=True, timeout=45)
    lines = [
        line for line in result.stdout.splitlines()
        if line.startswith(("INSTALL\t", "STATUS\t", "ERROR\t"))
    ]
    errors = [line for line in lines if line.startswith("ERROR\t")]
    if result.returncode or errors:
        raise ValueError(
            errors[-1].split("\t", 1)[1] if errors else
            "Installation job command failed")
    if not any(line.startswith("INSTALL\tSTATUS\t") for line in lines):
        raise ValueError("Installation job status missing")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--type", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    print(run(args.action, args.rom, args.emulator, args.type, args.token))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:1200])
        sys.exit(1)
