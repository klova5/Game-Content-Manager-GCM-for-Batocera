"""PS4 stored-digest checks; no signature authenticity or install authorization."""
import hashlib
import importlib.util
import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Verification dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def verify(path):
    checks = load("gcm-content-stream-checks.py",
        "b18fefd1c1b8645bea59c681beb20fc235a242e5bb72c74322d09fe74a4602e7")
    metadata = load("gcm-sony-package-metadata.py",
        "9955f47fd8c7adc812233ff49d885e066bf9cea37d7e8519f69717c1905d186e")
    path = Path(path)
    report = metadata.read(path)
    if report["container"] != "PS4_PKG":
        raise ValueError("PS4 package required")
    def identity(value):
        return [value.st_dev, value.st_ino, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns]
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if identity(before) != report["stat_identity"]:
            raise ValueError("Package changed since metadata reading")
        header = stream.read(0x1000)
        if len(header) != 0x1000:
            raise ValueError("Truncated package header")
        package_size = struct.unpack_from(">Q", header, 0x430)[0]
        if package_size != before.st_size:
            raise ValueError("Declared package size differs from file")
        checks.hash_region(stream, 0, 0xfe0,
                           expected=header[0xfe0:0x1000].hex())
        body_offset, body_size = struct.unpack_from(">QQ", header, 0x20)
        if body_offset < 0x1000:
            raise ValueError("Invalid body region")
        checks.hash_region(stream, body_offset, body_size,
                           expected=header[0x160:0x180].hex())
        pfs_offset, pfs_size = struct.unpack_from(">QQ", header, 0x410)
        content_type = struct.unpack_from(">I", header, 0x74)[0]
        pfs_verified = False
        if pfs_size:
            if pfs_offset < body_offset + body_size:
                raise ValueError("Overlapping body and PFS regions")
            checks.hash_region(stream, pfs_offset, pfs_size,
                               expected=header[0x440:0x460].hex())
            pfs_verified = True
        elif content_type != 0x1c:
            raise ValueError("Required PFS image is absent")

        count = struct.unpack_from(">I", header, 0x10)[0]
        table_offset = struct.unpack_from(">I", header, 0x18)[0]
        if not 0 < count <= 65536:
            raise ValueError("Invalid entry count")
        checks.bounds(stream, table_offset, count * 32)
        if not body_offset <= table_offset or table_offset + count * 32 > body_offset + body_size:
            raise ValueError("Entry table outside verified body")
        stream.seek(table_offset)
        table = stream.read(count * 32)
        entries = []
        for number in range(count):
            entry = table[number * 32:(number + 1) * 32]
            ident = struct.unpack_from(">I", entry)[0]
            flags = struct.unpack_from(">I", entry, 8)[0]
            offset, size = struct.unpack_from(">II", entry, 16)
            stored_size = (size + 15) & ~15 if flags & 0x80000000 else size
            if offset > before.st_size or stored_size > before.st_size - offset:
                raise ValueError("Stored entry exceeds package")
            if stored_size and not (
                body_offset <= offset
                and offset + stored_size <= body_offset + body_size
            ):
                raise ValueError("Stored entry outside verified body")
            entries.append((ident, offset, stored_size))
        digest_entries = [item for item in entries if item[0] == 1]
        if len(digest_entries) != 1:
            raise ValueError("One entry digest table required")
        _, digest_offset, digest_size = digest_entries[0]
        if digest_size < count * 32 or digest_size > 4 * 1024 * 1024:
            raise ValueError("Entry digest table outside bounds")
        checks.hash_region(stream, digest_offset, digest_size,
                           expected=header[0x140:0x160].hex())
        stream.seek(digest_offset)
        digests = stream.read(digest_size)
        if entries[0][0] != 1:
            raise ValueError("Unsupported digest-table ordering")
        for number, (ident, offset, size) in enumerate(entries[1:], 1):
            expected = digests[number * 32:(number + 1) * 32].hex()
            if size:
                checks.hash_region(stream, offset, size, expected=expected)
            elif expected != hashlib.sha256(b"").hexdigest():
                raise ValueError("Empty entry digest mismatch")
        package_hash = checks.hash_region(stream, 0, before.st_size)
        if identity(before) != identity(os.fstat(stream.fileno())):
            raise ValueError("Package changed during payload verification")
    if identity(before) != identity(path.stat()):
        raise ValueError("Package changed before verification completion")
    return {
        "status": "PAYLOAD_HASHES_VERIFIED",
        "path": str(path.absolute()), "sha256": package_hash,
        "title_id": report["title_id"], "content_id": report["content_id"],
        "content_type": report["content_type"],
        "version": report["fields"].get("APP_VER", ""),
        "stat_identity": report["stat_identity"],
        "header_digest_verified": True,
        "body_digest_verified": True,
        "entry_digests_verified": True,
        "pfs_digest_verified": pfs_verified,
        "signature_authenticity_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
