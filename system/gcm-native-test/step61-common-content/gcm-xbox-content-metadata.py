"""Bounded Xbox 360 XContent/STFS metadata; no payload verification."""
import hashlib
import os
import struct
from pathlib import Path

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular Xbox content file required")
    with path.open("rb") as stream:
        before = identity(os.fstat(stream.fileno()))
        data = stream.read(0x971a)
        if len(data) != 0x971a or data[:4] not in (b"CON ", b"LIVE", b"PIRS"):
            raise ValueError("Unsupported or truncated Xbox content header")
        header_size = struct.unpack_from(">I", data, 0x340)[0]
        if not 0x971a <= header_size <= min(before[2], 16 * 1024 * 1024):
            raise ValueError("Xbox header size outside bounds")
        raw_type, metadata_version = struct.unpack_from(">II", data, 0x344)
        if metadata_version not in (1, 2):
            raise ValueError("Unsupported Xbox metadata version")
        media, version, base_version, title = struct.unpack_from(">IIII", data, 0x354)
        if not title:
            raise ValueError("Missing internal Xbox title ID")
        volume_type = struct.unpack_from(">I", data, 0x3a9)[0]
        if volume_type != 0:
            raise ValueError("Only STFS volume metadata currently supported")
        if data[0x379] != 0x24:
            raise ValueError("Invalid STFS volume descriptor length")
        if identity(os.fstat(stream.fileno())) != before:
            raise ValueError("Xbox source changed during reading")
    if identity(path.stat()) != before:
        raise ValueError("Xbox source changed before completion")
    return {
        "path": str(path), "stat_identity": before,
        "container": "STFS", "package_magic": data[:4].decode("ascii"),
        "header_size": header_size, "metadata_version": metadata_version,
        "raw_content_type": raw_type,
        "title_id": f"{title:08X}", "media_id": f"{media:08X}",
        "internal_version": version, "base_version": base_version,
        "declared_content_size": struct.unpack_from(">Q", data, 0x34c)[0],
        "metadata_sha256": hashlib.sha256(data).hexdigest(),
        "content_type": "UNRESOLVED",
        "payload_hashes_verified": False,
        "signature_authenticity_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
