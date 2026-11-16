"""Read-only PS4 PFS access through verified package and bounded decoders."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-format-crypto-v02.py":
        "694878b9b4185c43761209a56c4f9f4d95c80114b722a9f05237b0f87fa47d7a",
    "gcm-pfsc-reader.py":
        "c8d3b0d3f059055abe867f6ddfe719fc83016ba496e6c12e2e83579aee47a38f",
    "gcm-ps4-payload-verify-v02.py":
        "bb3c8e664a4c289e5152b2bf915084097012babacbb12b063b92c7a9c7bcf7d7",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("PFS dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

class Reader:
    def __init__(self, path):
        self.path = Path(path).absolute()
        self.stream = None
        self._keys = None

    def unchanged(self):
        if (
            any(p.is_symlink() for p in (self.path, *self.path.parents))
            or identity(self.path.stat()) != self.stat_identity
            or identity(os.fstat(self.stream.fileno())) != self.stat_identity
        ):
            raise ValueError("Package changed during PFS access")

    def raw(self, offset, size):
        if (
            type(offset) is not int or type(size) is not int
            or offset < 0 or size < 0 or size > 8 * 1024 * 1024
            or offset + size > self.stat_identity[2]
        ):
            raise ValueError("Package read outside bounds")
        self.stream.seek(offset)
        data = self.stream.read(size)
        if len(data) != size:
            raise ValueError("Truncated package read")
        return data

    def decrypted(self, offset, size):
        if (
            type(offset) is not int or type(size) is not int
            or offset < 0 or size < 0
            or offset + size > self.image_size
        ):
            raise ValueError("PFS read outside image")
        if size == 0:
            return b""
        start = offset // 4096 * 4096
        end = (offset + size + 4095) // 4096 * 4096
        if end > self.image_size or end - start > 8 * 1024 * 1024:
            raise ValueError("Aligned PFS read outside bounds")
        data = self.crypto.decrypt_pfs(
            self.raw(self.image_offset + start, end - start),
            self._keys["data_key"], self._keys["tweak_key"], start // 4096)
        return data[offset - start:offset - start + size]

    def __enter__(self):
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise ValueError("Symlink package path rejected")
        self.crypto = load("gcm-ps4-format-crypto-v02.py")
        self.proof = load("gcm-ps4-payload-verify-v02.py").verify(self.path)
        if self.proof.get("status") != "PAYLOAD_HASHES_VERIFIED":
            raise ValueError("Fresh package verification required")
        self.stat_identity = self.proof["stat_identity"]
        self.stream = self.path.open("rb")
        try:
            self.unchanged()
            header = self.raw(0, 4096)
            count = struct.unpack_from(">I", header, 0x10)[0]
            table = struct.unpack_from(">I", header, 0x18)[0]
            if not 1 <= count <= 65536:
                raise ValueError("Invalid package entry count")
            self.entries = {}
            for offset in range(0, count * 32, 32):
                raw = self.raw(table + offset, 32)
                identifier, _, _, _, position, size, padding = struct.unpack(">IIIIIIQ", raw)
                if identifier in self.entries or padding != 0:
                    raise ValueError("Unsupported or duplicate package entry")
                if position + size > self.stat_identity[2]:
                    raise ValueError("Package entry outside bounds")
                self.entries[identifier] = (raw, position, size)
            key_entry = self.entries[0x10]
            image_entry = self.entries[0x20]
            if key_entry[2] != 2048 or image_entry[2] != 256:
                raise ValueError("Unsupported package key-record sizes")
            self.image_offset, self.image_size = struct.unpack_from(">QQ", header, 0x410)
            cache = struct.unpack_from(">I", header, 0x43c)[0]
            if (
                self.image_size < 4096 or self.image_size % 4096
                or self.image_offset + self.image_size > self.stat_identity[2]
            ):
                raise ValueError("Invalid bounded PFS image")
            self._keys = self.crypto.derive(
                self.raw(key_entry[1], key_entry[2]), image_entry[0],
                self.raw(image_entry[1], image_entry[2]),
                self.raw(self.image_offset + 0x370, 16))
            length = min(cache * 2, self.image_size, 8 * 1024 * 1024)
            length -= length % 4096
            prefix = self.decrypted(0, length)
            matches = [
                offset for offset in range(0x20000, len(prefix) - 47, 0x10000)
                if prefix[offset:offset + 4] == b"PFSC"
            ]
            if len(matches) != 1:
                raise ValueError("Unique bounded PFSC header required")
            self.pfsc_offset = matches[0]
            self.pfsc = load("gcm-pfsc-reader.py").Reader(
                lambda offset, size: self.decrypted(self.pfsc_offset + offset, size),
                self.image_size - self.pfsc_offset)
            self.unchanged()
            return self
        except BaseException:
            self.stream.close()
            self.stream = None
            self._keys = None
            raise

    def read(self, offset, size):
        self.unchanged()
        data = self.pfsc.read(offset, size)
        self.unchanged()
        return data

    def __exit__(self, exc_type, exc, traceback):
        try:
            self.unchanged()
        finally:
            self.stream.close()
            self.stream = None
            self._keys = None
        return False
