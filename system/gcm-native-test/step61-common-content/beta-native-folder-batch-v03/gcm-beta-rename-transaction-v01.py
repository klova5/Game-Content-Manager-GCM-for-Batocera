"""Journaled quarantine/restore engine. Callers own emulator locks and path policy."""
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path


def safe(path):
    path = Path(path)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        raise ValueError('Absolute normalized transaction path required')
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Symlink transaction path rejected')
    return path


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(path):
    path = safe(path)
    result = {}
    entries = [path]
    if path.is_dir():
        for directory, dirs, files in os.walk(path, followlinks=False):
            entries.extend(Path(directory) / name for name in sorted(dirs + files))
    for item in entries:
        safe(item)
        s = item.lstat()
        if not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)):
            raise ValueError('Unsupported transaction entry')
        result['.' if item == path else item.relative_to(path).as_posix()] = [
            s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
    return result


def matches(actual, expected, renamed=False):
    if actual.keys() != expected.keys():
        return False
    for name, value in actual.items():
        # rename may change the root ctime; descendants must remain identical.
        if value != expected[name] and not (
            renamed and name == '.' and value[:5] == expected[name][:5]):
            return False
    return True


def save(path, data):
    path = safe(path)
    fd, temporary = tempfile.mkstemp(prefix='.uninstall-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_moves(moves):
    roots = []
    for move in moves:
        for key in ('source', 'quarantine'):
            p = safe(move[key])
            if any(p == q or p in q.parents or q in p.parents for q in roots):
                raise ValueError('Overlapping transaction paths')
            roots.append(p)
        source, dest = Path(move['source']), Path(move['quarantine'])
        if not dest.parent.is_dir() or source.stat().st_dev != dest.parent.stat().st_dev:
            raise ValueError('Quarantine must have an existing parent on the same filesystem')
        if dest.exists():
            raise ValueError('Quarantine destination already exists')
        if not matches(snapshot(source), move['snapshot']):
            raise ValueError('Uninstall target changed before transaction')


def apply(journal_path, moves, metadata, after_move=None):
    journal_path = safe(journal_path)
    if journal_path.exists():
        raise ValueError('New uninstall journal required')
    validate_moves(moves)
    journal = {'schema': 'GCM_UNINSTALL_TRANSACTION_V1', 'status': 'PREPARED',
               'metadata': metadata, 'moves': moves}
    save(journal_path, journal)
    try:
        journal['status'] = 'QUARANTINING'
        save(journal_path, journal)
        for index, move in enumerate(moves):
            source, dest = safe(move['source']), safe(move['quarantine'])
            if dest.exists() or not matches(snapshot(source), move['snapshot']):
                raise ValueError('Uninstall paths changed before rename')
            os.rename(source, dest)
            sync(source.parent)
            sync(dest.parent)
            if not matches(snapshot(dest), move['snapshot'], renamed=True):
                raise ValueError('Quarantined content changed')
            move['state'] = 'QUARANTINED'
            save(journal_path, journal)
            if after_move:
                after_move(index)
        journal['status'] = 'COMPLETE'
        save(journal_path, journal)
        return journal
    except Exception:
        rollback(journal_path)
        raise


def rollback(journal_path):
    journal_path = safe(journal_path)
    journal = json.loads(journal_path.read_text())
    if journal.get('schema') != 'GCM_UNINSTALL_TRANSACTION_V1':
        raise ValueError('Unsupported uninstall journal')
    moves = journal['moves']
    if journal.get('status') == 'ROLLED_BACK':
        for move in moves:
            if Path(move['quarantine']).exists() or not matches(
                    snapshot(move['source']), move['snapshot'], renamed=True):
                raise ValueError('Rolled-back paths changed')
        return journal
    # Validate every target before changing any path during recovery.
    pending = []
    for move in reversed(moves):
        source, dest = safe(move['source']), safe(move['quarantine'])
        if source.exists() == dest.exists():
            raise ValueError('Ambiguous or missing uninstall recovery paths')
        current = source if source.exists() else dest
        if not matches(snapshot(current), move['snapshot'], renamed=True):
            raise ValueError('Uninstall recovery content changed')
        if dest.exists():
            pending.append((dest, source))
    journal['status'] = 'ROLLING_BACK'
    save(journal_path, journal)
    for dest, source in pending:
        if source.exists():
            raise ValueError('Recovery destination appeared')
        os.rename(dest, source)
        sync(dest.parent)
        sync(source.parent)
    journal['status'] = 'ROLLED_BACK'
    save(journal_path, journal)
    return journal
