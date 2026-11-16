"""Journaled PS4 DLC directory selection for the global Apply Changes action."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib
import uuid

ROOT = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/shadps4/user/config.toml")
ADDONS = Path("/userdata/roms/ps4/DLC")

def store():
    path = ROOT / "gcm-ps4-dlc-version-store.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != (
        "3193c40a796a00aeb1bcd4bfa25714e75e055e30f73417e55234d08109062058"
    ):
        raise ValueError("DLC version store changed")
    spec = importlib.util.spec_from_file_location("dlc_selection_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def save(module, path, data):
    fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        module.sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def idle():
    for item in Path("/proc").iterdir():
        if not item.name.isdigit():
            continue
        try:
            name = (item / "comm").read_text().strip().lower()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if "shadps4" in name:
            raise ValueError("Close shadPS4 before applying DLC changes")

def configuration(module):
    module.safe(CONFIG)
    data = CONFIG.read_bytes()
    parsed = tomllib.loads(data.decode())
    if parsed.get("GUI", {}).get("addonInstallDir") != str(ADDONS):
        raise ValueError("Unsupported configured DLC directory")
    module.safe(ADDONS)
    return hashlib.sha256(data).hexdigest()

def catalog(base):
    module = store()
    runtime = module.load("gcm-ps4-dlc-runtime-manifest.py")
    service = runtime.load("gcm-platform-content-service-v03.py")
    # Read the base identity through the pinned shared SFO reader.
    metadata = module.load("gcm-sfo-metadata.py").read(base)
    title = metadata["fields"].get("TITLE_ID", "")
    import re
    if not re.fullmatch(r"CUSA[0-9]{5}", title):
        raise ValueError("Internal PS4 game identity required")
    directory = module.safe(module.MANAGED / title / "installed-dlc")
    records = {}
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            module.safe(path)
            record = json.loads(path.read_text())
            digest = record.get("package_sha256", "")
            if (
                record.get("schema") != "GCM_PS4_INSTALLED_DLC_V1"
                or record.get("application_id") != title
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or path.name != digest + ".json"
                or Path(record["installed_root"]) != directory / digest
                or Path(record["source"]).parent != module.MANAGED / title / "dlc"
            ):
                raise ValueError("Installed DLC record location or identity disagrees")
            proof = module.verify(base, record["source"], record["installed_root"])
            label = record["entitlement_label"]
            if (
                proof["entitlement_label"] != label
                or proof["content_id"] != record["content_id"]
                or proof["package_sha256"] != digest
                or label in records
            ):
                raise ValueError("Conflicting DLC entitlement records")
            records[label] = record
    if module.load("gcm-sfo-metadata.py").read(base) != metadata:
        raise ValueError("Base metadata changed during DLC inventory")
    return module, title, records

def rollback_locked(module, base, journal_path, journal):
    operations = journal["operations"]
    moves = []
    for operation in reversed(operations):
        target = module.safe(operation["target"])
        parked = module.safe(operation["parked"])
        record = operation["record"]
        if target.exists() and parked.exists():
            raise ValueError("Ambiguous DLC recovery paths")
        if not target.exists() and not parked.exists():
            raise ValueError("DLC recovery content missing")
        current = target if target.exists() else parked
        module.verify(base, record["source"], current)
        if operation["action"] == "ENABLE" and target.exists():
            moves.append((target, parked))
        elif operation["action"] == "DISABLE" and parked.exists():
            moves.append((parked, target))
    journal["status"] = "ROLLING_BACK"
    save(module, journal_path, journal)
    for origin, destination in moves:
        os.rename(origin, destination)
        module.sync(origin.parent)
        module.sync(destination.parent)
    journal["status"] = "ROLLED_BACK"
    save(module, journal_path, journal)

def apply(base, selected_labels):
    if (
        not isinstance(selected_labels, list)
        or any(not isinstance(label, str) for label in selected_labels)
        or len(selected_labels) != len(set(selected_labels))
    ):
        raise ValueError("Unique explicit DLC selection required")
    module, title, records = catalog(base)
    wanted = set(selected_labels)
    if not wanted.issubset(records):
        raise ValueError("Selected DLC has no verified installed record")
    config_hash = configuration(module)
    idle()
    state = module.safe(ROOT / "state/ps4-dlc-selection")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(state / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for existing in state.glob("transaction-*.json"):
            module.safe(existing)
            if json.loads(existing.read_text()).get("status") not in (
                "COMPLETE", "ROLLED_BACK"
            ):
                raise ValueError("Unfinished DLC transaction requires recovery")
        if configuration(module) != config_hash:
            raise ValueError("DLC configuration changed")
        idle()
        game = module.safe(ADDONS / title)
        enabled = set()
        unknown = set()
        if game.exists():
            if not game.is_dir():
                raise ValueError("DLC game directory has wrong type")
            for item in game.iterdir():
                module.safe(item)
                if item.is_dir():
                    if item.name in records:
                        module.verify(base, records[item.name]["source"], item)
                        enabled.add(item.name)
                    else:
                        unknown.add(item.name)
        if len(wanted | unknown) > 16:
            raise ValueError("DLC selection exceeds this adapter's bounded scope")
        changes = sorted(enabled ^ wanted)
        if not changes:
            return {"status": "VERIFIED", "enabled": sorted(enabled), "changes": 0}
        ADDONS.mkdir(mode=0o700, parents=True, exist_ok=True)
        game.mkdir(mode=0o700, exist_ok=True)
        parking = module.safe(ADDONS / ".gcm-transactions")
        parking.mkdir(mode=0o700, exist_ok=True)
        work = parking / uuid.uuid4().hex
        work.mkdir(mode=0o700)
        operations = []
        for label in changes:
            record = records[label]
            parked = work / label
            action = "DISABLE" if label in enabled else "ENABLE"
            if action == "ENABLE":
                shutil.copytree(record["installed_root"], parked, symlinks=True)
                module.verify(base, record["source"], parked)
                for directory, dirs, files in os.walk(parked, topdown=False):
                    for name in files:
                        with (Path(directory) / name).open("rb") as stream:
                            os.fsync(stream.fileno())
                    module.sync(directory)
            operations.append({
                "action": action, "target": str(game / label),
                "parked": str(parked), "record": record,
            })
        module.sync(work)
        module.sync(parking)
        journal_path = state / ("transaction-" + uuid.uuid4().hex + ".json")
        journal = {
            "status": "PREPARED", "base": str(Path(base).resolve(strict=True)),
            "application_id": title, "config_sha256": config_hash,
            "operations": operations, "selected_labels": sorted(wanted),
        }
        save(module, journal_path, journal)
        try:
            if configuration(module) != config_hash:
                raise ValueError("DLC configuration changed before publication")
            idle()
            for operation in operations:
                target = Path(operation["target"])
                parked = Path(operation["parked"])
                origin, destination = (
                    (parked, target) if operation["action"] == "ENABLE"
                    else (target, parked))
                if destination.exists() or destination.is_symlink():
                    raise ValueError("DLC destination appeared during transaction")
                record = operation["record"]
                module.verify(base, record["source"], origin)
                os.rename(origin, destination)
                module.sync(origin.parent)
                module.sync(destination.parent)
            for label, record in records.items():
                target = game / label
                if label in wanted:
                    module.verify(base, record["source"], target)
                elif target.exists():
                    raise ValueError("Deselected DLC remains enabled")
            if configuration(module) != config_hash:
                raise ValueError("DLC configuration changed during transaction")
            journal["status"] = "COMPLETE"
            save(module, journal_path, journal)
        except Exception:
            rollback_locked(module, base, journal_path, journal)
            raise
        return {
            "status": "VERIFIED", "enabled": sorted(wanted),
            "changes": len(operations), "transaction": str(journal_path),
            "unmanaged_entitlements_preserved": sorted(unknown),
            "in_game_availability": "UNVERIFIED",
        }
    finally:
        os.close(lock)
