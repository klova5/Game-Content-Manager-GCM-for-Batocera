"""Read-only manifests for PS4 package system-file entries."""
import hashlib
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "step61GN-ps4-complete-format-source/src/core/file_format/pkg_type.cpp"
REFERENCE_SHA256 = "e79609f587681eb61c236a1fe2d038d42116aceb533ad7daecde4be2c38cad6d"

def names():
    if REFERENCE.is_symlink():
        raise ValueError("Symlink entry-name reference rejected")
    data = REFERENCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != REFERENCE_SHA256:
        raise ValueError("Native entry-name reference changed")
    pairs = re.findall(
        r'\{\s*(0x[0-9a-fA-F]+)\s*,\s*"([^"]+)"\s*\}',
        data.decode())
    result = {}
    for identifier, name in pairs:
        identifier = int(identifier, 16)
        if identifier in result:
            raise ValueError("Duplicate native entry-name identifier")
        parts = name.split("/")
        if (
            any(part in ("", ".", "..") for part in parts)
            or any(c in name for c in ("\\", ":", chr(0)))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
        ):
            raise ValueError("Unsafe native system-file name")
        result[identifier] = name
    if len(result) != 611:
        raise ValueError("Unexpected native entry-name mapping")
    return result

def decode(reader, identifier):
    raw, offset, size = reader.entries[identifier]
    flags, flags2 = struct.unpack_from(">II", raw, 8)
    encrypted = bool(flags & 0x80000000)
    key_index = (flags2 >> 12) & 15
    if identifier == 0x20:
        if size != 256:
            raise ValueError("Invalid stored image-key record")
        return reader.raw(offset, size), False
    if size > (4 if encrypted else 8) * 1024 * 1024:
        raise ValueError("System-file entry exceeds bounded decoder")
    if encrypted:
        if identifier not in (0x400, 0x401, 0x402, 0x403):
            raise ValueError("Encrypted system entry requires separate support")
        if key_index != 3:
            raise ValueError(
                "Encrypted entry key index " + str(key_index)
                + " requires separate support")
    stored = (size + 15) // 16 * 16 if encrypted else size
    data = reader.raw(offset, stored)
    if encrypted:
        data = reader.crypto.decrypt_entry(raw, data, reader._keys["dk3"])
    if len(data) != size:
        raise ValueError("Decoded system entry size mismatch")
    return data, encrypted

def manifest(reader):
    mapping = names()
    entries, seen, total = [], set(), 0
    for identifier in sorted(reader.entries):
        data, encrypted = decode(reader, identifier)
        total += len(data)
        if total > 64 * 1024 * 1024:
            raise ValueError("System-file manifest exceeds total size bound")
        relative = "sce_sys/" + mapping.get(identifier, str(identifier))
        if relative.casefold() in seen:
            raise ValueError("Conflicting system-file output paths")
        seen.add(relative.casefold())
        entries.append({
            "path": relative, "kind": "file",
            "entry_id": identifier,
            "encoding": (
                "STORED_KEY_RECORD" if identifier == 0x20
                else "AES_CBC" if encrypted else "RAW"),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if 0x1000 not in reader.entries:
        raise ValueError("Internal package PARAM.SFO required")
    reader.unchanged()
    return {
        "schema": "GCM_PS4_SYSTEM_MANIFEST_V1",
        "package_sha256": reader.proof["sha256"],
        "stat_identity": reader.stat_identity,
        "entries": entries,
        "installation_authorized": False,
    }
