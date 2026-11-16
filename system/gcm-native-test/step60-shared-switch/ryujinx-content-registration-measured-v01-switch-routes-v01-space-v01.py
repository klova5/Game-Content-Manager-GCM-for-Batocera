
"""Guard existing Ryujinx registration operations with backups and post-checks."""
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Registration dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def normalized(document, kind):
    document = copy.deepcopy(document)
    if kind == "dlc":
        for entry in document:
            for item in entry.get("dlc_nca_list", []):
                item.setdefault("is_enabled", True)
    return document

def register(rom, managed, proof, *, native, read_game,
             refresh_inventory, validate_source):
    if not all(callable(item) for item in
               (read_game, refresh_inventory, validate_source)):
        raise ValueError("Inventory and source validation callbacks required")
    planner = load("ryujinx-registration-plan.py",
        "282d0db06087279b264bac6cf9a2a1ae28b1719b22fb64f175d0aa2fa33dbcd7")
    operations = load("ryujinx-global-backend-measured-v01-switch-routes-v01-space-v01.py",
        "1156b3a49ae5b1aecaff48ec29626258c24af43de7e2de8bf6fbeec2cceb9980")
    adapter = load("ryujinx-native-adapter-measured-v01.py",
        "155db518541f0196773ef338995f9864c1225f26a813a71ea3459edabb3a0301")
    _, _, writer = operations.dependencies()
    managed = Path(managed)
    if managed.is_symlink() or not managed.is_file():
        raise ValueError("Regular managed package required")
    before_file = writer.source_id(managed)
    if (managed.stat().st_size != proof.get("source_size")
        or writer.sha(managed) != proof.get("sha256")
        or writer.source_id(managed) != before_file):
        raise ValueError("Managed package differs from verified content")
    app = proof["application_id"]
    if native.rom_title_id(rom).upper() != app:
        raise ValueError("Native operation game differs from shared proof")
    row = native._managed_row(managed)
    if (row["application_id"].upper() != app
        or row["title_id"].upper() != proof["title_id"]
        or row["content_type"] != proof["content_type"]):
        raise ValueError("Managed index identity differs from shared proof")

    state = ROOT / "state"
    lockpath = state / "ryujinx-configuration.lock"
    if state.is_symlink() or lockpath.is_symlink():
        raise ValueError("Unsafe registration lock")
    state.mkdir(mode=0o700, exist_ok=True)
    with lockpath.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        operations.ensure_closed()
        validate_source()
        game = read_game()
        names = []
        if proof["content_type"] == "dlc":
            names = [
                name for name in native.pfs0_names(managed)
                if name.lower().endswith(".nca")
                and not name.lower().endswith(".cnmt.nca")
            ]
        plan = planner.plan(game, managed, proof, names)
        configuration = game["native_snapshot"]["configuration"]
        originals = {}
        paths = {}
        for kind, filename in (("updates", "updates.json"), ("dlc", "dlc.json")):
            path = native.ryujinx_game_dir(app) / filename
            if str(path) != configuration[kind]["path"]:
                raise ValueError("Unexpected native configuration location")
            raw, document = adapter.read_configuration(
                path, {"selected": None, "paths": []} if kind == "updates" else [])
            if document != plan["before"][kind]:
                raise ValueError("Native configuration changed during planning")
            originals[kind], paths[kind] = raw, path
        target = plan["target_configuration"]
        other = "dlc" if target == "updates" else "updates"
        if read_game() != game:
            raise ValueError("Installed state changed before registration")
        transaction = state / "ryujinx-registration-transactions" / uuid.uuid4().hex
        transaction.mkdir(mode=0o700, parents=True)
        for kind, raw in originals.items():
            if raw is not None:
                writer.atomic_bytes(transaction / (kind + ".before.json"), raw)
        journal = {
            "state": "prepared", "application_id": app,
            "managed_path": str(managed), "verified_report": proof,
            "plan": plan,
        }
        def save():
            writer.atomic_bytes(transaction / "journal.json",
                (json.dumps(journal, indent=2) + "\n").encode())
        save()
        allowed = [plan["before"][target], plan["after"][target]]
        if target == "dlc":
            for enabled in (True, False):
                document = copy.deepcopy(plan["after"]["dlc"])
                for entry in document:
                    if os.path.realpath(entry["path"]) == os.path.realpath(managed):
                        for item in entry["dlc_nca_list"]:
                            item["is_enabled"] = enabled
                allowed.append(document)
        started = False
        try:
            operations.ensure_closed()
            for kind, path in paths.items():
                current = path.read_bytes() if path.exists() else None
                if current != originals[kind]:
                    raise ValueError("Configuration changed before native registration")
            validate_source()
            started = True
            native.ryujinx_install(managed, rom, confirmed=True)
            if target == "dlc" and plan["migration_enabled"] is True:
                native.ryujinx_set_dlc_enabled(
                    [str(managed)], rom, True, confirmed=True)
            written = paths[target].read_bytes()
            if normalized(json.loads(written), target) != normalized(
                    plan["after"][target], target):
                raise ValueError("Native registration differs from preservation plan")
            other_now = paths[other].read_bytes() if paths[other].exists() else None
            if other_now != originals[other]:
                raise ValueError("Unrelated native configuration changed")
            journal["state"] = "registered_pending_verification"
            save()
            refresh_inventory()
            validate_source()
            fresh = read_game()
            matches = [
                item for item in fresh["records"]
                if os.path.realpath(item["registered_path"]) == os.path.realpath(managed)
                and all(item.get(key) == proof.get(key) for key in
                    ("application_id", "title_id", "content_type",
                     "internal_version", "sha256"))
            ]
            if len(matches) != 1:
                raise ValueError("Fresh inventory did not verify managed registration")
            for kind in ("updates", "dlc"):
                actual = fresh["native_snapshot"]["configuration"][kind]["document"]
                if normalized(actual, kind) != normalized(plan["after"][kind], kind):
                    raise ValueError("Registered selection changed during verification")
            operations.ensure_closed()
            if (paths[target].read_bytes() != written
                or writer.source_id(managed) != before_file):
                raise ValueError("Registration or managed package changed before completion")
            journal["state"] = "registered_preserving_selection"
            save()
            return {
                "state": "INSTALLED", "local": str(managed),
                "active_preserved": True, "transaction": str(transaction),
            }
        except BaseException as exc:
            journal["error"] = str(exc)[:400]
            try:
                operations.ensure_closed()
                if started:
                    path = paths[target]
                    current = path.read_bytes() if path.exists() else None
                    if current != originals[target]:
                        if (current is None
                            or not any(normalized(json.loads(current), target) ==
                                       normalized(item, target) for item in allowed)):
                            raise RuntimeError("Concurrent native change; refusing overwrite")
                        if originals[target] is None:
                            path.unlink()
                            writer.directory_sync(path.parent)
                        else:
                            writer.atomic_bytes(path, originals[target])
                    refresh_inventory()
                    read_game()
                journal["state"] = "rolled_back"
                save()
            except BaseException as rollback:
                journal.update(state="rollback_requires_review",
                               rollback_error=str(rollback)[:400])
                save()
                raise RuntimeError("Inspect Ryujinx registration: "
                                   + str(transaction)) from exc
            raise
