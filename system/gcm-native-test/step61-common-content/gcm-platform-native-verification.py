"""Resolve frontend game paths for shared background verification."""
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
    "gcm-platform-content-service.py":
        "afc5a18198527458b2ed8d991841b179c24055ed0bd1518616ad2bcadc7c195e",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Verification bridge dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def command(action, rom, emulator, kind, tokens=None):
    if emulator != "shadps4" or kind not in ("update", "dlc"):
        raise ValueError("Background payload verification unavailable for this scope")
    if action not in ("start", "status"):
        raise ValueError("Unsupported verification action")
    if action == "start":
        selected = tokens.split(",") if isinstance(tokens, str) else []
        if not 1 <= len(selected) <= 32 or len(set(selected)) != len(selected):
            raise ValueError("Select between one and 32 unique candidates")
        for token in selected:
            if (
                not token or len(token) > 8192 or len(token) % 2
                or any(c not in "0123456789abcdef" for c in token)
            ):
                raise ValueError("Invalid candidate token")
            try:
                source = bytes.fromhex(token).decode("utf-8")
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Invalid candidate path encoding") from exc
            if not Path(source).is_absolute() or chr(0) in source:
                raise ValueError("Absolute candidate source required")
    elif tokens is not None:
        raise ValueError("Status does not accept candidate tokens")

    identity = load("gcm-ps4-base-identity.py").identify(rom)
    batch = load("gcm-platform-content-service.py").batch_component()
    result = [
        sys.executable, "-B", str(batch), action,
        "--system", "ps4", "--rom", identity["metadata_path"],
        "--type", kind,
    ]
    if action == "start":
        result += ["--tokens", tokens]
    return result

def run(action, rom, emulator, kind, tokens=None):
    args = command(action, rom, emulator, kind, tokens)
    child = subprocess.run(
        args, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=30)
    lines = [
        line for line in child.stdout.splitlines()
        if line.startswith(("BATCH\t", "STATUS\t", "ERROR\t"))
    ]
    if child.returncode or any(line.startswith("ERROR\t") for line in lines):
        detail = next(
            (line[6:] for line in lines if line.startswith("ERROR\t")),
            "Background verification command failed")
        raise ValueError(detail)
    if not any(line.startswith("BATCH\tSTATUS\t") for line in lines):
        raise ValueError("Missing verification batch status")
    return lines

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--type", required=True, choices=("update", "dlc"))
    parser.add_argument("--tokens")
    args = parser.parse_args()
    try:
        for line in run(args.action, args.rom, args.emulator,
                        args.type, args.tokens):
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
