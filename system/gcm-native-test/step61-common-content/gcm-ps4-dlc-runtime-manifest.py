"""Verified DLC filesystem with explicitly preserved encrypted metadata."""
import hashlib
import importlib.util
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-pfs-reader.py":
        "39a40e507935d43e8713091aebd1d56e435dc37126fa4f8e28b9910240ec940b",
    "gcm-ps4-pfs-manifest-v02.py":
        "494b2061bec0e6b7797bfa233b64d938adb70e5651b6ec84b00d13236b2d1b9c",
    "gcm-ps4-system-manifest-v02.py":
        "0cb0f67c0be9a2d29e9aca89e6070f6d957366c562af891fb0e51759ed4b3f98",
    "gcm-ps4-combined-manifest.py":
        "0018d941fc096c9af0a04daeb3f200e2e4294bf503e46e83bf69db106ba34b13",
    "gcm-platform-content-service-v03.py":
        "a17c9940e33ad8f2488523b9fec7cd576d4166008de6e9ec356b45429472cf9c",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("DLC manifest dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def decode_system(reader, identifier, normal_decoder):
    raw, offset, size = reader.entries[identifier]
    flags, flags2 = struct.unpack_from(">II", raw, 8)
    if identifier == 0x401 and flags & 0x80000000 and ((flags2 >> 12) & 15) == 2:
        if size != 512:
            raise ValueError("Unsupported stored license.info representation")
        data = reader.raw(offset, size)
        if len(data) != size:
            raise ValueError("Stored license.info is truncated")
        return data, False
    return normal_decoder(reader, identifier)

def from_reader(reader):
    system = load("gcm-ps4-system-manifest-v02.py")
    original_decode = system.decode
    preserved = []

    def decoder(current_reader, identifier):
        raw, offset, size = current_reader.entries[identifier]
        flags, flags2 = struct.unpack_from(">II", raw, 8)
        if (
            identifier == 0x401 and flags & 0x80000000
            and ((flags2 >> 12) & 15) == 2
        ):
            preserved.append(identifier)
        return decode_system(current_reader, identifier, original_decode)

    system.decode = decoder
    system_manifest = system.manifest(reader)
    for entry in system_manifest["entries"]:
        if entry["entry_id"] in preserved:
            entry["encoding"] = "STORED_ENCRYPTED_METADATA"
    pfs_manifest = load("gcm-ps4-pfs-manifest-v02.py").manifest(reader)
    combined = load("gcm-ps4-combined-manifest.py").merge(
        pfs_manifest, system_manifest)
    combined["runtime_profile"] = "SHADPS4_DIRECTORY_DLC"
    combined["stored_encrypted_metadata"] = [
        entry["path"] for entry in system_manifest["entries"]
        if entry["encoding"] == "STORED_ENCRYPTED_METADATA"
    ]
    combined["all_system_metadata_decrypted"] = not bool(preserved)
    combined["in_game_availability"] = "UNVERIFIED"
    combined["installation_authorized"] = False
    return combined

def manifest(base, source):
    service = load("gcm-platform-content-service-v03.py")
    proof = service.verify_game_content(base, source, "ps4")
    if proof["content_type"] != "dlc":
        raise ValueError("Verified game-matching DLC required")
    with load("gcm-ps4-pfs-reader.py").Reader(source) as reader:
        result = from_reader(reader)
        if result["package_sha256"] != proof["sha256"]:
            raise ValueError("DLC package evidence disagrees")
        reader.unchanged()
    current = service.verify_game_content(base, source, "ps4")
    if current != proof:
        raise ValueError("Game or DLC changed during manifest creation")
    return {
        "status": "GAME_BOUND_DLC_RUNTIME_MANIFEST",
        "game_evidence": proof,
        "manifest": result,
        "installation_authorized": False,
        "in_game_availability": "UNVERIFIED",
    }
