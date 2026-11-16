"""Native PS4 update installation start/status bridge."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-dlc-install-job-v02-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01.py":
        "0ce76b64ff2a716801472c7030fefb054a14db2eec6a53e57aee8b23d394122c",
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-platform-native-copy-measured-v01.py":
        "718a3e02eb6dac2ff6ccc4a6f7b1398e8c349a6bce94d32bf4a106cb03076368",
    "gcm-platform-install-job-v03-measured-v01-menu-evidence-v01.py":
        "43fdae9653c9c1831e41cc90ff65e09759bcb2a6ed836a108cd508f069374128",
    "gcm-ps4-install-command-measured-v01-menu-evidence-v01.py":
        "037b104f84bb12793b8ce67d1e40c6098e4f8d576506cd6144d92f2eba9b3621",
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
    if emulator != "shadps4" or kind not in ("update", "dlc"):
        raise ValueError("Native installation unavailable for this scope")
    if action not in ("start", "status"):
        raise ValueError("Unsupported installation action")
    name = (
        "gcm-platform-install-job-v03-measured-v01-menu-evidence-v01.py" if kind == "update"
        else "gcm-platform-dlc-install-job-v02-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01.py"
    )
    job = load(name)
    job.validate_token(token)
    if action == "start":
        job.checked_preflight(rom, token)
    return [
        sys.executable, "-B", str(ROOT / name),
        action, "--rom", str(rom), "--token", token, "--type", kind,
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
