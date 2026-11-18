
"""Ryujinx installation preparation using the common Switch engine."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Ryujinx install dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def dependencies():
    copier = load("gcm-switch-managed-copy-measured-v01-space-v01.py",
        "19fa1f5ab2fa2dd06dce95250095994c7d9819e23802d97f5df6c15a0b0e001e")
    integrity = load("ryujinx-installed-integrity-v02-measured-v01.py",
        "144796758dcdd3934b2b7a8655509c676f147d0ba6891c83f8591977225ce694")
    adapter = load("ryujinx-native-adapter-measured-v01.py",
        "155db518541f0196773ef338995f9864c1225f26a813a71ea3459edabb3a0301")
    operations = load("ryujinx-global-backend-measured-v01-switch-routes-v01-space-v01.py",
        "ccdce1cd8a8f0a6918c01534f21a1a565fe3956ddc7604eb85472ba7ec71de90")
    index = load("gcm-switch-managed-index-measured-v01-space-v01.py",
        "64322683ace290d5be17a92adcd5b25c7047a496b15244336172ae914131cd76")
    return copier, integrity, adapter, operations, index

def prepare(rom, token, content_type):
    copier, integrity, adapter, _, _ = dependencies()
    legacy, store, proof, source, managed = copier.candidate(rom, token, content_type)
    game = integrity.validate(rom)
    if game["native_snapshot"]["application_id"] != proof["application_id"]:
        raise ValueError("Native registration and source game disagree")
    records = [item for item in game["records"]
               if item["content_type"] == content_type]
    matches = [
        item for item in records
        if item["title_id"] == proof["title_id"]
        and item["internal_version"] == proof["internal_version"]
        and item["sha256"] == proof["sha256"]
    ]
    local_matches = [
        item for item in matches
        if Path(item["registered_path"]).resolve(strict=True).is_relative_to(
            legacy.ROOT.resolve())
    ]
    migration_enabled = None
    if not local_matches and content_type == "dlc":
        same_title = [item for item in records
                      if item["title_id"] == proof["title_id"]]
        if any(item not in matches for item in same_title):
            raise ValueError("Different registered DLC revision requires explicit replacement planning")
        if any(item["content_type"] == "dlc" for item in game["problems"]):
            raise ValueError("Resolve unverified DLC registrations before registration")
        if matches:
            matching_paths = {
                str(Path(item["registered_path"]).resolve(strict=True))
                for item in matches
            }
            entries = [
                entry for entry in game["native_snapshot"]["configuration"]["dlc"]["document"]
                if str(Path(entry["path"]).resolve(strict=True)) in matching_paths
            ]
            flags = {
                bool(item.get("is_enabled", True))
                for entry in entries for item in entry.get("dlc_nca_list", [])
            }
            if len(entries) != 1 or len(flags) != 1:
                raise ValueError("Ambiguous DLC migration state")
            migration_enabled = next(iter(flags))
        elif proof.get("verified_primary_records") != 1:
            raise ValueError("Native registration requires an explicit multi-NCA DLC mapping")
    if store.lookup(rom, source, content_type) != proof:
        raise ValueError("Source proof changed during install preparation")
    if integrity.validate(rom) != game:
        raise ValueError("Ryujinx state changed during install preparation")
    return {
        "proof": proof, "source": source, "managed": managed,
        "game": game, "local_matches": local_matches,
        "matching_registrations": matches,
        "migration_enabled": migration_enabled,
    }

def preview(rom, token, content_type):
    prepared = prepare(rom, token, content_type)
    proof = prepared["proof"]
    return {
        "status": "PREVIEW",
        "application_id": proof["application_id"],
        "title_id": proof["title_id"],
        "content_type": content_type,
        "internal_version": proof["internal_version"],
        "source": str(prepared["source"]),
        "managed_path": str(prepared["managed"]),
        "registration_action": (
            "REUSE_VERIFIED_MANAGED_REGISTRATION" if prepared["local_matches"]
            else "COPY_AND_REGISTER_MANAGED"),
        "preserve_existing_dlc_enabled": prepared["migration_enabled"],
        "active_preserved": True,
        "source_preserved": True,
        "changes_performed": False,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview",))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = preview(args.rom, args.token, args.type)
    print(json.dumps(result, indent=2))


def install(rom, token, content_type, confirmed=False):
    if not confirmed:
        raise PermissionError("Installation requires explicit confirmation")
    import fcntl
    import json
    import os

    copier, integrity, adapter, operations, index = dependencies()
    transaction = load("ryujinx-content-registration-measured-v01-switch-routes-v01-space-v01.py",
        "38f2052747df79bb15024efac2747c680313fb456d2d948a9e6e66296dfc5406")
    state = ROOT / "state"
    lockpath = state / "ryujinx-install.lock"
    if state.is_symlink() or lockpath.is_symlink():
        raise ValueError("Unsafe installation lock")
    state.mkdir(mode=0o700, exist_ok=True)

    with lockpath.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        operations.ensure_closed()
        initial = prepare(rom, token, content_type)
        legacy, store, engine = copier.dependencies()
        print("Fresh shared content verification...", flush=True)
        proof = engine.verify(rom, initial["source"], content_type)
        if proof.get("status") != "PASS":
            raise ValueError("Shared verification did not pass")
        for key in ("application_id", "title_id", "content_type",
                    "internal_version", "sha256"):
            if proof.get(key) != initial["proof"].get(key):
                raise ValueError("Source identity changed before installation")
        store.record(proof)
        prepared = prepare(rom, token, content_type)
        if prepared["game"] != initial["game"]:
            raise ValueError("Native state changed during fresh verification")

        def validate_source():
            if store.lookup(rom, prepared["source"], content_type) != proof:
                raise ValueError("Verified source changed during installation")

        validate_source()
        if prepared["local_matches"]:
            operations.ensure_closed()
            if integrity.validate(rom) != prepared["game"]:
                raise ValueError("Registered state changed before reuse")
            validate_source()
            return {
                "state": "INSTALLED",
                "local": prepared["local_matches"][0]["registered_path"],
                "registration_action": "REUSE_VERIFIED_MANAGED_REGISTRATION",
                "active_preserved": True, "source_preserved": True,
                "copied": False,
            }

        print("Checking shared managed copy...", flush=True)
        copied = copier.copy(rom, token, content_type)
        validate_source()
        if integrity.validate(rom) != prepared["game"]:
            raise ValueError("Native state changed during managed copying")
        index.record(copied, proof)
        validate_source()

        native, application, canonical_rom, _ = adapter.controller(rom)
        if application != proof["application_id"]:
            raise ValueError("Native game identity differs from verified package")
        if integrity.validate(rom) != prepared["game"]:
            raise ValueError("Native state changed before registration")

        snapshot = json.loads(integrity.STORE.read_text())
        roms = sorted(set(snapshot["games"]) | {str(canonical_rom)})

        def refresh_inventory():
            # refresh() replaces the combined snapshot: retain every known game.
            integrity.refresh(roms)

        result = transaction.register(
            str(canonical_rom), Path(copied["destination"]), proof,
            native=native,
            read_game=lambda: integrity.validate(str(canonical_rom)),
            refresh_inventory=refresh_inventory,
            validate_source=validate_source)
        return {
            **result,
            "registration_action": "COPY_AND_REGISTER_MANAGED",
            "application_id": application,
            "title_id": proof["title_id"],
            "content_type": content_type,
            "internal_version": proof["internal_version"],
            "sha256": proof["sha256"],
            "managed_path": copied["destination"],
            "copied": copied["copied"],
            "source_preserved": True,
        }

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:400])
        sys.exit(1)
