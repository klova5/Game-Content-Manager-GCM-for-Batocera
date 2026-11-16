"""Bounded stream integrity primitives; keys remain caller-owned and are never saved."""
import hashlib
import hmac

BLOCK_SIZE = 8 * 1024 * 1024


# Optional measured telemetry; original content checks remain authoritative.
def _gcm_progress():
    import hashlib as _hashlib
    import importlib.util as _importlib
    from pathlib import Path as _Path
    if "_GCM_PROGRESS_MODULE" not in globals():
        _path = _Path('/userdata/system/gcm-native-test/step61-common-content/gcm-global-action-progress-measured-v01.py')
        if _path.is_symlink() or _hashlib.sha256(_path.read_bytes()).hexdigest() != 'd51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
            raise ValueError("Measured progress dependency changed")
        _spec = _importlib.spec_from_file_location("gcm_measured_progress", _path)
        _module = _importlib.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        globals()["_GCM_PROGRESS_MODULE"] = _module
    return globals()["_GCM_PROGRESS_MODULE"]

def bounds(stream, offset, size):
    if type(offset) is not int or type(size) is not int or offset < 0 or size <= 0:
        raise ValueError("Invalid stream region")
    position = stream.tell()
    stream.seek(0, 2)
    end = stream.tell()
    stream.seek(position)
    if offset > end or size > end - offset:
        raise ValueError("Stream region exceeds source")

def expected_digest(value, length):
    if (not isinstance(value, str) or len(value) != length
        or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError("Invalid expected digest")

def hash_region(stream, offset, size, algorithm="sha256", expected=None):
    if algorithm not in ("sha1", "sha256"):
        raise ValueError("Unsupported digest algorithm")
    bounds(stream, offset, size)
    if expected is not None:
        expected_digest(expected, 40 if algorithm == "sha1" else 64)
    stream.seek(offset)
    result = hashlib.new(algorithm)
    remaining = size
    progress = _gcm_progress()
    label = str(getattr(stream, "name", "package")).rsplit("/", 1)[-1]
    phase = "VERIFYING PACKAGE" if offset == 0 and size > 4096 else "VERIFYING PACKAGE DATA"
    progress.publish(phase, label, 0, size)
    while remaining:
        data = stream.read(min(BLOCK_SIZE, remaining))
        if not data:
            raise ValueError("Truncated stream region")
        remaining -= len(data)
        result.update(data)
        progress.publish(phase, label, size - remaining, size)
    digest = result.hexdigest()
    if expected is not None and not hmac.compare_digest(digest, expected):
        raise ValueError("Stream content hash mismatch")
    return digest

def verify_cbc_sha1_region(stream, offset, encrypted_size, plaintext_size,
                           key, iv, expected):
    from Cryptodome.Cipher import AES
    bounds(stream, offset, encrypted_size)
    if (type(plaintext_size) is not int or not 0 < plaintext_size <= encrypted_size
        or encrypted_size % 16
        or not isinstance(key, bytes) or len(key) != 16
        or not isinstance(iv, bytes) or len(iv) != 16):
        raise ValueError("Invalid AES content region")
    expected_digest(expected, 40)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    digest = hashlib.sha1()
    stream.seek(offset)
    remaining = encrypted_size
    clear_remaining = plaintext_size
    while remaining:
        length = min(BLOCK_SIZE, remaining)
        data = stream.read(length)
        if len(data) != length:
            raise ValueError("Truncated encrypted content")
        clear = cipher.decrypt(data)
        used = min(clear_remaining, len(clear))
        digest.update(clear[:used])
        clear_remaining -= used
        remaining -= length
    if clear_remaining or not hmac.compare_digest(digest.hexdigest(), expected):
        raise ValueError("Decrypted content hash mismatch")
    return {
        "plaintext_bytes_verified": plaintext_size,
        "encrypted_bytes_read": encrypted_size,
        "sha1": digest.hexdigest(),
    }
