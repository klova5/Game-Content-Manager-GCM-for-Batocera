"""Select verified PS4 update overlays; game execution remains separate."""
import ctypes
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent
STORE_SHA = "7f6e6ac883d680cc08f851a08c382f83b73b080b4cae3c2034af155189976bcc"

def store():
    path = ROOT / "gcm-ps4-update-version-store-measured-v01-menu-evidence-v01-space-v01.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != STORE_SHA:
        raise ValueError("Update version store changed")
    spec = importlib.util.spec_from_file_location("gcm_selection_store", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def save(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".selection-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def idle():
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / "comm").read_text().strip().lower()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        if "shadps4" in name:
            raise ValueError("Close shadPS4 before selecting an update")

def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}\.\d{2}", value):
        raise ValueError("Unsupported update version")
    return tuple(map(int, value.split(".")))

def select(rom, record):
    module = store()
    proof = module.verify(rom, record)
    relationship = proof["relationship_evidence"]
    base_sfo = module.safe(relationship["base_metadata_path"]).resolve(strict=True)
    game = base_sfo.parent.parent
    if (
        game.parent != Path("/userdata/roms/ps4")
        or game.name != record["application_id"]
        or base_sfo != game / "sce_sys/param.sfo"
    ):
        raise ValueError("Existing internal PS4 game target required")

    # Use the already pinned SFO reader through the version-store identity dependency.
    # Resolve the SFO reader directly with its established component pin.
    sfo_path = ROOT / "gcm-sfo-metadata.py"
    if (
        sfo_path.is_symlink()
        or hashlib.sha256(sfo_path.read_bytes()).hexdigest()
        != "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41"
    ):
        raise ValueError("SFO reader changed")
    spec = importlib.util.spec_from_file_location("gcm_selection_sfo", sfo_path)
    sfo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sfo)
    baseline = sfo.read(base_sfo)
    if version(record["version"]) < version(baseline["fields"]["APP_VER"]):
        raise ValueError("Cannot downgrade a game that already contains a newer merged update")

    target = game.with_name(game.name + "-UPDATE")
    alternate = game.with_name(game.name + "-patch")
    module.safe(target)
    module.safe(alternate)
    if alternate.exists():
        raise ValueError("Existing alternate patch directory requires reconciliation")

    state = ROOT / "state/ps4-update-selection"
    module.safe(state)
    state.mkdir(parents=True, exist_ok=True)
    receipt = state / (game.name + ".json")
    lock_path = state / ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        idle()
        service = module.load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")

        def verify_at(candidate, selected_record):
            return service.verify_installed(
                base_sfo, selected_record["source"], candidate,
                "ps4", "update", allow_unrelated=False)

        if target.exists():
            module.safe(receipt)
            previous = json.loads(receipt.read_text())
            if (
                previous.get("status") != "SELECTED_FILES_VERIFIED"
                or previous.get("overlay") != str(target)
            ):
                raise ValueError("Existing overlay has no matching selection receipt")
            verify_at(target, previous["installed_record"])

        staging = Path(tempfile.mkdtemp(
            prefix=".gcm-update-" + game.name + "-", dir=game.parent))
        output = staging / "output"
        shutil.copytree(record["installed_root"], output, symlinks=True)
        verify_at(output, record)
        for directory, dirs, files in os.walk(output, topdown=False):
            for name in files:
                with (Path(directory) / name).open("rb") as stream:
                    os.fsync(stream.fileno())
            sync(directory)
        sync(staging)

        if sfo.read(base_sfo) != baseline:
            raise ValueError("Base metadata changed before selection")
        idle()
        transaction = state / ("transaction-" + uuid.uuid4().hex + ".json")
        journal = {
            "status": "PREPARED",
            "overlay": str(target),
            "staged_output": str(output),
            "previous_receipt": (
                json.loads(receipt.read_text()) if target.exists() else None),
            "installed_record": record,
        }
        save(transaction, journal)

        if target.exists():
            libc = ctypes.CDLL(None, use_errno=True)
            exchange = getattr(libc, "renameat2", None)
            if exchange is None:
                raise ValueError("Atomic overlay exchange unavailable")
            exchange.argtypes = [
                ctypes.c_int, ctypes.c_char_p,
                ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            exchange.restype = ctypes.c_int
            if exchange(-100, os.fsencode(output), -100,
                        os.fsencode(target), 2) != 0:
                error = ctypes.get_errno()
                raise OSError(error, "Atomic overlay exchange failed")
        else:
            os.rename(output, target)
        sync(game.parent)
        sync(staging)
        journal["status"] = "PUBLISHED"
        save(transaction, journal)

        verify_at(target, record)
        result = {
            "status": "SELECTED_FILES_VERIFIED",
            "application_id": game.name,
            "version": record["version"],
            "overlay": str(target),
            "installed_record": record,
            "transaction": str(transaction),
            "previous_overlay_retained": str(output) if output.exists() else None,
            "in_game_availability": "UNVERIFIED",
        }
        save(receipt, result)
        journal["status"] = "COMPLETE"
        save(transaction, journal)
        return result
    finally:
        os.close(fd)
