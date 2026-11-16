
"""Shared, typed Switch verification proofs; no emulator configuration writes."""
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / "state"
STORE = DIRECTORY / "verified-content.json"
LOCK = DIRECTORY / "verified-content.lock"
SCHEMA = "GCM_SHARED_SWITCH_VERIFIED_CONTENT_V1"

def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def key(rom, source, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    return hashlib.sha256((
        str(Path(rom).resolve(strict=True)) + "\0"
        + str(Path(source).resolve(strict=True)) + "\0" + content_type
    ).encode()).hexdigest()

def valid(proof):
    if not isinstance(proof, dict):
        return False
    if (
        proof.get("schema") != "GCM_SHARED_SWITCH_CONTENT_PROOF_V1"
        or proof.get("status") != "PASS"
        or proof.get("content_type") not in ("update", "dlc")
        or proof.get("boot_id") != boot()
        or proof.get("base_metadata_verified") is not True
        or proof.get("identity_match") is not True
        or proof.get("primary_content_hashes_verified") is not True
        or proof.get("present_content_hashes_verified") is not True
        or proof.get("import_authorized") is not False
        or proof.get("changes_performed") is not False
        or type(proof.get("internal_version")) is not int
    ):
        return False
    for field, length in (("application_id", 16), ("title_id", 16), ("sha256", 64)):
        value = proof.get(field)
        if (not isinstance(value, str) or len(value) != length
                or any(c not in "0123456789abcdefABCDEF" for c in value)):
            return False
    try:
        base = Path(proof["base"])
        source = Path(proof["source"])
        if (not base.is_absolute() or not source.is_absolute()
                or str(base.resolve(strict=True)) != str(base)
                or str(source.resolve(strict=True)) != str(source)
                or not base.is_file() or not source.is_file()
                or base.samefile(source)):
            return False
        return proof.get("source_stats") == [identity(base), identity(source)]
    except (OSError, KeyError, TypeError, ValueError):
        return False

def read():
    if DIRECTORY.is_symlink() or STORE.is_symlink():
        raise ValueError("Unsafe shared proof store")
    if not STORE.exists():
        return {"schema": SCHEMA, "records": {}}
    state = json.loads(STORE.read_text())
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or not isinstance(state.get("records"), dict)):
        raise ValueError("Invalid shared proof store")
    return state

def lookup(rom, source, content_type):
    proof = read()["records"].get(key(rom, source, content_type))
    if not valid(proof):
        return None
    if (proof["base"] != str(Path(rom).resolve(strict=True))
            or proof["source"] != str(Path(source).resolve(strict=True))
            or proof["content_type"] != content_type):
        return None
    return proof

def record(proof):
    proof = json.loads(json.dumps(proof))
    if not valid(proof):
        raise ValueError("Current complete verification proof required")
    if DIRECTORY.is_symlink():
        raise ValueError("Unsafe shared state directory")
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    if LOCK.is_symlink():
        raise ValueError("Unsafe shared proof lock")
    descriptor = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not valid(proof):
            raise ValueError("Verification proof changed before promotion")
        state = read()
        state["records"][key(proof["base"], proof["source"], proof["content_type"])] = proof
        fd, name = tempfile.mkstemp(prefix=".verified-", dir=DIRECTORY)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(state, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, STORE)
            directory_fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)
