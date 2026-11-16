"""Internal Sony package metadata; no decryption, installation or integrity claim."""
import hashlib
import importlib.util
import os
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def sfo_reader():
    path = ROOT / "gcm-sfo-metadata.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41":
        raise ValueError("Shared SFO reader changed")
    spec = importlib.util.spec_from_file_location("package_sfo_reader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def content_id(raw):
    value = raw.split(b"\0", 1)[0].decode("ascii")
    if not re.fullmatch(r"[A-Z]{2}[0-9]{4}-[A-Z0-9]{9}_[0-9]{2}-[A-Z0-9]{16}", value):
        raise ValueError("Unsupported internal content ID")
    return value

def read(path):
    parser = sfo_reader()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular package required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        header = stream.read(0x440)
        fields = {}
        if header[:4] == b"\x7fPKG":
            if len(header) < 0x60:
                raise ValueError("Truncated Sony PKG header")
            cid = content_id(header[0x30:0x60])
            release, platform, meta_offset, meta_count, meta_size, file_count = \
                struct.unpack_from(">HHIIII", header, 4)
            package_size, data_offset, data_size = struct.unpack_from(">QQQ", header, 0x18)
            if release not in (0, 0x8000) or platform not in (1, 2):
                raise ValueError("Unsupported Sony package platform/release")
            if package_size != before.st_size:
                raise ValueError("Split or size-mismatched Sony package requires support")
            if (not 0 < meta_count <= 4096 or not 8 <= meta_size <= 1024 * 1024
                or meta_offset < 0xc0
                or meta_offset + meta_size > data_offset
                or data_offset > package_size
                or data_size > package_size - data_offset):
                raise ValueError("Invalid Sony package metadata/data bounds")
            stream.seek(meta_offset)
            raw_metadata = stream.read(meta_size)
            if len(raw_metadata) != meta_size:
                raise ValueError("Truncated Sony package metadata")
            position = 0
            seen = set()
            values = {}
            for number in range(meta_count):
                if position + 8 > len(raw_metadata):
                    raise ValueError("Truncated Sony metadata packet")
                ident, length = struct.unpack_from(">II", raw_metadata, position)
                position += 8
                if ident in seen or length > len(raw_metadata) - position:
                    raise ValueError("Duplicate or invalid Sony metadata packet")
                seen.add(ident)
                if ident in (2, 3):
                    if length != 4:
                        raise ValueError("Invalid Sony type/flag packet size")
                    values[ident] = struct.unpack_from(">I", raw_metadata, position)[0]
                position += length
            if 2 not in values or 3 not in values:
                raise ValueError("Internal Sony content type and flags required")
            content_type, flags = values[2], values[3]
            fields = {
                "PKG_PLATFORM": platform, "PKG_RELEASE": release,
                "PKG_CONTENT_TYPE": content_type, "PKG_FLAGS": flags,
            }
            kind = "UNRESOLVED"
            if platform == 1:
                if content_type == 4 and flags & 0x10:
                    kind = "update"
                elif content_type == 5:
                    kind = "application"
            elif content_type == 0x16:
                kind = "dlc"
            elif content_type == 0x15:
                kind = "application"
            family = "SONY_PKG"
        elif header[:4] == b"\x7fCNT":
            if len(header) < 0x440:
                raise ValueError("Truncated PS4 package header")
            cid = content_id(header[0x40:0x70])
            count = struct.unpack_from(">I", header, 0x10)[0]
            table = struct.unpack_from(">I", header, 0x18)[0]
            if not 0 < count <= 65536 or table < 0x440:
                raise ValueError("Invalid PS4 entry table")
            if table + count * 32 > before.st_size:
                raise ValueError("PS4 entry table exceeds package")
            stream.seek(table)
            entries = stream.read(count * 32)
            matches = []
            for index in range(count):
                entry = entries[index * 32:(index + 1) * 32]
                ident = struct.unpack_from(">I", entry)[0]
                offset, size = struct.unpack_from(">II", entry, 16)
                if offset > before.st_size or size > before.st_size - offset:
                    raise ValueError("PS4 entry exceeds package")
                if ident == 0x1000:
                    matches.append((offset, size))
            if len(matches) != 1 or not 20 <= matches[0][1] <= parser.MAX_SIZE:
                raise ValueError("Expected one bounded PS4 SFO")
            offset, size = matches[0]
            stream.seek(offset)
            metadata = stream.read(size)
            fields = parser.parse(metadata)
            if fields.get("CONTENT_ID") not in (None, cid):
                raise ValueError("PS4 SFO and header content IDs disagree")
            if fields.get("TITLE_ID") != cid[7:16]:
                raise ValueError("PS4 SFO and header title IDs disagree")
            category = fields.get("CATEGORY")
            kind = {"gd": "application", "gp": "update",
                    "ac": "dlc", "al": "dlc"}.get(category, "UNRESOLVED")
            family = "PS4_PKG"
        else:
            raise ValueError("Unsupported Sony package header")
        if parser.identity(before) != parser.identity(os.fstat(stream.fileno())):
            raise ValueError("Package changed during metadata reading")
    if parser.identity(before) != parser.identity(path.stat()):
        raise ValueError("Package changed before metadata completion")
    return {
        "path": str(path.absolute()), "container": family,
        "content_id": cid, "title_id": cid[7:16],
        "content_type": kind, "fields": fields,
        "stat_identity": parser.identity(before),
        "metadata_source": "internal-package",
        "package_integrity_verified": False,
        "game_relationship_verified": False,
    }
