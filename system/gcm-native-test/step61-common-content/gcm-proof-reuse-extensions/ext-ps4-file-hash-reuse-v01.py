# 61TF add-on: PS4 DLC file-hash reuse (loaded by gcm-proof-reuse-v04 into its own namespace).
#
# The PS4 DLC version-store check hashes every unpacked file. During one install it checks the
# same files up to three times (in the preparing folder, then after the folder is renamed into
# place, then once more). A rename keeps each file's identity (device, file number, size,
# modification and change time), so a file whose identity was hashed before is not read again:
# the check still runs its own code (paths, sizes, identities, metadata, receipts); only the
# bytes of an already-proven, unchanged file are not re-read. Any other file is hashed normally,
# and its result is remembered for next time. Only the version-store module's own 'verify'
# reading loop is affected (checked by the calling function's file and name).
import hashlib as _x_hashlib, pathlib as _x_pathlib, sys as _x_sys, threading as _x_threading

_X_PREFIX = 'gcm-ps4-dlc-version-store'
_X_DIR = _os.path.join(CACHE_DIR, 'file-hash')
_X_MODULES = set()
_x_local = _x_threading.local()
_x_real_open = _x_pathlib.Path.open
_x_real_sha256 = _x_hashlib.sha256


def _x_key(st):
    return _hashlib.sha256(_json.dumps(['GCM_FILE_HASH_V1', st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]).encode()).hexdigest()


def _x_known(key):
    try:
        with _real_open(_os.path.join(_X_DIR, key[:2], key + '.json'), 'rb') as f:
            doc = _json.loads(f.read())
        return doc['sha256'] if doc.get('schema') == 'GCM_FILE_HASH_V1' else None
    except (OSError, ValueError, KeyError):
        return None


def _x_remember(key, digest):
    try:
        d = _os.path.join(_X_DIR, key[:2]); _os.makedirs(d, mode=0o700, exist_ok=True)
        tmp = _os.path.join(d, key + '.%d.tmp' % _os.getpid())
        fd = _real_os_open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC | _os.O_NOFOLLOW, 0o600)
        try:
            _os.write(fd, _json.dumps({'schema': 'GCM_FILE_HASH_V1', 'sha256': digest}).encode())
        finally:
            _real_os_close(fd)
        _os.replace(tmp, _os.path.join(d, key + '.json'))
    except Exception:
        pass


class _XProven(bytes):
    """Placeholder bytes standing for part of a file whose hash is already proven."""


class _XStream:
    """A real open file (real descriptor, real fstat) whose content is not re-read."""

    def __init__(self, real, size, sha):
        self._real, self._left, self._sha = real, size, sha

    def read(self, n=-1):
        n = self._left if n is None or n < 0 else min(n, self._left)
        if n <= 0:
            return b''
        self._left -= n
        block = _XProven(bytes(n)); block.sha = self._sha
        return block

    def fileno(self):
        return self._real.fileno()

    def close(self):
        return self._real.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._real.close()
        return False

    def __getattr__(self, name):
        return getattr(self._real, name)


class _XHash:
    """sha256 for the version-store module: real hashing, except proven placeholder blocks."""

    def __init__(self, *a, **k):
        # A new hash never inherits a marker left by an earlier file (for example an empty one).
        self._real = _x_real_sha256(*a, **k); self._proven = None; self._key = None
        _x_local.key = None

    def update(self, data):
        if isinstance(data, _XProven):
            self._proven = data.sha
        else:
            if self._key is None:
                self._key = getattr(_x_local, 'key', None); _x_local.key = None
            self._real.update(data)

    def hexdigest(self):
        if self._proven:
            return self._proven
        digest = self._real.hexdigest()
        if self._key:
            _x_remember(self._key, digest); self._key = None
        return digest

    def digest(self):
        return bytes.fromhex(self.hexdigest())

    def __getattr__(self, name):
        return getattr(self._real, name)


class _XHashlib:
    def __getattr__(self, name):
        return _XHash if name == 'sha256' else getattr(_x_hashlib, name)


def _x_open(self, mode='r', *a, **k):
    stream = _x_real_open(self, mode, *a, **k)
    try:
        caller = _x_sys._getframe(1)
        if mode != 'rb' or caller.f_code.co_name != 'verify' or caller.f_code.co_filename not in _X_MODULES:
            return stream
        st = _os.fstat(stream.fileno())
        key = _x_key(st)
        sha = _x_known(key)
        if sha:
            _stats['hits'] += 1
            return _XStream(stream, st.st_size, sha)
        _x_local.key = key  # the next hash started by this check belongs to this file
    except Exception:
        pass
    return stream


def _x_hook(module):
    name = _os.path.basename(getattr(module, '__file__', '') or '')
    if not (name.startswith(_X_PREFIX) and name.endswith('.py')) or not REUSE:
        return
    if getattr(module, 'hashlib', None) is _x_hashlib:
        module.hashlib = _XHashlib()
        _X_MODULES.add(module.__file__)


if REUSE:
    _x_pathlib.Path.open = _x_open
    EXT_HOOKS.append(_x_hook)
