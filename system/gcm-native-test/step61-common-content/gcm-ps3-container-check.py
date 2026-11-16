"""PS3 container consistency; does not assert decrypted payload integrity."""
import hashlib
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def metadata_reader():
    path = ROOT / "gcm-sony-package-metadata-v02.py"
    expected = "0238e5f5072ce329d049dda515be94420a3c1d23b8790d5644801bcbde43ef1c"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Sony metadata reader changed")
    spec = importlib.util.spec_from_file_location("ps3_internal_metadata", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def check(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular package required")
    reader = metadata_reader()
    metadata = reader.read(path)
    if metadata.get("fields", {}).get("PKG_PLATFORM") != 1:
        raise ValueError("Internal PS3 platform required")
    before = identity(path.stat())
    if before != list(metadata["stat_identity"]) or before[2] < 32:
        raise ValueError("Package identity changed or invalid")

    prefix = hashlib.sha1()
    whole = hashlib.sha256()
    with path.open("rb") as stream:
        if identity(os.fstat(stream.fileno())) != before:
            raise ValueError("Opened package identity changed")
        remaining = before[2] - 32
        while remaining:
            block = stream.read(min(8 * 1024 * 1024, remaining))
            if not block:
                raise ValueError("Truncated package")
            prefix.update(block)
            whole.update(block)
            remaining -= len(block)
        footer = stream.read(32)
        if len(footer) != 32 or stream.read(1):
            raise ValueError("Package size changed")
        whole.update(footer)
        if prefix.digest() != footer[:20]:
            raise ValueError("PS3 container footer hash mismatch")
        if identity(os.fstat(stream.fileno())) != before:
            raise ValueError("Package changed during checking")

    if identity(path.stat()) != before or reader.read(path) != metadata:
        raise ValueError("Package metadata changed before completion")
    return {
        "status": "CONTAINER_HASHES_VERIFIED",
        "path": str(path),
        "title_id": metadata["title_id"],
        "content_type": metadata["content_type"],
        "sha256": whole.hexdigest(),
        "stat_identity": before,
        "footer_sha1_verified": True,
        "decrypted_payload_hashes_verified": False,
        "publisher_authentication_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
