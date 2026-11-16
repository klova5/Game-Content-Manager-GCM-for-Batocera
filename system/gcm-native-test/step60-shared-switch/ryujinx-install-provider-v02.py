
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
    copier = load("gcm-switch-managed-copy.py",
        "e6c9140a8086cb30202d831630d0b2219690ffd724735376c333f6b2ac544432")
    integrity = load("ryujinx-installed-integrity-v02.py",
        "ced277370d7f282dba78c596d727601401ff24e91e16ad98f204fe06fb3600e0")
    adapter = load("ryujinx-native-adapter.py",
        "dbf3498d04fb0d38a2c0ed09b5fc34bd6943a7bfec80b8ede2e78351085f1141")
    operations = load("ryujinx-global-backend.py",
        "701fe33d5d7ac6b8195261240bd794e9ea818888d0b34e24d589c681d8618fdb")
    index = load("gcm-switch-managed-index.py",
        "086321dbd71878064044445ada98d7d80f2ae6f482b425e099eca0f146545772")
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
    transaction = load("ryujinx-content-registration.py",
        "656d7b8ddd749424aa5a7cfab8d71c3a0147d8dcf2cf1af33cf5d5577692e7ed")
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
