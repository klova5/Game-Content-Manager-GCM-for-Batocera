"""Read-only decrypted PS3 retail entry manifest, bound to container integrity."""
import hashlib
import importlib.util
import os
import re
import struct
from pathlib import Path, PurePosixPath
from Cryptodome.Cipher import AES

ROOT = Path(__file__).resolve().parent

def checker():
    path = ROOT / "gcm-ps3-container-check.py"
    expected = "5006cf34b54d96b9171e2c679d3e04f79da1871f08708236704b58e944c9c38b"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("PS3 container checker changed")
    spec = importlib.util.spec_from_file_location("ps3_container", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def native_key():
    path = ROOT / "step61DW-ps3-format-constants/key_vault.h"
    expected = "d5d0cede74c7900bb23ca8d60066194648ed4b976eaee60315c0bcbed7dc2aaf"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Native format constants changed")
    match = re.search(r"\bPKG_AES_KEY\s*\[[^\]]*\]\s*=\s*\{([^}]+)\}",
                      path.read_text(), re.S)
    if not match:
        raise ValueError("Native retail constant unavailable")
    values = re.findall(r"0x([0-9a-fA-F]{2})\b", match.group(1))
    if len(values) != 16:
        raise ValueError("Unexpected native constant length")
    return bytes.fromhex("".join(values))

def manifest(path):
    path = Path(path)
    guard = checker()
    proof = guard.check(path)
    key = native_key()
    with path.open("rb") as stream:
        if [getattr(os.fstat(stream.fileno()), name) for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")] != proof["stat_identity"]:
            raise ValueError("Opened package changed")
        header = stream.read(0x80)
        release, platform = struct.unpack_from(">HH", header, 4)
        count = struct.unpack_from(">I", header, 0x14)[0]
        data_offset, data_size = struct.unpack_from(">QQ", header, 0x20)
        nonce = int.from_bytes(header[0x70:0x80], "big")
        if release != 0x8000 or platform != 1:
            raise ValueError("Only ordinary PS3 retail packages supported")
        if not 1 <= count <= 100000 or count * 32 > data_size:
            raise ValueError("Entry table outside supported bounds")

        def cipher_at(offset, size):
            if offset < 0 or size < 0 or offset > data_size or size > data_size - offset:
                raise ValueError("Entry range exceeds package data")
            counter = nonce + offset // 16
            skip = offset % 16
            if counter + (skip + size + 15) // 16 > 1 << 128:
                raise ValueError("Package counter overflow")
            cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=counter)
            if skip:
                cipher.decrypt(bytes(skip))
            stream.seek(data_offset + offset)
            return cipher

        def decode(offset, size):
            if size > 4 * 1024 * 1024:
                raise ValueError("Metadata allocation exceeds limit")
            cipher = cipher_at(offset, size)
            data = stream.read(size)
            if len(data) != size:
                raise ValueError("Package entry truncated")
            return cipher.decrypt(data)

        table = decode(0, count * 32)
        entries, seen, ranges = [], set(), []
        for number in range(count):
            name_offset, name_size, offset, size, flags, padding = \
                struct.unpack_from(">IIQQII", table, number * 32)
            kind = flags & 0xff
            if padding or flags & ~0x800000ff or kind not in (1, 3, 4):
                raise ValueError("Unsupported PS3 entry flags/type")
            if not 1 <= name_size <= 256 or name_offset < count * 32:
                raise ValueError("Invalid PS3 entry name bounds")
            name = decode(name_offset, name_size).rstrip(b"\0").decode("utf-8")
            parts = name.split("/")
            if (not name or "\\" in name or ":" in name
                    or any(part in ("", ".", "..") for part in parts)
                    or any(ord(char) < 32 or ord(char) == 127 for char in name)
                    or PurePosixPath(name).is_absolute()
                    or name.casefold() in seen):
                raise ValueError("Unsafe or duplicate PS3 entry path")
            seen.add(name.casefold())
            if kind == 4:
                if size:
                    raise ValueError("Unexpected nonempty directory entry")
                entries.append({"path": name, "kind": "directory"})
                continue
            if offset < count * 32:
                raise ValueError("File overlaps entry table")
            cipher = cipher_at(offset, size)
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                block = stream.read(min(8 * 1024 * 1024, remaining))
                if not block:
                    raise ValueError("Package file truncated")
                digest.update(cipher.decrypt(block))
                remaining -= len(block)
            if size:
                ranges.append((offset, offset + size))
            entries.append({
                "path": name, "kind": "file", "size": size,
                "sha256": digest.hexdigest(), "entry_type": kind,
            })
        ordered = sorted(ranges)
        if any(left[1] > right[0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Overlapping file data requires separate support")
    if guard.check(path) != proof:
        raise ValueError("Package changed during decrypted-entry verification")
    return {
        "schema": "GCM_PS3_DECRYPTED_ENTRY_MANIFEST_V1",
        "content_type": proof["content_type"],
        "title_id": proof["title_id"], "source": str(path),
        "source_sha256": proof["sha256"],
        "source_stat_identity": proof["stat_identity"],
        "container_footer_verified": True,
        "entries": entries,
        "signature_authenticity_verified": False,
        "installation_authorized": False,
    }
