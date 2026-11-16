"""Bounded PS4 package-format decoding from pinned public references."""
import hashlib
import hmac
from pathlib import Path
import re
import struct

from Cryptodome.Cipher import AES, PKCS1_v1_5
from Cryptodome.PublicKey import RSA
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parent
KEY_SOURCE = ROOT / "step61GO-ps4-format-crypto/src/core/crypto/keys.h"
KEY_SHA256 = "44a738064e208e0349f161afe58bf13e1a8ab7653f09f6f7fd370c9ee6417e2b"

def reference_key(name):
    if name not in ("PkgDerivedKey3Keyset", "FakeKeyset"):
        raise ValueError("Unsupported public format keyset")
    if KEY_SOURCE.is_symlink():
        raise ValueError("Symlink format reference rejected")
    data = KEY_SOURCE.read_bytes()
    if hashlib.sha256(data).hexdigest() != KEY_SHA256:
        raise ValueError("Public format reference changed")
    text = data.decode()
    start = re.search(r"\bclass\s+" + name + r"\b", text)
    if not start:
        raise ValueError("Public format keyset missing")
    following = re.search(r"\bclass\s+", text[start.end():])
    end = start.end() + following.start() if following else len(text)
    body = text[start.end():end]
    values = {}
    for field, length in (
        ("Modulus", 256), ("PublicExponent", 4),
        ("PrivateExponent", 256), ("Prime1", 128), ("Prime2", 128),
    ):
        match = re.search(
            r"\b" + field + r"\b[^=;]*=\s*\{([^}]+)\}",
            body, re.S)
        if not match:
            raise ValueError("Public format constant missing: " + field)
        content = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.S)
        tokens = [token.strip() for token in content.split(",") if token.strip()]
        if len(tokens) != length:
            raise ValueError("Unexpected public format constant length")
        parsed = []
        for token in tokens:
            if re.fullmatch(r"0[xX][0-9a-fA-F]{1,2}", token):
                value = int(token, 16)
            elif re.fullmatch(r"[0-9]{1,3}", token):
                value = int(token, 10)
            else:
                raise ValueError("Unexpected public format byte syntax")
            if not 0 <= value <= 255:
                raise ValueError("Public format byte outside bounds")
            parsed.append(value)
        values[field] = int.from_bytes(bytes(parsed), "big")
    return RSA.construct((
        values["Modulus"], values["PublicExponent"],
        values["PrivateExponent"], values["Prime1"], values["Prime2"],
    ))

def rsa_key(ciphertext, keyset):
    if not isinstance(ciphertext, bytes) or len(ciphertext) != 256:
        raise ValueError("RSA package block must be 256 bytes")
    sentinel = b"invalid-package-key-length-result"
    result = PKCS1_v1_5.new(reference_key(keyset)).decrypt(
        ciphertext, sentinel, expected_pt_len=32)
    if result == sentinel or len(result) != 32:
        raise ValueError("Unsupported or invalid package key block")
    return result

def entry_ivkey(raw_entry, dk3):
    if len(raw_entry) != 32 or len(dk3) != 32 or raw_entry[24:] != bytes(8):
        raise ValueError("Unsupported package entry header")
    return hashlib.sha256(raw_entry + dk3).digest()

def derive(entry_keys, image_entry, image_ciphertext, seed):
    if (
        len(entry_keys) != 2048 or len(image_entry) != 32
        or len(image_ciphertext) != 256 or len(seed) != 16
    ):
        raise ValueError("Invalid package key-record sizes")
    dk3 = rsa_key(entry_keys[1024:1280], "PkgDerivedKey3Keyset")
    ivkey = entry_ivkey(image_entry, dk3)
    image_plaintext = AES.new(
        ivkey[16:], AES.MODE_CBC, ivkey[:16]).decrypt(image_ciphertext)
    ekpfs = rsa_key(image_plaintext, "FakeKeyset")
    material = hmac.new(
        ekpfs, struct.pack("<I", 1) + seed, hashlib.sha256).digest()
    return {"dk3": dk3, "data_key": material[16:], "tweak_key": material[:16]}

def decrypt_entry(raw_entry, ciphertext, dk3):
    declared = struct.unpack_from(">I", raw_entry, 20)[0] if len(raw_entry) == 32 else -1
    if (
        not isinstance(ciphertext, bytes) or declared < 0
        or declared > 4 * 1024 * 1024
        or len(ciphertext) != (declared + 15) // 16 * 16
    ):
        raise ValueError("Invalid bounded encrypted entry")
    ivkey = entry_ivkey(raw_entry, dk3)
    return AES.new(
        ivkey[16:], AES.MODE_CBC, ivkey[:16]).decrypt(ciphertext)[:declared]

def decrypt_pfs(ciphertext, data_key, tweak_key, sector):
    if (
        not isinstance(ciphertext, bytes) or not ciphertext
        or len(ciphertext) > 8 * 1024 * 1024 or len(ciphertext) % 4096
        or len(data_key) != 16 or len(tweak_key) != 16
        or type(sector) is not int or sector < 0
        or sector + len(ciphertext) // 4096 > 1 << 64
    ):
        raise ValueError("Invalid bounded PFS sector request")
    result = bytearray()
    for offset in range(0, len(ciphertext), 4096):
        tweak = struct.pack("<Q", sector + offset // 4096) + bytes(8)
        decoder = Cipher(
            algorithms.AES(data_key + tweak_key), modes.XTS(tweak)).decryptor()
        result.extend(
            decoder.update(ciphertext[offset:offset + 4096]) + decoder.finalize())
    return bytes(result)
