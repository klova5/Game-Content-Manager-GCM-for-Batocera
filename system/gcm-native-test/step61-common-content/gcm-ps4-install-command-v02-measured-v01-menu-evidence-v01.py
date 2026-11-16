"""PS4 update installation command with automatic isolated preparation."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-ps4-version-install-measured-v01-menu-evidence-v01.py":
        "112adcf74551371c5435a2a19265afbb4313dea5d84c4f0f4368662ef5d879f8",
    "gcm-ps4-extraction-rehearsal-measured-v01.py":
        "5c035ee3c0a9db17e27cf7d35d54a130d2391a07c0bc5587646f584d3bff5b8e",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Installation command dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def resolve(rom, source):
    identity = load("gcm-ps4-base-identity.py").identify(rom)
    coordinator = load("gcm-ps4-version-install-measured-v01-menu-evidence-v01.py")
    transaction = coordinator.load("gcm-ps4-file-transaction-v02.py")
    source = transaction.safe(Path(source).absolute()).resolve(strict=True)
    if (
        not source.is_file()
        or source.parent != Path("/userdata/system/game-content/ps4")
            / identity["application_id"] / "updates"
    ):
        raise ValueError("Managed game-specific update package required")
    base = Path(identity["metadata_path"])
    if transaction.checked_target(base.parent.parent).name != identity["application_id"]:
        raise ValueError("Launcher and installation target disagree")
    return coordinator, base, source

def prepare(source):
    load("gcm-ps4-extraction-rehearsal-measured-v01.py")
    work = ROOT / ("step61-ps4-extraction-install-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    script = """
import hashlib
import importlib.util
import sys
from pathlib import Path
path, source, work = map(Path, sys.argv[1:])
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != "5c035ee3c0a9db17e27cf7d35d54a130d2391a07c0bc5587646f584d3bff5b8e":
    raise ValueError("Extraction adapter changed")
spec = importlib.util.spec_from_file_location("isolated_install_preparation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.rehearse(source, work)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script,
         str(ROOT / "gcm-ps4-extraction-rehearsal-measured-v01.py"), str(source), str(work)],
        executable=sys.executable,
        capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise ValueError("Isolated installation preparation failed: "
                         + (result.stderr or result.stdout)[-1600:])
    report = json.loads((work / "report.json").read_text())
    if report["status"] != "ISOLATED_EXTRACTION_FILES_VERIFIED":
        raise ValueError("Prepared extraction did not verify")
    return Path(report["output"])

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "install"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--prepared")
    args = parser.parse_args()
    coordinator, base, source = resolve(args.rom, args.source)
    if args.action == "preview":
        if not args.prepared:
            raise ValueError("Preview requires an existing prepared extraction")
        result = coordinator.preview(base, source, args.prepared)
    else:
        coordinator.idle()
        transaction = coordinator.load("gcm-ps4-file-transaction-v02.py")
        coordinator.configuration(transaction)
        proof = coordinator.load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py").verify_game_content(
            base, source, "ps4")
        if proof["content_type"] != "update":
            raise ValueError("Game-matching update required")
        prepared = args.prepared if args.prepared else prepare(source)
        result = coordinator.install(base, source, prepared)
    print(json.dumps(result))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ")[:1800])
        sys.exit(1)
