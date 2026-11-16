"""Verified DLC filesystem with explicitly preserved encrypted metadata."""
import hashlib
import importlib.util
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-pfs-reader-measured-v01.py":
        "e370b9ada6579776a554079c953ab9028a68f4ef9db36e4f12fae373434b9648",
    "gcm-ps4-pfs-manifest-v02-measured-v01.py":
        "9d0dec20230e208bf18af049bbe4fbdaa014cf08fc41bf3636d60b6886a2297d",
    "gcm-ps4-system-manifest-v02.py":
        "0cb0f67c0be9a2d29e9aca89e6070f6d957366c562af891fb0e51759ed4b3f98",
    "gcm-ps4-combined-manifest-measured-v01.py":
        "8e6ca451abf22dd1a612e85475b1bfaf35a9568326acdd6e863355bfc183ea45",
    "gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py":
        "0fc79563dca135d396b1a9c19e25193c7e85ecdfa6c65ab1497329830d2239fa",
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
    pfs_manifest = load("gcm-ps4-pfs-manifest-v02-measured-v01.py").manifest(reader)
    combined = load("gcm-ps4-combined-manifest-measured-v01.py").merge(
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
    service = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")
    proof = service.verify_game_content(base, source, "ps4")
    if proof["content_type"] != "dlc":
        raise ValueError("Verified game-matching DLC required")
    with load("gcm-ps4-pfs-reader-measured-v01.py").Reader(source) as reader:
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
