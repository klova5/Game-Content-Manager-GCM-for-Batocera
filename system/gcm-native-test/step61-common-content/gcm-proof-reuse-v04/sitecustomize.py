"""GCM read profiler, proof reuse (61TC), stable file identity on exFAT/FAT drives (61TD) and reuse
rule add-ons (61TF).
Loaded through PYTHONPATH by the GCM frontend.

Active only in Python processes started for GCM scripts (an argument under /gcm-native-test/);
every other process (emulator launchers, configgen) only gets the system sitecustomize, if any.
It records, for each file of 64 MB or more opened for reading, how many bytes were read and
from where (the innermost GCM call sites). Nothing is changed in how files are read; logging
failures (for example inside a read-only sandbox) are ignored.
Log: /userdata/system/gcm-native-test/step61-common-content/state/read-profile/<day>.jsonl

Proof reuse: the results of a fixed list of pure checking functions (they only read files and
return a result) are kept in state/proof-cache, keyed by the exact source of the checking code,
the function, and the on-disk identity of every file argument (device, inode, size, modification
and change time; small files by content). An unchanged file is never read again by those checks,
in any GCM process or after a reboot. A changed file, a changed checker or any error means the
check runs normally. GCM_PROOF_REUSE_OFF=1 disables reuse (profiling stays on).

Stable identity (61TD): exFAT/FAT drives have no file numbers and Linux hands out new ones after
the file cache is cleared. In GCM processes, os.stat/lstat/fstat give files on such drives a file
number derived from their place on the drive (followed through renames made by the same process).
GCM_IDENTITY_FIX_OFF=1 disables it.

Add-ons (61TF): ext-*.py files in step61-common-content/gcm-proof-reuse-extensions are loaded in name
order into this module, so new reuse rules for pure checking functions can be added without a frontend
update. They may extend TABLE or add EXT_HOOKS (called with every GCM module after it loads). A failing
add-on is skipped. GCM_PROOF_EXT_OFF=1 disables add-ons.
"""
import os as _os
import sys as _sys


def _chain_system_sitecustomize():
    import importlib.machinery, importlib.util
    here = _os.path.dirname(_os.path.abspath(__file__))
    path = [p for p in _sys.path if p and _os.path.abspath(p) != here]
    spec = importlib.machinery.PathFinder.find_spec('sitecustomize', path)
    if spec and spec.origin and _os.path.abspath(spec.origin) != _os.path.abspath(__file__):
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


try:
    _chain_system_sitecustomize()
except Exception:
    pass

MIN_SIZE = 64 * 1024 * 1024
LOG_DIR = _os.environ.get('GCM_READ_PROFILE_DIR') or '/userdata/system/gcm-native-test/step61-common-content/state/read-profile'
_ARGS = list(getattr(_sys, 'orig_argv', _sys.argv) or [])
ACTIVE = any('/gcm-native-test/' in str(a) for a in _ARGS) and not _os.environ.get('GCM_READ_PROFILE_OFF')

# ---------------- stable file identity on exFAT/FAT drives (61TD) ----------------
# exFAT and FAT have no file numbers. Linux drivers (exfat-fuse, the kernel exfat/vfat drivers)
# invent one per cached file and hand out a new one after the cache is cleared, so an untouched
# package looked "changed" to GCM identity checks. In GCM processes only, files on such drives get
# a file number derived from their location on the drive (kept across renames done by this
# process); device, size and times are the real ones, so a real change is still detected.
IDENT_ACTIVE = (any('/gcm-native-test/' in str(a) for a in _ARGS) or bool(_os.environ.get('GCM_IDENTITY_TEST_TYPES'))) \
    and not _os.environ.get('GCM_IDENTITY_FIX_OFF')

if IDENT_ACTIVE:
    import hashlib as _ih, threading as _ithreading

    _UNSTABLE_TYPES = {'exfat', 'vfat', 'msdos', 'fat'} | set(filter(None, (_os.environ.get('GCM_IDENTITY_TEST_TYPES') or '').split(',')))
    _raw_stat, _raw_lstat, _raw_fstat = _os.stat, _os.lstat, _os.fstat
    _raw_rename, _raw_replace = _os.rename, _os.replace
    _raw_dev_open, _raw_dev_read, _raw_dev_close = _os.open, _os.read, _os.close
    _iguard = _ithreading.local()
    _raw_file_open = open
    _devs = {}        # st_dev -> list of (mountpoint, fs_root) when unstable, [] when stable
    _realdirs = {}
    _aliases = {}     # (st_dev, path on the drive) -> origin path on the drive (renames by this process)
    _fresh = [0]

    def _signature_unstable(source):
        """fuseblk: decide from the drive's own boot sector (exFAT or FAT -> unstable)."""
        try:
            fd = _raw_dev_open(source, _os.O_RDONLY | getattr(_os, 'O_CLOEXEC', 0))
        except OSError:
            return False
        try:
            head = _raw_dev_read(fd, 512)
        except OSError:
            return False
        finally:
            _raw_dev_close(fd)
        return head[3:11] == b'EXFAT   ' or head[54:59] in (b'FAT12', b'FAT16') or head[82:87] == b'FAT32'

    def _mounts_for(dev):
        found = _devs.get(dev)
        if found is not None:
            return found
        want = '%d:%d' % (_os.major(dev), _os.minor(dev))
        rows = []
        try:
            with _raw_file_open('/proc/self/mountinfo', 'rb') as f:
                text = f.read().decode('utf-8', 'surrogateescape')
        except OSError:
            text = ''

        def unescape(s):
            return s.replace('\\040', ' ').replace('\\011', '\t').replace('\\012', '\n').replace('\\134', '\\')
        for line in text.splitlines():
            left, sep, right = line.partition(' - ')
            a, b = left.split(), right.split()
            if not sep or len(a) < 5 or len(b) < 2 or a[2] != want:
                continue
            fstype, source = b[0], unescape(b[1])
            unstable = fstype in _UNSTABLE_TYPES or (fstype == 'fuseblk' and _signature_unstable(source))
            if unstable:
                rows.append((unescape(a[4]).rstrip('/') or '/', unescape(a[3]).rstrip('/')))
        rows.sort(key=lambda r: -len(r[0]))
        _devs[dev] = rows
        return rows

    def _realdir(d):
        r = _realdirs.get(d)
        if r is None:
            r = _os.path.realpath(d)
            if len(_realdirs) > 4096:
                _realdirs.clear()
            _realdirs[d] = r
        return r

    def _canonical(path, follow, dir_fd):
        if isinstance(path, int):
            p = _os.readlink('/proc/self/fd/%d' % path)
            return None if p.endswith(' (deleted)') or not p.startswith('/') else p
        p = _os.fsdecode(_os.fspath(path))
        if dir_fd is not None and not p.startswith('/'):
            base = _os.readlink('/proc/self/fd/%d' % dir_fd)
            p = _os.path.join(base, p)
        if not p.startswith('/'):
            p = _os.path.join(_os.getcwd(), p)
        head, tail = _os.path.split(p.rstrip('/') or '/')
        if tail in ('', '.', '..'):
            return _os.path.realpath(p)
        if follow and _stat_mod.S_ISLNK(_raw_lstat(p).st_mode):
            return _os.path.realpath(p)
        return _os.path.join(_realdir(head), tail)

    def _origin(dev, rel):
        """Alias values are already final origins: one substitution of the longest renamed prefix."""
        if not _aliases:
            return rel
        parts = rel.split('/')
        for i in range(len(parts), 0, -1):
            pre = '/'.join(parts[:i])
            hit = _aliases.get((dev, pre))
            if hit is not None:
                return hit + rel[len(pre):]
        return rel

    def _on_drive(dev, canonical):
        for mountpoint, fs_root in _mounts_for(dev):
            if canonical == mountpoint or canonical.startswith(mountpoint.rstrip('/') + '/'):
                return fs_root + canonical[len(mountpoint.rstrip('/')):]
        return None

    def _fix(st, path, follow=True, dir_fd=None):
        if getattr(_iguard, 'busy', False) or not _mounts_for(st.st_dev):
            return st
        _iguard.busy = True
        try:
            rel = _on_drive(st.st_dev, _canonical(path, follow, dir_fd))
            if rel is None or rel in ('', '/'):
                return st
            origin = _origin(st.st_dev, rel)
            ino = int.from_bytes(_ih.sha256(b'GCM-STABLE-INO-V1\0' + origin.encode('utf-8', 'surrogateescape')).digest()[:8], 'big')
            ino = (ino & ((1 << 62) - 1)) | (1 << 62)
            cls, (seq, extra) = st.__reduce__()[:2]
            seq = list(seq); seq[1] = ino
            return cls(seq, extra)
        except Exception:
            return st
        finally:
            _iguard.busy = False

    def _stat_fixed(path, *, dir_fd=None, follow_symlinks=True):
        st = _raw_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        return _fix(st, path, follow_symlinks, dir_fd)

    def _lstat_fixed(path, *, dir_fd=None):
        st = _raw_lstat(path, dir_fd=dir_fd)
        return _fix(st, path, False, dir_fd)

    def _fstat_fixed(fd):
        st = _raw_fstat(fd)
        return _fix(st, fd)

    def _drive_path(path, dir_fd, must_exist):
        """(st_dev, path on drive) for a rename end, or None when not on an unstable drive."""
        try:
            if must_exist:
                st = _raw_lstat(path, dir_fd=dir_fd)
                dev = st.st_dev
            else:
                p = _os.fsdecode(_os.fspath(path))
                dev = _raw_stat(_os.path.dirname(p.rstrip('/')) or '.', dir_fd=dir_fd).st_dev
            if not _mounts_for(dev):
                return None
            _iguard.busy = True
            try:
                rel = _on_drive(dev, _canonical(path, False, dir_fd))
            finally:
                _iguard.busy = False
            return None if rel in (None, '', '/') else (dev, rel)
        except Exception:
            return None

    def _note_rename(src, dst):
        if not src or not dst or src[0] != dst[0]:
            return
        dev, s, d = src[0], src[1], dst[1]
        origin = _origin(dev, s)
        moved = {}
        for (k_dev, k), v in list(_aliases.items()):
            if k_dev != dev:
                continue
            if k == d or k.startswith(d + '/'):
                del _aliases[(k_dev, k)]
            elif k.startswith(s + '/'):
                del _aliases[(k_dev, k)]
                moved[(dev, d + k[len(s):])] = v
        _aliases.update(moved)
        _aliases[(dev, d)] = origin
        _fresh[0] += 1
        _aliases[(dev, s)] = '\0fresh-%d-%d-%s' % (_os.getpid(), _fresh[0], s)
        _realdirs.clear()

    def _make_rename(raw):
        def renamed(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
            a = _drive_path(src, src_dir_fd, True)
            raw(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            if a is not None:
                try:
                    _note_rename(a, _drive_path(dst, dst_dir_fd, True))
                except Exception:
                    pass
        renamed.__name__ = raw.__name__
        renamed.__doc__ = raw.__doc__
        return renamed

    import stat as _stat_mod
    _os.stat, _os.lstat, _os.fstat = _stat_fixed, _lstat_fixed, _fstat_fixed
    _os.rename, _os.replace = _make_rename(_raw_rename), _make_rename(_raw_replace)


if ACTIVE:
    import builtins as _builtins, io as _io, json as _json, time as _time, traceback as _tb, atexit as _atexit, stat as _stat

    _real_open = _builtins.open
    _real_os_open, _real_os_read, _real_os_close = _os.open, _os.read, _os.close
    _records = []
    _fds = {}
    _script = next((str(a) for a in _ARGS if str(a).endswith('.py')), ' '.join(map(str, _ARGS[:3])))

    def _sites():
        out = []
        here = _os.path.abspath(__file__)
        for f in reversed(_tb.extract_stack()[:-2]):
            if _os.path.abspath(f.filename) == here:  # 61TF: skip this module's own wrappers
                continue
            if '/gcm-native-test/' in f.filename or f.filename == '<string>':
                out.append('%s:%s:%d' % (_os.path.basename(f.filename), f.name, f.lineno))
            if len(out) == 4:
                break
        return out

    def _big(path_or_fd):
        try:
            st = _os.stat(path_or_fd)
            return _stat.S_ISREG(st.st_mode) and st.st_size >= MIN_SIZE, st
        except (OSError, TypeError, ValueError):
            return False, None

    def _new(path, st):
        rec = {'path': str(path), 'size': st.st_size, 'read': 0, 'sites': _sites(), 't': _time.time()}
        _records.append(rec)
        return rec

    class _Counted:
        __slots__ = ('_f', '_r')

        def __init__(self, f, rec):
            object.__setattr__(self, '_f', f); object.__setattr__(self, '_r', rec)

        def read(self, *a):
            data = self._f.read(*a); self._r['read'] += len(data) if data else 0; return data

        def read1(self, *a):
            data = self._f.read1(*a); self._r['read'] += len(data) if data else 0; return data

        def readinto(self, b):
            n = self._f.readinto(b); self._r['read'] += n or 0; return n

        def readinto1(self, b):
            n = self._f.readinto1(b); self._r['read'] += n or 0; return n

        def readline(self, *a):
            data = self._f.readline(*a); self._r['read'] += len(data); return data

        def __iter__(self):
            for line in self._f:
                self._r['read'] += len(line); yield line

        def __enter__(self):
            self._f.__enter__(); return self

        def __exit__(self, *a):
            return self._f.__exit__(*a)

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __setattr__(self, name, value):
            setattr(self._f, name, value)

    def _open(file, mode='r', *a, **k):
        f = _real_open(file, mode, *a, **k)
        try:
            if isinstance(file, int) and file in _fds and 'b' in mode:
                return _Counted(f, _fds.pop(file))
            if 'r' in mode and 'b' in mode and '+' not in mode and not isinstance(file, int):
                big, st = _big(file)
                if big:
                    return _Counted(f, _new(file, st))
        except Exception:
            pass
        return f

    def _os_open(path, flags, *a, **k):
        fd = _real_os_open(path, flags, *a, **k)
        try:
            if flags & 3 == _os.O_RDONLY:
                big, st = _big(fd)
                if big:
                    _fds[fd] = _new(path, st)
        except Exception:
            pass
        return fd

    def _os_read(fd, n):
        data = _real_os_read(fd, n)
        rec = _fds.get(fd)
        if rec is not None:
            rec['read'] += len(data)
        return data

    def _os_close(fd):
        _fds.pop(fd, None)
        return _real_os_close(fd)

    def _flush():
        rows = [r for r in _records if r['read'] >= MIN_SIZE // 2]
        if not rows:
            return
        try:
            _os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
            day = _time.strftime('%Y%m%d')
            line = ''.join(_json.dumps(dict(r, pid=_os.getpid(), script=_os.path.basename(_script))) + '\n' for r in rows)
            fd = _real_os_open(_os.path.join(LOG_DIR, day + '.jsonl'), _os.O_WRONLY | _os.O_APPEND | _os.O_CREAT | _os.O_NOFOLLOW, 0o600)
            try:
                _os.write(fd, line.encode())
            finally:
                _real_os_close(fd)
        except Exception:
            pass

    _builtins.open = _open
    _io.open = _open
    _os.open, _os.read, _os.close = _os_open, _os_read, _os_close
    _atexit.register(_flush)

    # ---------------- proof reuse (61TC) ----------------
    import hashlib as _hashlib, functools as _functools, copy as _copy
    from importlib import _bootstrap_external as _be

    CACHE_DIR = _os.environ.get('GCM_PROOF_CACHE_DIR') or '/userdata/system/gcm-native-test/step61-common-content/state/proof-cache'
    REUSE = not _os.environ.get('GCM_PROOF_REUSE_OFF')
    SMALL = 16 * 1024 * 1024
    # module file name (prefix match) -> functions whose results are cached. Only pure checks:
    # they read files and return a result; none of them writes anything outside temporary folders.
    TABLE = (
        ('citron-package-verify-v03.py', ('digest', 'verify')),
        ('gcm-v35-verified-install', ('sha',)),
        ('gcm-ps4-payload-verify', ('verify',)),
    )
    REPLAY = {'gcm-v35-internal-integrity.py': ('--nsp', '--base-cnmt', '--patch-cnmt', '--meta-nca')}
    _stats = {'hits': 0, 'misses': 0}

    def _boot():
        try:
            return _real_open('/proc/sys/kernel/random/boot_id').read().strip()
        except OSError:
            return ''

    def _file_key(value, with_path=True):
        """Identity of a file or folder argument, or None when the value is not an existing path."""
        if isinstance(value, (bytes, bytearray)) or not isinstance(value, (str, _os.PathLike)):
            return None
        text = _os.fspath(value)
        if not isinstance(text, str) or not text.startswith('/') or len(text) > 4096:
            return None
        try:
            st = _os.stat(text)
        except (OSError, ValueError):
            return None
        if _stat.S_ISREG(st.st_mode):
            if st.st_size <= SMALL:
                h = _hashlib.sha256()
                with _real_open(text, 'rb') as f:
                    h.update(f.read())
                return ['small', _os.path.realpath(text) if with_path else '', h.hexdigest()]
            return ['file', _os.path.realpath(text), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]
        if _stat.S_ISDIR(st.st_mode):
            rows = []
            for root, dirs, files in _os.walk(text):
                dirs.sort()
                for name in sorted(files):
                    p = _os.path.join(root, name)
                    s = _os.lstat(p)
                    rows.append([_os.path.relpath(p, text), s.st_mode, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns])
            return ['dir', _os.path.realpath(text), st.st_dev, _hashlib.sha256(_json.dumps(rows).encode()).hexdigest()]
        return None

    def _norm(value):
        key = _file_key(value)
        if key is not None:
            return key
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [_norm(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _norm(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
        raise TypeError('uncacheable argument')

    def _path(key):
        return _os.path.join(CACHE_DIR, key[:2], key + '.json')

    def _load(key):
        try:
            with _real_open(_path(key), 'rb') as f:
                return _json.loads(f.read())
        except (OSError, ValueError):
            return None

    def _store(key, doc):
        try:
            d = _os.path.dirname(_path(key)); _os.makedirs(d, mode=0o700, exist_ok=True)
            tmp = _path(key) + '.%d.tmp' % _os.getpid()
            fd = _real_os_open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL | _os.O_NOFOLLOW, 0o600)
            try:
                _os.write(fd, _json.dumps(doc).encode())
            finally:
                _real_os_close(fd)
            _os.replace(tmp, _path(key))
        except Exception:
            pass

    def _reboot(value, old, new):
        if isinstance(value, dict):
            return {k: (new if k == 'boot_id' and v == old else _reboot(v, old, new)) for k, v in value.items()}
        if isinstance(value, list):
            return [_reboot(v, old, new) for v in value]
        return value

    def _wrap(function, source_sha, name):
        @_functools.wraps(function)
        def cached(*args, **kwargs):
            if not REUSE:
                return function(*args, **kwargs)
            try:
                ident = _norm([list(args), kwargs])
                key = _hashlib.sha256(_json.dumps(['GCM_PROOF_V1', source_sha, name, ident], sort_keys=True).encode()).hexdigest()
            except Exception:
                return function(*args, **kwargs)
            doc = _load(key)
            if doc and doc.get('schema') == 'GCM_PROOF_V1' and 'result' in doc:
                _stats['hits'] += 1
                return _reboot(_copy.deepcopy(doc['result']), doc.get('boot_id'), _boot())
            result = function(*args, **kwargs)
            try:
                if _norm([list(args), kwargs]) == ident and _json.loads(_json.dumps(result)) == result:
                    _stats['misses'] += 1
                    _store(key, {'schema': 'GCM_PROOF_V1', 'function': name, 'boot_id': _boot(), 'created': _time.time(), 'result': result})
            except Exception:
                pass
            return result
        cached._gcm_proof_reuse = True
        return cached

    def _targets(filename):
        base = _os.path.basename(filename or '')
        for prefix, names in TABLE:
            if base == prefix or (not prefix.endswith('.py') and base.startswith(prefix) and base.endswith('.py')):
                return names
        return ()

    _real_exec_module = _be.SourceFileLoader.exec_module

    EXT_HOOKS = []

    def _exec_module(self, module):
        _real_exec_module(self, module)
        for hook in list(EXT_HOOKS):
            try:
                hook(module)
            except Exception:
                pass
        try:
            names = _targets(getattr(module, '__file__', ''))
            if names:
                with _real_open(module.__file__, 'rb') as f:
                    source_sha = _hashlib.sha256(f.read()).hexdigest()
                for name in names:
                    fn = getattr(module, name, None)
                    if callable(fn) and not getattr(fn, '_gcm_proof_reuse', False):
                        setattr(module, name, _wrap(fn, source_sha, name))
        except Exception:
            pass

    _be.SourceFileLoader.exec_module = _exec_module

    def _replay():
        """Whole-process reuse for checking scripts run as their own process with an --output report."""
        script = next((a for a in _ARGS if str(a).endswith('.py')), '')
        flags = REPLAY.get(_os.path.basename(str(script)))
        if not flags or not REUSE:
            return
        args = list(map(str, _ARGS[_ARGS.index(script) + 1:]))
        if '--output' not in args or args.count('--output') != 1:
            return
        output = args[args.index('--output') + 1]
        try:
            with _real_open(script, 'rb') as f:
                source_sha = _hashlib.sha256(f.read()).hexdigest()
            values = {}
            for flag in flags:
                if args.count(flag) != 1:
                    return
                # temporary metadata files get new paths each run: small files count by content only
                values[flag] = _file_key(args[args.index(flag) + 1], with_path=(flag == '--nsp'))
                if values[flag] is None:
                    return
            key = _hashlib.sha256(_json.dumps(['GCM_REPLAY_V1', source_sha, values], sort_keys=True).encode()).hexdigest()
        except Exception:
            return
        doc = _load(key)
        if doc and doc.get('schema') == 'GCM_REPLAY_V1':
            try:
                _os.makedirs(_os.path.dirname(output) or '.', exist_ok=True)
                tmp = output + '.json.tmp'
                with _real_open(tmp, 'w') as f:
                    f.write(doc['report'])
                _os.replace(tmp, output)
                _sys.stdout.write(doc['stdout']); _sys.stdout.flush()
                _stats['hits'] += 1
                _flush()
                _os._exit(0)
            except Exception:
                pass
        start = _time.time()
        captured = []

        class _Tee:
            def __init__(self, stream):
                self._stream = stream

            def write(self, text):
                captured.append(text)
                return self._stream.write(text)

            def __getattr__(self, name):
                return getattr(self._stream, name)

        _sys.stdout = _Tee(_sys.stdout)

        def save():
            try:
                st = _os.stat(output)
                if st.st_mtime < start - 1:
                    return
                with _real_open(output, 'r') as f:
                    report = f.read()
                _json.loads(report)
                again = {flag: _file_key(args[args.index(flag) + 1], with_path=(flag == '--nsp')) for flag in flags}
                if again == values:
                    _store(key, {'schema': 'GCM_REPLAY_V1', 'boot_id': _boot(), 'created': _time.time(), 'report': report, 'stdout': ''.join(captured)})
            except Exception:
                pass

        _atexit.register(save)

    def _load_extensions():
        folder = _os.environ.get('GCM_PROOF_EXT_DIR') or '/userdata/system/gcm-native-test/step61-common-content/gcm-proof-reuse-extensions'
        if _os.environ.get('GCM_PROOF_EXT_OFF') or not _os.path.isdir(folder):
            return
        for name in sorted(_os.listdir(folder)):
            path = _os.path.join(folder, name)
            if not (name.startswith('ext-') and name.endswith('.py')) or _os.path.islink(path):
                continue
            try:
                with _real_open(path, 'rb') as f:
                    code = compile(f.read(), path, 'exec')
                exec(code, globals())
            except Exception:
                pass

    _load_extensions()

    try:
        _replay()
    except Exception:
        pass
