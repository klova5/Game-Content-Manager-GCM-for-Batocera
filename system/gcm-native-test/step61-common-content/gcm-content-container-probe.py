"""Bounded container identification; no content verification or installation."""
import os
import struct
from pathlib import Path

def identity(value):
    return [value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]

def probe(path, expected_identity=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular candidate file required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if expected_identity is not None and identity(before) != expected_identity:
            raise ValueError("Candidate changed since discovery")
        header = stream.read(64)
        after = os.fstat(stream.fileno())
    if identity(before) != identity(after) or identity(before) != identity(path.stat()):
        raise ValueError("Candidate changed during header inspection")
    magic = header[:4]
    kind = "UNRESOLVED"
    scopes = []
    if magic == b"\x7fCNT":
        kind, scopes = "PS4_PKG_HEADER", ["ps4"]
    elif magic == b"\x7fPKG":
        kind, scopes = "SONY_PKG_HEADER", ["ps3", "psvita"]
    elif magic in (b"CON ", b"LIVE", b"PIRS"):
        kind, scopes = "XBOX360_STFS_HEADER", ["xbox360"]
    elif magic in (b"PK\x03\x04", b"PK\x05\x06"):
        kind = "ZIP_CONTAINER_HEADER"
    elif len(header) >= 32:
        header_size, wad_type, padding = struct.unpack_from(">IHH", header)
        if header_size == 32 and wad_type == 0x4973 and padding == 0:
            kind, scopes = "WII_WAD_HEADER", ["wii"]
    return {
        "path": str(path.absolute()),
        "container_hint": kind,
        "possible_systems": scopes,
        "stat_identity": identity(before),
        "content_type": "UNRESOLVED",
        "package_integrity_verified": False,
        "game_relationship_verified": False,
    }
