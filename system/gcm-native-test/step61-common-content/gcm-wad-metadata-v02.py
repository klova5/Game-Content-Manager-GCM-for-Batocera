"""Bounded Wii WAD/TMD metadata; encrypted content is not read or verified."""
import hashlib
import os
import struct
from pathlib import Path

def identity(value):
    return [value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]

def align(value):
    return (value + 63) & ~63

def read(path, expected_identity=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular WAD required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if expected_identity is not None and identity(before) != expected_identity:
            raise ValueError("WAD changed since discovery")
        header = stream.read(32)
        if len(header) != 32:
            raise ValueError("Truncated WAD header")
        size, kind, padding, cert, crl, ticket, tmd_size, data, footer = \
            struct.unpack(">IHHIIIIII", header)
        if size != 32 or kind != 0x4973 or padding != 0:
            raise ValueError("Unsupported WAD header")
        offset = align(size)
        sections = {}
        for name, length in (("cert", cert), ("crl", crl),
                             ("ticket", ticket), ("tmd", tmd_size),
                             ("data", data), ("footer", footer)):
            if offset > before.st_size or length > before.st_size - offset:
                raise ValueError("WAD section exceeds file: " + name)
            sections[name] = (offset, length)
            offset = align(offset + length)
        if not 0x1e4 <= tmd_size <= 0x1e4 + 512 * 36:
            raise ValueError("TMD outside supported bounds")
        stream.seek(sections["tmd"][0])
        tmd = stream.read(tmd_size)
        if len(tmd) != tmd_size:
            raise ValueError("Truncated TMD")
        if struct.unpack_from(">I", tmd)[0] != 0x00010001:
            raise ValueError("Additional TMD signature layout requires support")
        title = struct.unpack_from(">Q", tmd, 0x18c)[0]
        version, count = struct.unpack_from(">HH", tmd, 0x1dc)
        if not title or not 0 < count <= 512 or 0x1e4 + count * 36 > len(tmd):
            raise ValueError("Invalid TMD title or record count")
        records = []
        ids, indices = set(), set()
        for number in range(count):
            start = 0x1e4 + number * 36
            cid, index, flags, length = struct.unpack_from(">IHHQ", tmd, start)
            if cid in ids or index in indices or length <= 0:
                raise ValueError("Invalid or duplicate TMD content record")
            ids.add(cid)
            indices.add(index)
            records.append({
                "content_id": f"{cid:08x}", "index": index,
                "flags": flags, "size": length,
                "sha1": tmd[start + 16:start + 36].hex(),
            })
        if identity(before) != identity(os.fstat(stream.fileno())):
            raise ValueError("WAD changed while reading metadata")
    if identity(before) != identity(path.stat()):
        raise ValueError("WAD changed before completion")
    return {
        "path": str(path.absolute()), "title_id": f"{title:016X}",
        "title_namespace": f"{title >> 32:08X}",
        "internal_version": version, "records": records,
        "tmd_sha256": hashlib.sha256(tmd).hexdigest(),
        "stat_identity": identity(before),
        "content_type": {
            0x00010001: "application",
            0x00010005: "dlc",
        }.get(title >> 32, "UNRESOLVED"),
        "package_integrity_verified": False,
        "signature_verified": False,
        "game_relationship_verified": False,
    }
