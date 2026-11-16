
"""Shared verified managed copy for Switch updates and DLC."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEGACY = Path("/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-install-measured-v01-space-v01.py")

def load(path, expected, name):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def dependencies():
    shared = load(LEGACY,
        "5c4dafd5826105920f17f0b5a90b0aefdbee66157ea934f18c25f26b47e0ae0a",
        "shared_copy_engine")
    store = load(ROOT / "gcm-switch-verification-store.py",
        "07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195",
        "shared_proofs")
    engine = load(ROOT / "gcm-switch-content-engine-measured-v01.py",
        "7120a24469daa2e31316235a79989060777f92701628ce3711ae2b67d9b262c4",
        "shared_content")
    return shared, store, engine

def candidate(rom, token, content_type):
    if (not isinstance(token, str) or not 2 <= len(token) <= 8192
            or len(token) % 2
            or any(c not in "0123456789abcdefABCDEF" for c in token)):
        raise ValueError("Invalid content token")
    decoded = bytes.fromhex(token).decode("utf-8")
    if not Path(decoded).is_absolute():
        raise ValueError("Absolute content path required")
    source = Path(decoded).resolve(strict=True)
    shared, store, engine = dependencies()
    proof = store.lookup(rom, source, content_type)
    if proof is None:
        raise ValueError("Fresh shared verification required")
    discovered = engine.candidates(rom, content_type)
    matches = [
        group for group in discovered["groups"]
        if group["application_id"] == proof["application_id"]
        and group["title_id"] == proof["title_id"]
        and group["internal_version"] == proof["internal_version"]
        and any(Path(item["path"]).resolve(strict=True) == source
                for item in group["sources"])
    ]
    if len(matches) != 1:
        raise ValueError("Verified source is not an exact indexed game candidate")
    folder = shared.ROOT / proof["application_id"] / (
        "updates" if content_type == "update" else "dlc")
    destination = folder / source.name
    for path in (destination, *destination.parents):
        if path.is_symlink():
            raise ValueError("Symlink in managed destination")
    if source.is_relative_to(shared.ROOT.resolve()):
        raise ValueError("Select an external source for managed copying")
    return shared, store, proof, source, destination

def preview(rom, token, content_type):
    _, _, proof, source, destination = candidate(rom, token, content_type)
    return {
        "status": "PREVIEW", "application_id": proof["application_id"],
        "title_id": proof["title_id"], "content_type": content_type,
        "internal_version": proof["internal_version"],
        "source": str(source), "destination": str(destination),
        "destination_exists": destination.exists(),
        "registration_performed": False,
    }

def copy(rom, token, content_type):
    shared, store, proof, source, destination = candidate(rom, token, content_type)
    state = ROOT / "state"
    if state.is_symlink():
        raise ValueError("Unsafe shared state directory")
    state.mkdir(mode=0o700, exist_ok=True)
    lockpath = state / "managed-copy.lock"
    if lockpath.is_symlink():
        raise ValueError("Unsafe managed-copy lock")
    descriptor = os.open(lockpath, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if store.lookup(rom, source, content_type) != proof:
            raise ValueError("Proof changed before copying")
        for path in (destination, *destination.parents):
            if path.is_symlink():
                raise ValueError("Managed path changed")
        with tempfile.TemporaryDirectory(prefix="shared-copy-", dir=ROOT) as temporary:
            digest, copied = shared.copy_verified(
                {"source": str(source), "target": destination}, Path(temporary))
        if (digest != proof["sha256"]
                or store.lookup(rom, source, content_type) != proof):
            raise ValueError("Copied content no longer matches verification proof")
        before = shared.source_id(destination)
        if shared.sha(destination) != digest or shared.source_id(destination) != before:
            raise ValueError("Managed content changed during final check")
        return {
            "state": "READY", "application_id": proof["application_id"],
            "title_id": proof["title_id"], "content_type": content_type,
            "internal_version": proof["internal_version"],
            "source": str(source), "destination": str(destination),
            "sha256": digest, "managed_stat_identity": before,
            "copied": copied, "source_preserved": True,
            "registration_performed": False,
        }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "copy"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.action == "copy" and not args.confirm:
        raise ValueError("Explicit copy confirmation required")
    with contextlib.redirect_stdout(sys.stderr):
        result = (copy if args.action == "copy" else preview)(
            args.rom, args.token, args.type)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        sys.exit(1)
