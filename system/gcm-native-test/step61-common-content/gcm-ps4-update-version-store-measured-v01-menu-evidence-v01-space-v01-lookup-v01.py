"""Verified PS4 update versions stored separately from the active overlay."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
MANAGED = Path("/userdata/system/game-content/ps4")
PINS = {
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py":
        "2b9ab6a2bdad2e235e82de9dccc8f692dfc5d183dc056b6b8206ac102ba2d79b",
}

_GCM_DECLARED_CODE_NAMES = {'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01.py': 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py', 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py': 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py'}

def load(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Version-store dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe(path):
    path = Path(path).absolute()
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError("Symlink version-store path rejected")
    return path

def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def publish(rom, source, prepared):
    identity = load("gcm-ps4-base-identity.py").identify(rom)
    service = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py")
    source = safe(source).resolve(strict=True)
    prepared = safe(prepared).resolve(strict=True)
    game_root = MANAGED / identity["application_id"]
    if source.parent != game_root / "updates":
        raise ValueError("Managed game-matching update package required")
    base = identity["metadata_path"]
    proof = service.verify_installed(
        base, source, prepared, "ps4", "update", allow_unrelated=False)
    manifest = proof["installed_evidence"]["source_manifest"]
    digest = manifest["package_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Invalid verified package digest")
    store = safe(game_root / "installed-updates")
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    slot = store / digest
    record_path = store / (digest + ".json")
    lock_fd = os.open(store / ".lock",
                      os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    temporary = None
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if slot.exists():
            safe(slot)
            service.verify_installed(
                base, source, slot, "ps4", "update", allow_unrelated=False)
        else:
            temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=store))
            output = temporary / "output"
            shutil.copytree(prepared, output, symlinks=True)
            checked = service.verify_installed(
                base, source, output, "ps4", "update", allow_unrelated=False)
            for directory, dirs, files in os.walk(output):
                for name in files:
                    with (Path(directory) / name).open("rb") as stream:
                        os.fsync(stream.fileno())
            for directory, dirs, files in os.walk(output, topdown=False):
                sync(Path(directory))
            os.rename(output, slot)
            sync(store)
        final = service.verify_installed(
            base, source, slot, "ps4", "update", allow_unrelated=False)
        record = {
            "schema": "GCM_PS4_INSTALLED_UPDATE_VERSION_V1",
            "application_id": identity["application_id"],
            "content_type": "update",
            "version": final["installed_version"],
            "source": str(source),
            "package_sha256": digest,
            "installed_root": str(slot),
            "state": "INSTALLED",
            "selection_performed": False,
            "in_game_availability": "UNVERIFIED",
        }
        if record_path.exists():
            safe(record_path)
            if json.loads(record_path.read_text()) != record:
                raise ValueError("Existing installed-version record disagrees")
        else:
            with record_path.open("x") as stream:
                json.dump(record, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            sync(store)
        return record
    finally:
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        os.close(lock_fd)

def verify(rom, record):
    if record.get("schema") != "GCM_PS4_INSTALLED_UPDATE_VERSION_V1":
        raise ValueError("Installed-version record required")
    identity = load("gcm-ps4-base-identity.py").identify(rom)
    if record["application_id"] != identity["application_id"]:
        raise ValueError("Installed version belongs to another game")
    expected = MANAGED / identity["application_id"] / "installed-updates" / record["package_sha256"]
    installed = safe(record["installed_root"]).resolve(strict=True)
    if installed != expected:
        raise ValueError("Installed-version location disagrees")
    proof = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py").verify_installed(
        identity["metadata_path"], record["source"], installed,
        "ps4", "update", allow_unrelated=False)
    if (
        proof["installed_version"] != record["version"]
        or proof["installed_evidence"]["source_manifest"]["package_sha256"]
            != record["package_sha256"]
    ):
        raise ValueError("Installed-version evidence disagrees")
    return proof
