"""Verify complete unencrypted CIA payloads and TMD hash tables."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("CIA verification dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def verify(path):
    reader = load("gcm-cia-metadata-v02.py",
        "0a6f9554f3a78730b314b5dd5fc87087b74f07c02a43c71d8a1e1575ae4068ab")
    checks = load("gcm-content-stream-checks.py",
        "b18fefd1c1b8645bea59c681beb20fc235a242e5bb72c74322d09fe74a4602e7")
    path = Path(path)
    metadata = reader.read(path)
    records = metadata["records"]
    if any(not item["present"] for item in records):
        raise ValueError("Partial CIA requires separate verification support")
    if any(item["flags"] & 1 for item in records):
        raise ValueError("Encrypted CIA requires separate verification support")
    before = metadata["stat_identity"]
    with path.open("rb") as stream:
        import os
        if reader.identity(os.fstat(stream.fileno())) != before:
            raise ValueError("Opened CIA identity changed")
        tmd_offset, tmd_size = metadata["sections"]["tmd"]
        stream.seek(tmd_offset)
        tmd = stream.read(tmd_size)
        if len(tmd) != tmd_size:
            raise ValueError("CIA TMD truncated")
        body = 0x140
        info = tmd[body + 0xc4:body + 0x9c4]
        expected = tmd[body + 0xa4:body + 0xc4]
        if hashlib.sha256(info).digest() != expected:
            raise ValueError("CIA content-info table hash mismatch")
        chunks = tmd[body + 0x9c4:]
        covered = set()
        for offset in range(0, len(info), 36):
            index = int.from_bytes(info[offset:offset+2], "big")
            count = int.from_bytes(info[offset+2:offset+4], "big")
            if not count:
                continue
            if index + count > len(records):
                raise ValueError("CIA content-info range exceeds records")
            indexes = set(range(index, index + count))
            if covered & indexes:
                raise ValueError("Overlapping CIA content-info ranges")
            covered.update(indexes)
            digest = hashlib.sha256(chunks[index*48:(index+count)*48]).digest()
            if digest != info[offset+4:offset+36]:
                raise ValueError("CIA content-record table hash mismatch")
        if covered != set(range(len(records))):
            raise ValueError("CIA content-info coverage incomplete")
        position, total = metadata["sections"]["content"]
        end = position + total
        verified = []
        for record in records:
            if record["size"] > end - position:
                raise ValueError("CIA content exceeds declared section")
            checks.hash_region(stream, position, record["size"],
                               expected=record["sha256"])
            verified.append({
                "index": record["index"], "size": record["size"],
                "sha256": record["sha256"], "offset": position,
            })
            position += record["size"]
        if position != end:
            raise ValueError("CIA content coverage incomplete")
        digest = checks.hash_region(stream, 0, before[2])
        if reader.identity(os.fstat(stream.fileno())) != before:
            raise ValueError("CIA changed during verification")
    if reader.read(path) != metadata:
        raise ValueError("CIA metadata changed before completion")
    return {
        "status": "PAYLOAD_HASHES_VERIFIED",
        "path": str(path), "title_id": metadata["title_id"],
        "internal_version": metadata["internal_version"],
        "content_type": metadata["content_type"],
        "sha256": digest, "stat_identity": before,
        "verified_records": verified,
        "content_info_hashes_verified": True,
        "full_content_record_set_present": True,
        "signature_authenticity_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
