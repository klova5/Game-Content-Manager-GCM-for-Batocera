"""Verified PS4 DLC storage, independent of enabled entitlement directories."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
MANAGED = Path("/userdata/system/game-content/ps4")
PINS = {
    "gcm-ps4-dlc-runtime-manifest.py":
        "aaf6e25af1f0e8ead282bf16f2127962b1f9cd5305f8b5e8898c2e8d85d64678",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("DLC store dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink DLC path rejected")
    return path

def identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def verify(base, source, installed):
    runtime = load("gcm-ps4-dlc-runtime-manifest.py")
    evidence = runtime.manifest(base, source)
    manifest = evidence["manifest"]
    installed = safe(installed).resolve(strict=True)
    if not installed.is_dir():
        raise ValueError("Installed DLC directory required")
    expected = {entry["path"] for entry in manifest["entries"]}
    observed = set()
    for directory, dirs, files in os.walk(installed, followlinks=False):
        for name in dirs + files:
            item = safe(Path(directory) / name)
            observed.add(item.relative_to(installed).as_posix())
    if observed != expected:
        raise ValueError("Installed DLC paths differ from package")
    verified = []
    for entry in manifest["entries"]:
        item = installed / entry["path"]
        if entry["kind"] == "directory":
            if not item.is_dir():
                raise ValueError("Installed DLC directory missing")
            continue
        if not item.is_file():
            raise ValueError("Installed DLC regular file required")
        before = identity(item)
        if before[2] != entry["size"]:
            raise ValueError("Installed DLC size mismatch")
        digest = hashlib.sha256()
        with item.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if [opened.st_dev, opened.st_ino, opened.st_size,
                opened.st_mtime_ns, opened.st_ctime_ns] != before:
                raise ValueError("Installed DLC changed before reading")
            remaining = entry["size"]
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("Installed DLC truncated")
                digest.update(block)
                remaining -= len(block)
            if stream.read(1):
                raise ValueError("Installed DLC grew")
        if digest.hexdigest() != entry["sha256"] or identity(item) != before:
            raise ValueError("Installed DLC hash or identity mismatch: " + entry["path"])
        verified.append((item, before))
    metadata = load("gcm-sfo-metadata.py").read(installed / "sce_sys/param.sfo")
    title = evidence["game_evidence"]["application_id"]
    content_id = metadata["fields"].get("CONTENT_ID", "")
    match = re.fullmatch(
        r"[A-Z]{2}[0-9]{4}-" + re.escape(title) + r"_00-([A-Z0-9_]{16})",
        content_id)
    if not match or metadata["fields"].get("TITLE_ID") != title:
        raise ValueError("DLC entitlement and game identity disagree")
    if any(identity(item) != before for item, before in verified):
        raise ValueError("Installed DLC changed before completion")
    if load("gcm-sfo-metadata.py").read(installed / "sce_sys/param.sfo") != metadata:
        raise ValueError("DLC metadata changed during verification")
    return {
        "status": "GAME_BOUND_DLC_FILES_VERIFIED",
        "application_id": title,
        "content_id": content_id,
        "entitlement_label": match.group(1),
        "installed_root": str(installed),
        "package_sha256": manifest["package_sha256"],
        "manifest": manifest,
        "in_game_availability": "UNVERIFIED",
    }

def publish(base, source, prepared):
    source = safe(source).resolve(strict=True)
    proof = verify(base, source, prepared)
    title = proof["application_id"]
    if source.parent != MANAGED / title / "dlc":
        raise ValueError("Managed game-specific DLC source required")
    directory = safe(MANAGED / title / "installed-dlc")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = proof["package_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid package digest")
    slot = safe(directory / digest)
    record_path = safe(directory / (digest + ".json"))
    lock = os.open(directory / ".lock",
                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not slot.exists():
            temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=directory))
            output = temporary / "output"
            shutil.copytree(prepared, output, symlinks=True)
            verify(base, source, output)
            for current, dirs, files in os.walk(output, topdown=False):
                for name in files:
                    with (Path(current) / name).open("rb") as stream:
                        os.fsync(stream.fileno())
                sync(current)
            os.rename(output, slot)
            sync(directory)
            temporary.rmdir()
        final = verify(base, source, slot)
        record = {
            "schema": "GCM_PS4_INSTALLED_DLC_V1",
            "application_id": title,
            "content_id": final["content_id"],
            "entitlement_label": final["entitlement_label"],
            "source": str(source),
            "package_sha256": digest,
            "installed_root": str(slot),
            "installation_state": "INSTALLED",
            "enablement_performed": False,
            "in_game_availability": "UNVERIFIED",
        }
        if record_path.exists():
            if json.loads(record_path.read_text()) != record:
                raise ValueError("Existing DLC record disagrees")
        else:
            fd, temporary_name = tempfile.mkstemp(prefix=".record-", dir=directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(record, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, record_path)
                sync(directory)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return record
    finally:
        os.close(lock)
