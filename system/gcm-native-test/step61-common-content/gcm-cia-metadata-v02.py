"""Bounded CIA metadata reader; no decryption, verification or installation."""
import hashlib
import os
import struct
from pathlib import Path

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def align(value):
    return (value + 63) & ~63

def read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular CIA file required")
    with path.open("rb") as stream:
        before = identity(os.fstat(stream.fileno()))
        header = stream.read(0x2020)
        if len(header) != 0x2020:
            raise ValueError("Truncated CIA header")
        header_size, kind, version, cert, ticket, tmd, meta, content = \
            struct.unpack_from("<IHHIIIIQ", header)
        if header_size != 0x2020:
            raise ValueError("Unsupported CIA header size")
        if kind != 0 or version != 0:
            raise ValueError("Unsupported CIA container type/version")
        if cert > 4 * 1024 * 1024 or not 0 < ticket <= 1024 * 1024:
            raise ValueError("CIA certificate/ticket size outside limits")
        if not 0 < tmd <= 4 * 1024 * 1024 or meta > 1024 * 1024:
            raise ValueError("CIA metadata size outside limits")
        sections = {}
        offset = align(header_size)
        for name, size in (("certificate", cert), ("ticket", ticket),
                           ("tmd", tmd), ("content", content)):
            if offset > before[2] or size > before[2] - offset:
                raise ValueError("CIA section exceeds file: " + name)
            sections[name] = [offset, size]
            offset = align(offset + size)
        if meta:
            if offset > before[2] or meta > before[2] - offset:
                raise ValueError("CIA trailing metadata exceeds file")
            sections["metadata"] = [offset, meta]
        stream.seek(sections["tmd"][0])
        data = stream.read(tmd)
        if len(data) != tmd:
            raise ValueError("CIA TMD truncated")
        # This initial adapter supports the RSA-2048/SHA-256 TMD layout.
        if len(data) < 0x140 + 0x9c4 or struct.unpack_from(">I", data)[0] != 0x10004:
            raise ValueError("Unsupported CIA TMD signature layout")
        body = 0x140
        title = struct.unpack_from(">Q", data, body + 0x4c)[0]
        title_version, count = struct.unpack_from(">HH", data, body + 0x9c)
        if not title or not 1 <= count <= 4096:
            raise ValueError("Invalid CIA title or content count")
        start = body + 0x9c4
        if start + count * 48 != len(data):
            raise ValueError("CIA TMD record bounds mismatch")
        records, ids, indexes, present_indexes = [], set(), set(), set()
        total = 0
        for number in range(count):
            position = start + number * 48
            cid, index, flags, size = struct.unpack_from(">IHHQ", data, position)
            if cid in ids or index in indexes or size <= 0:
                raise ValueError("Invalid or duplicate CIA content record")
            ids.add(cid)
            indexes.add(index)
            present = bool(header[32 + (index >> 3)] & (0x80 >> (index & 7)))
            if present:
                present_indexes.add(index)
                total += size
            records.append({
                "content_id": f"{cid:08x}", "index": index, "flags": flags,
                "size": size, "sha256": data[position+16:position+48].hex(),
                "present": present,
            })
        bitmap_indexes = {
            index for index in range(65536)
            if header[32 + (index >> 3)] & (0x80 >> (index & 7))
        }
        if bitmap_indexes != present_indexes or total != content or not present_indexes:
            raise ValueError("CIA content bitmap and declared sizes disagree")
        if identity(os.fstat(stream.fileno())) != before:
            raise ValueError("CIA changed during metadata reading")
    if identity(path.stat()) != before:
        raise ValueError("CIA changed before completion")
    return {
        "path": str(path), "stat_identity": before,
        "container": "CIA", "title_id": f"{title:016X}",
        "internal_version": title_version, "records": records,
        "sections": sections,
        "metadata_sha256": hashlib.sha256(header + data).hexdigest(),
        "content_type": {
            0x0004000E: "update", 0x0004008C: "dlc",
        }.get(title >> 32, "UNRESOLVED"),
        "payload_hashes_verified": False,
        "signature_authenticity_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
