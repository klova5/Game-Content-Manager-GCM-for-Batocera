
"""Global Citron installation preparation using the shared Switch copy engine."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")
PUBLICATION_ROOT = Path("/userdata/system/game-content/citron-registered")

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Install dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def dependencies():
    copier = load(ROOT / "gcm-switch-managed-copy-measured-v01.py",
        "3fe602bcf4d94747ae4b2fdf66fe565cf2119d89e8e6092042a8c3e8d71de7fb")
    integrity = load(ROOT / "citron-installed-integrity.py",
        "987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc")
    planner = load(ROOT / "citron-registration-plan.py",
        "e0e22248eb3d8289158a91996b19cd45f1dd7206bc45d6b0cca76ce069fa7965")
    native = load(ROOT / "citron-installed-rows-measured-v01.py",
        "08ad3056382bfb3d6cc0c511a0bed39e018252b8443a037fd9fad69a3a8317ee")
    packages = load(COMPONENTS / "citron-package-verify-v03.py",
        "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388")
    return copier, integrity, planner, native, packages

def matching_registered(proof, source, snapshot, packages):
    parser, reader = packages.dependencies()
    source = Path(source)
    before = parser.stat_identity(source.stat())
    identity = reader.identify(source)
    if any(identity.get(key) != proof.get(key) for key in (
        "application_id", "title_id", "content_type", "internal_version"
    )):
        raise ValueError("Source metadata no longer matches shared proof")
    with source.open("rb") as stream:
        entries = parser.read_pfs0(stream, source.stat().st_size)
        name = identity["meta_nca_name"]
        metadata_hash = parser.sha256_member(stream, entries[name], name)
    if parser.stat_identity(source.stat()) != before:
        raise ValueError("Source changed during registration reuse check")
    same_version = [
        item for item in snapshot["records"]
        if all(item.get(key) == proof.get(key) for key in (
            "application_id", "title_id", "content_type", "internal_version"))
    ]
    matched = []
    for item in same_version:
        if (item.get("primary_content_hashes_verified") is not True
            or item.get("present_content_hashes_verified") is not True):
            raise ValueError("Registered content lacks integrity proof")
        if item["origin"] == "External":
            equivalent = item.get("sha256") == proof["sha256"]
        else:
            equivalent = item.get("metadata_sha256") == metadata_hash
        if not equivalent:
            raise ValueError("Registered version has different verified content; review required")
        matched.append(item)
    return matched

def prepare(rom, token, content_type):
    before_config = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    copier, integrity, planner, native, packages = dependencies()
    _, store, proof, source, managed = copier.candidate(rom, token, content_type)
    snapshot = integrity.validate()
    matches = matching_registered(proof, source, snapshot, packages)
    app = proof["application_id"]
    folder = "updates" if content_type == "update" else "dlc"
    destination = PUBLICATION_ROOT / app / folder / managed.name
    plan = None
    if not matches:
        roots = [Path(item["path"]) for item in snapshot["inventory"]["roots"]
                 if item["origin"] == "External"]
        if not any(destination.is_relative_to(path) for path in roots):
            raise ValueError("Publication root is not configured in Citron")
        candidate = {**proof, "origin": "External", "path": str(destination)}
        records = [item for item in snapshot["records"]
                   if item["application_id"] == app]
        plan = planner.plan(
            records, candidate, native.disabled_keys(app),
            native.disabled_keys(f"{int(app, 16) + 0x800:016X}"),
            inventory_complete=True)
    if (store.lookup(rom, source, content_type) != proof
        or integrity.validate() != snapshot
        or hashlib.sha256(CONFIG.read_bytes()).hexdigest() != before_config):
        raise ValueError("Source, configuration or inventory changed during preparation")
    return {
        "proof": proof, "source": source, "managed": managed,
        "destination": destination, "snapshot": snapshot,
        "matches": matches, "plan": plan,
        "configuration_sha256": before_config,
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
            "REUSE_VERIFIED_REGISTERED_CONTENT" if prepared["matches"]
            else "PUBLISH_MANAGED_DISABLED"),
        "registered_origins": sorted({
            item["origin"] for item in prepared["matches"]}),
        "active_preserved": True,
        "source_preserved": True,
        "changes_performed": False,
    }


def install(rom, token, content_type, *, confirm=False):
    if confirm is not True:
        raise ValueError("Explicit confirmation required")
    import fcntl
    import os

    writer = load(COMPONENTS / "citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf")
    transaction = load(ROOT / "citron-content-registration.py",
        "c95291226a656fe60862a1920b90b0fd530192a5376a4ec1b8f28d72795c1994")
    catalog = load(ROOT / "citron-content-catalog.py",
        "6dec4983cb4c7f6707d4604db6f11c5cca01d0d1a9e2f20169e5df84d1e79cfa")
    state = ROOT / "state"
    lockpath = state / "citron-install.lock"
    if state.is_symlink() or lockpath.is_symlink():
        raise ValueError("Unsafe install lock")
    state.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(lockpath, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        initial = prepare(rom, token, content_type)
        copier, integrity, _, _, packages = dependencies()
        _, store, engine = copier.dependencies()

        # The actual common verifier supplies fresh proof for every game/type.
        fresh = engine.verify(rom, initial["source"], content_type)
        store.record(fresh)
        prepared = prepare(rom, token, content_type)
        if prepared["configuration_sha256"] != initial["configuration_sha256"]:
            raise ValueError("Configuration changed during fresh verification")
        if any(prepared["proof"].get(key) != initial["proof"].get(key)
               for key in ("application_id", "title_id", "content_type",
                           "internal_version", "sha256")):
            raise ValueError("Verified candidate changed during installation")
        writer.ensure_closed()

        if prepared["matches"]:
            if integrity.validate() != prepared["snapshot"]:
                raise ValueError("Registered content changed before reuse")
            if hashlib.sha256(CONFIG.read_bytes()).hexdigest() != initial["configuration_sha256"]:
                raise ValueError("Configuration changed before reuse")
            return {
                "state": "INSTALLED",
                "registration_action": "REUSE_VERIFIED_REGISTERED_CONTENT",
                "registered_paths": [item["path"] for item in prepared["matches"]],
                "application_id": fresh["application_id"],
                "title_id": fresh["title_id"],
                "content_type": content_type,
                "internal_version": fresh["internal_version"],
                "sha256": fresh["sha256"],
                "active_preserved": True,
                "source_preserved": True,
                "copied": False,
            }

        copied = copier.copy(rom, token, content_type)
        managed = Path(copied["destination"])
        verified_managed = packages.verify(
            managed, fresh["application_id"], content_type)
        if any(verified_managed.get(key) != fresh.get(key)
               for key in ("application_id", "title_id", "content_type",
                           "internal_version", "sha256")):
            raise ValueError("Managed package differs from fresh shared verification")
        if store.lookup(rom, prepared["source"], content_type) != prepared["proof"]:
            raise ValueError("Original source changed before registration")
        if integrity.validate() != prepared["snapshot"]:
            raise ValueError("Registered inventory changed before registration")

        def refresh():
            catalog.refresh()
            integrity.refresh()
            # During rollback the new publication has already been removed.
            if (prepared["destination"].exists()
                and store.lookup(rom, prepared["source"], content_type)
                    != prepared["proof"]):
                raise ValueError("Original source changed during registration")

        result = transaction.register(
            managed, verified_managed, initial["configuration_sha256"],
            read_inventory=integrity.validate,
            refresh_inventory=refresh)
        return {
            **result,
            "registration_action": "PUBLISH_MANAGED_DISABLED",
            "application_id": fresh["application_id"],
            "title_id": fresh["title_id"],
            "content_type": content_type,
            "internal_version": fresh["internal_version"],
            "managed_path": str(managed),
            "sha256": fresh["sha256"],
            "source_preserved": True,
            "copied": copied["copied"],
        }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "install"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        if args.action == "preview":
            result = preview(args.rom, args.token, args.type)
        else:
            result = install(args.rom, args.token, args.type,
                             confirm=args.confirm)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:400])
        sys.exit(1)
