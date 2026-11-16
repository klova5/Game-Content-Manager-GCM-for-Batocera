
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
SCHEMA = "GCM_PLATFORM_VERIFIED_CONTENT_V1"

def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def key(rom, source, content_type, system):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    return hashlib.sha256((
        system + "\0" + str(Path(rom).resolve(strict=True)) + "\0"
        + str(Path(source).resolve(strict=True)) + "\0" + content_type
    ).encode()).hexdigest()

def valid(proof):
    if not isinstance(proof, dict):
        return False
    if (
        proof.get("schema") != "GCM_GAME_CONTENT_PROOF_V1"
        or proof.get("status") != "PASS"
        or proof.get("system") != "ps4"
        or proof.get("content_type") not in ("update", "dlc")
        or proof.get("boot_id") != boot()
        or proof.get("game_relationship_verified") is not True
        or proof.get("payload_hashes_verified") is not True
        or proof.get("installation_authorized") is not False
        or proof.get("changes_performed") is not False
    ):
        return False
    try:
        import importlib.util
        import re
        if not re.fullmatch(r"CUSA[0-9]{5}", proof["application_id"]):
            return False
        if proof["title_id"] != proof["application_id"]:
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", proof["sha256"]):
            return False
        base, source = Path(proof["base"]), Path(proof["source"])
        if (
            not base.is_absolute() or not source.is_absolute()
            or base.is_symlink() or source.is_symlink()
            or str(base.resolve(strict=True)) != str(base)
            or str(source.resolve(strict=True)) != str(source)
            or not base.is_file() or not source.is_file()
            or base.samefile(source)
            or proof["source_stats"] != [identity(base), identity(source)]
        ):
            return False
        payload = proof["payload_evidence"]
        if (
            payload.get("schema") != "GCM_PLATFORM_PAYLOAD_PROOF_V1"
            or payload.get("status") != "PAYLOAD_HASHES_VERIFIED"
            or payload.get("boot_id") != proof["boot_id"]
            or payload.get("installation_authorized") is not False
            or payload.get("stat_identity") != proof["source_stats"][1]
            or payload.get("path") != proof["source"]
            or any(payload.get(k) != proof.get(k) for k in (
                "system", "title_id", "content_type", "version", "sha256"))
        ):
            return False
        dependency = ROOT / "gcm-sony-content-relationship-measured-v01.py"
        expected = "0c006d1a583cbae533845554af8fb54797bfe3f437017701e155bb32fc4747fa"
        if dependency.is_symlink() or hashlib.sha256(dependency.read_bytes()).hexdigest() != expected:
            raise ValueError("Relationship dependency changed")
        spec = importlib.util.spec_from_file_location("stored_relationship", dependency)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        current = module.match(base, source, proof["system"])
        return (
            current == proof["relationship_evidence"]
            and current["application_id"] == proof["application_id"]
            and current["content_type"] == proof["content_type"]
            and current["version"] == proof["version"]
            and current["base_stat_identity"] == proof["source_stats"][0]
            and current["package_stat_identity"] == proof["source_stats"][1]
        )
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

def lookup(rom, source, content_type, system):
    proof = read()["records"].get(key(rom, source, content_type, system))
    if not valid(proof):
        return None
    if (proof["system"] != system
            or proof["base"] != str(Path(rom).resolve(strict=True))
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
        state["records"][key(proof["base"], proof["source"], proof["content_type"], proof["system"])] = proof
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
