"""Copy game-bound verified content; never register or activate it."""
import fcntl
import hashlib
import importlib.util
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANAGED = Path("/userdata/system/game-content")
LEGACY = Path("/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-install-measured-v01-space-v01.py")

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Managed-copy dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location("copy_" + path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def prepare(base, source, system, content_type):
    store = load(ROOT / "gcm-platform-verification-store-measured-v01.py",
        "9212ce545452089c3ca6b225dbd2bb2371a01a78b22f960c130cc7034556b433")
    source = Path(source)
    if source.is_symlink():
        raise ValueError("Regular external source required")
    source = source.resolve(strict=True)
    proof = store.lookup(base, source, content_type, system)
    if proof is None:
        raise ValueError("Current game-bound payload verification required")
    if source.is_relative_to(MANAGED.resolve()):
        raise ValueError("External source required")
    # Store validation supplies the platform and title-ID constraints.
    destination = MANAGED / proof["system"] / proof["application_id"] / (
        "updates" if content_type == "update" else "dlc") / source.name
    for path in (destination, *destination.parents):
        if path.is_symlink():
            raise ValueError("Symlink in managed destination")
    return store, proof, source, destination

def preview(base, source, system, content_type):
    _, proof, source, destination = prepare(base, source, system, content_type)
    return {
        "status": "PREVIEW",
        "system": system, "application_id": proof["application_id"],
        "content_type": content_type, "source": str(source),
        "destination": str(destination),
        "destination_exists": destination.exists(),
        "registration_performed": False,
    }

def copy(base, source, system, content_type):
    store, proof, source, destination = prepare(base, source, system, content_type)
    shared = load(LEGACY,
        "5c4dafd5826105920f17f0b5a90b0aefdbee66157ea934f18c25f26b47e0ae0a")
    # Private module instance: apply the same copy guard to the common storage root.
    shared.ROOT = MANAGED
    state = ROOT / "state"
    if state.is_symlink():
        raise ValueError("Unsafe state directory")
    state.mkdir(mode=0o700, exist_ok=True)
    lockpath = state / "platform-managed-copy.lock"
    descriptor = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if store.lookup(base, source, content_type, system) != proof:
            raise ValueError("Proof changed before copying")
        for path in (destination, *destination.parents):
            if path.is_symlink():
                raise ValueError("Managed destination changed")
        with tempfile.TemporaryDirectory(prefix="platform-copy-", dir=ROOT) as temporary:
            digest, copied = shared.copy_verified(
                {"source": str(source), "target": destination}, Path(temporary))
        if digest != proof["sha256"]:
            raise ValueError("Managed copy differs from verified package")
        if store.lookup(base, source, content_type, system) != proof:
            raise ValueError("Source proof changed during copying")
        before = store.identity(destination)
        if shared.sha(destination) != digest or store.identity(destination) != before:
            raise ValueError("Managed copy changed during final check")
        return {
            "state": "READY", "system": system,
            "application_id": proof["application_id"],
            "content_type": content_type, "version": proof["version"],
            "source": str(source), "destination": str(destination),
            "sha256": digest, "managed_stat_identity": before,
            "copied": copied, "source_preserved": True,
            "registration_performed": False,
        }
