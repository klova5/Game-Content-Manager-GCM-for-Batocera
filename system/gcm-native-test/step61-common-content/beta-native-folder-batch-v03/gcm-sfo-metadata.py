"""Bounded SFO metadata reading; no installation or content-integrity claims."""
import hashlib
import os
import struct
from pathlib import Path

MAX_SIZE = 4 * 1024 * 1024

def identity(stat):
    return [stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]

def parse(data):
    if not isinstance(data, bytes) or not 20 <= len(data) <= MAX_SIZE:
        raise ValueError("SFO size outside limits")
    magic, version, keys, values, count = struct.unpack_from("<4sIIII", data)
    if magic != b"\0PSF" or version != 0x101:
        raise ValueError("Unsupported SFO header")
    if count > 4096 or not 20 + count * 16 <= keys <= values <= len(data):
        raise ValueError("Invalid SFO table bounds")
    result = {}
    for index in range(count):
        key_offset, kind, length, capacity, offset = struct.unpack_from(
            "<HHIII", data, 20 + index * 16)
        start = keys + key_offset
        if not keys <= start < values:
            raise ValueError("Invalid SFO key offset")
        end = data.find(b"\0", start, values)
        if end < 0 or end == start or end - start > 256:
            raise ValueError("Invalid SFO key")
        key = data[start:end].decode("ascii")
        if key in result:
            raise ValueError("Duplicate SFO key")
        start = values + offset
        if length > capacity or start > len(data) or capacity > len(data) - start:
            raise ValueError("Invalid SFO value bounds")
        raw = data[start:start + length]
        if kind == 0x0404:
            if length != 4 or capacity != 4:
                raise ValueError("Invalid SFO integer")
            value = struct.unpack("<I", raw)[0]
        elif kind == 0x0204:
            value = raw.split(b"\0", 1)[0].decode("utf-8")
        elif kind == 0x0004:
            value = {"encoding": "hex", "value": raw.hex()}
        else:
            raise ValueError("Unsupported SFO field format")
        result[key] = value
    return result

def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular SFO file required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not 20 <= before.st_size <= MAX_SIZE:
            raise ValueError("SFO size outside limits")
        data = stream.read(MAX_SIZE + 1)
        after = os.fstat(stream.fileno())
    if identity(before) != identity(after) or identity(before) != identity(path.stat()):
        raise ValueError("SFO changed during reading")
    return {
        "path": str(path.absolute()),
        "stat_identity": identity(before),
        "metadata_sha256": hashlib.sha256(data).hexdigest(),
        "fields": parse(data),
        "package_integrity_verified": False,
        "game_relationship_verified": False,
    }
