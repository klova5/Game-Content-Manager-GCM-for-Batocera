"""Wii WAD content hashes; no signature authenticity or parent-game claim."""
import hashlib
import importlib.util
import os
import re
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

def native_common_keys():
    path = ROOT / "step61AC-payload-contracts/IOSC.cpp"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "1050ef0d7caa061ecf5807d4664492001fd12cb52c048a00c9c975fbe281fc11":
        raise ValueError("Pinned native key-selection source changed")
    text = path.read_text()
    retail = text.split("case ConsoleType::Retail:", 1)[1].split("break;", 1)[0]
    def extract(source, handle):
        match = re.search(
            r"m_key_entries\[" + re.escape(handle)
            + r"\]\s*=\s*\{.*?\{\{(.*?)\}\}", source, re.S)
        if match is None:
            raise ValueError("Native common-key initializer unavailable")
        values = re.findall(r"0x([0-9a-fA-F]{2})\b", match.group(1))
        if len(values) != 16:
            raise ValueError("Unexpected native common-key initializer")
        return bytes.fromhex("".join(values))
    return {
        0: extract(retail, "HANDLE_COMMON_KEY"),
        1: extract(text, "HANDLE_NEW_COMMON_KEY"),
    }

def verify(path):
    from Cryptodome.Cipher import AES
    checks = load("gcm-content-stream-checks-measured-v01.py",
        "cbf3f59c36301849234e4a8fcc6ab2546b4520b8c2650e98224042078e5d7a79")
    metadata = load("gcm-wad-metadata-v02.py",
        "4f6fb8cb2d3ef6a9fa9cd7821732118a8c65196199b4b88d4cd75429fa413d1e")
    path = Path(path)
    report = metadata.read(path)
    keys = native_common_keys()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if metadata.identity(before) != report["stat_identity"]:
            raise ValueError("WAD changed since metadata reading")
        header = stream.read(32)
        size, kind, padding, cert, crl, ticket_size, tmd_size, data_size, footer = \
            struct.unpack(">IHHIIIIII", header)
        offset = metadata.align(size)
        sections = {}
        for name, length in (("cert", cert), ("crl", crl),
                             ("ticket", ticket_size), ("tmd", tmd_size),
                             ("data", data_size), ("footer", footer)):
            sections[name] = (offset, length)
            offset = metadata.align(offset + length)
        if not 0x2a4 <= ticket_size <= 65536:
            raise ValueError("Unsupported ticket size")
        stream.seek(sections["ticket"][0])
        ticket = stream.read(ticket_size)
        if len(ticket) != ticket_size or struct.unpack_from(">I", ticket)[0] != 0x10001:
            raise ValueError("Unsupported ticket signature layout")
        issuer = ticket[0x140:0x180].split(b"\0", 1)[0]
        if not issuer.startswith(b"Root-CA00000001-"):
            raise ValueError("Additional console ticket type requires support")
        title_bytes = ticket[0x1dc:0x1e4]
        if title_bytes.hex().upper() != report["title_id"]:
            raise ValueError("Ticket and TMD title IDs disagree")
        declared_key_index = ticket[0x1f1]
        smallest = min(report["records"], key=lambda record: record["size"])
        smallest_offset = sections["data"][0]
        for record in report["records"]:
            if record["index"] == smallest["index"]:
                break
            smallest_offset += metadata.align(record["size"])
        if smallest_offset + metadata.align(smallest["size"]) > sections["data"][0] + data_size:
            raise ValueError("Key-check content exceeds WAD data section")

        def title_key_for(index):
            return AES.new(
                keys[index], AES.MODE_CBC, title_bytes + b"\0" * 8
            ).decrypt(ticket[0x1bf:0x1cf])

        def key_matches(index):
            try:
                checks.verify_cbc_sha1_region(
                    stream, smallest_offset, metadata.align(smallest["size"]),
                    smallest["size"], title_key_for(index),
                    smallest["index"].to_bytes(2, "big") + b"\0" * 14,
                    smallest["sha1"])
            except ValueError:
                return False
            return True

        if declared_key_index in keys and key_matches(declared_key_index):
            key_index = declared_key_index
        elif not any(ticket[4:0x104]):
            matching = [index for index in keys
                        if index != declared_key_index and key_matches(index)]
            if len(matching) != 1:
                raise ValueError("No unique content-verified native key recovery")
            key_index = matching[0]
        else:
            raise ValueError("Ticket key index/content mismatch; recovery not applicable")
        title_key = title_key_for(key_index)
        current = sections["data"][0]
        end = current + data_size
        verified = []
        for record in report["records"]:
            stored_size = metadata.align(record["size"])
            if current + stored_size > end:
                raise ValueError("Referenced content exceeds WAD data section")
            iv = record["index"].to_bytes(2, "big") + b"\0" * 14
            checks.verify_cbc_sha1_region(
                stream, current, stored_size, record["size"],
                title_key, iv, record["sha1"])
            verified.append(record)
            current += stored_size
        if current != end:
            raise ValueError("Unverified extra WAD data bytes")
        package_hash = checks.hash_region(stream, 0, before.st_size)
        if metadata.identity(before) != metadata.identity(os.fstat(stream.fileno())):
            raise ValueError("WAD changed during content verification")
    if metadata.identity(before) != metadata.identity(path.stat()):
        raise ValueError("WAD changed before verification completion")
    return {
        "status": "PAYLOAD_HASHES_VERIFIED",
        "path": str(path.absolute()), "sha256": package_hash,
        "title_id": report["title_id"], "content_type": report["content_type"],
        "internal_version": report["internal_version"],
        "stat_identity": report["stat_identity"],
        "verified_records": verified,
        "ticket_tmd_title_match": True,
        "declared_common_key_index": declared_key_index,
        "content_verified_common_key_index": key_index,
        "native_key_recovery_used": key_index != declared_key_index,
        "decrypted_content_hashes_verified": True,
        "signature_authenticity_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
