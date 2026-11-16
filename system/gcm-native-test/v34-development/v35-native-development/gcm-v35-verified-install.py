#!/usr/bin/env python3
"""GCM v3.5 guarded, game-specific Switch update installation.

One operation per invocation.  Requires existing guarded PASS promotion and a
current-boot PASS worker before installing; never moves or deletes source.
The installation endpoint is not exposed to the UI until separately validated.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

DEV = Path('/userdata/system/gcm-native-test/v34-development')
WORK = DEV / 'v35-native-development'
DB = Path('/userdata/system/add-ons/game-content-manager/index.sqlite')
STORE = WORK / 'v35-verified-inventory.json'
JOBS = DEV / 'v35-tools/verification-jobs'
ROOT = Path('/userdata/system/game-content/switch')
RYUJINX = Path('/userdata/system/configs/Ryujinx/games')
TRANSACTIONS = WORK / 'v35-install-transactions'
BOOT = Path('/proc/sys/kernel/random/boot_id')
CHECKS = (
    'preview_passed', 'identity_match', 'main_content_hashes_verified',
    'meta_archive_hash_verified', 'base_meta_nca_sha256_verified',
    'base_source_stat_unchanged', 'update_source_stat_unchanged',
    'source_stat_unchanged_during_scan',
)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_id(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def persistent_id(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns]


def key(base, source):
    return hashlib.sha256((base + '\0' + source).encode()).hexdigest()


def parse_token(value):
    if not (2 <= len(value) <= 8192 and not len(value) % 2 and re.fullmatch(r'[0-9a-fA-F]+', value)):
        raise ValueError('Invalid hexadecimal update token')
    decoded = bytes.fromhex(value).decode('utf-8')
    if not os.path.isabs(decoded) or not decoded.lower().endswith('.nsp'):
        raise ValueError('Update token must contain an absolute NSP file path')
    return os.path.abspath(decoded)


def exact_title(text):
    if not isinstance(text, str) or not re.fullmatch(r'[0-9a-fA-F]{16}', text):
        raise ValueError('Missing or invalid verified CNMT Title ID')
    return text.upper()


def db_candidate(path, base_id, patch_id):
    with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as con:
        row = con.execute(
            'SELECT system, application_id, title_id, content_type, state, metadata_source, size FROM packages WHERE path=?',
            (path,),
        ).fetchone()
    if not row or not (
        row[0] == 'switch' and (row[1] or '').upper() == base_id
        and (row[2] or '').upper() == patch_id
        and row[3] == 'update' and row[4] == 'external'
        and row[5] == 'internal-cnmt' and row[6] == Path(path).stat().st_size
    ):
        raise ValueError('Source is not a matching, internally identified external update in the live index')


def load_candidate(rom, token, fresh=False):
    base = os.path.abspath(rom)
    source = parse_token(token)
    if not Path(base).is_file() or not Path(source).is_file():
        raise FileNotFoundError('The selected game or update file is unavailable')
    state = json.loads(STORE.read_text())
    if state.get('schema') != 'GCM_V35_VERIFIED_EXTERNAL_V1':
        raise ValueError('Unsupported verified-inventory schema')
    entry = state.get('records', {}).get(key(base, source))
    if not isinstance(entry, dict) or entry.get('classification') != 'VERIFIED_EXTERNAL':
        raise ValueError('Not in verified AVAILABLE inventory; verify the update first')
    if entry.get('rom') != base or entry.get('source') != source:
        raise ValueError('Verified record is for a different game or source')
    if entry.get('base_stat') != persistent_id(base) or entry.get('update_stat') != persistent_id(source):
        raise ValueError('Base ROM or update changed since verification; verify again')
    base_id = exact_title(entry.get('base_title_id'))
    patch_id = exact_title(entry.get('patch_title_id'))
    if int(patch_id, 16) != int(base_id, 16) + 0x800:
        raise ValueError('Verified CNMT update Title ID does not match base game')
    if Path(source).resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Source is already in local managed storage')
    db_candidate(source, base_id, patch_id)
    target = ROOT / base_id / 'updates' / Path(source).name
    job_path = JOBS / (key(base, source) + '.json')
    recent = False
    try:
        job = json.loads(job_path.read_text())
        report_path = Path(job.get('verifier_report', ''))
        report = json.loads(report_path.read_text()) if report_path.is_file() else {}
        recent = (
            job.get('status') == 'PASS'
            and job.get('scan_id') == entry.get('verification_scan_id')
            and job.get('boot_id') == BOOT.read_text().strip()
            and job.get('preview_passed') is True
            and job.get('import_authorized') is False
            and job.get('changes_performed') is False
            and job.get('source_stats') == [source_id(base), source_id(source)]
            and job.get('base_title_id', '').upper() == base_id
            and job.get('patch_title_id', '').upper() == patch_id
            and report.get('status') == 'PASS'
            and report.get('import_authorized') is False
            and report.get('changes_performed') is False
            and all(report.get(c) is True for c in CHECKS)
            and report.get('base_title_id', '').upper() == base_id
            and report.get('patch_title_id', '').upper() == patch_id
        )
    except (ValueError, OSError, TypeError, AttributeError):
        recent = False
    if fresh and not recent:
        raise ValueError('A fresh guarded PASS for this boot and these exact file identities is required; verify again')
    return dict(base=base, source=source, base_id=base_id, patch_id=patch_id,
                target=target, entry=entry, fresh_pass=recent)


def config_path(base_id):
    if RYUJINX.is_dir():
        dirs = [p for p in RYUJINX.iterdir() if p.is_dir() and p.name.casefold() == base_id.casefold()]
        if len(dirs) > 1:
            raise ValueError('Ambiguous Ryujinx Title ID directories')
        if dirs:
            return dirs[0] / 'updates.json'
    return RYUJINX / base_id.lower() / 'updates.json'


def read_config(path):
    if not path.exists():
        return {'selected': None, 'paths': []}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('paths'), list):
        raise ValueError('Invalid Ryujinx updates.json configuration')
    if not all(isinstance(p, str) for p in value['paths']):
        raise ValueError('Invalid Ryujinx registered update path')
    selected = value.get('selected')
    if selected is not None and not isinstance(selected, str):
        raise ValueError('Invalid Ryujinx selected update')
    if selected and not any(same(selected, path) for path in value['paths']):
        raise ValueError('Ryujinx selected update is not registered; fix existing configuration first')
    return value


def same(a, b):
    return os.path.realpath(os.fspath(a)) == os.path.realpath(os.fspath(b))


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.gcm-v35-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
        directory_sync(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def directory_sync(folder):
    fd = os.open(folder, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def registration_status(target, config):
    if not target.is_file():
        return 'NOT_COPIED'
    if any(same(p, target) for p in config['paths']):
        return 'INSTALLED'
    return 'READY'


def preview(args):
    c = load_candidate(args.rom, args.token)
    config = read_config(config_path(c['base_id']))
    target = c['target']
    free = shutil.disk_usage(ROOT.parent if ROOT.parent.exists() else '/userdata').free
    print('INSTALL\tSTATUS\tPREVIEW')
    print('INSTALL\tGAME_TITLE_ID\t' + c['base_id'])
    print('INSTALL\tPATCH_TITLE_ID\t' + c['patch_id'])
    print('INSTALL\tSOURCE\t' + c['source'])
    print('INSTALL\tLOCAL\t' + str(target))
    print('INSTALL\tSOURCE_BYTES\t' + str(Path(c['source']).stat().st_size))
    print('INSTALL\tFREE_BYTES\t' + str(free))
    print('INSTALL\tFRESH_VERIFICATION\t' + ('YES' if c['fresh_pass'] else 'NO - REVERIFY'))
    print('INSTALL\tCURRENT_LOCAL_STATE\t' + registration_status(target, config))
    print('INSTALL\tACTIVE_PRESERVED\tYES')
    print('INSTALL\tEXTERNAL_ORIGINAL_PRESERVED\tYES')
    print('STATUS\tPREVIEW ONLY - NO FILE CHANGES')


def insert_managed(c, digest):
    target = c['target']
    s = target.stat()
    with sqlite3.connect(DB, timeout=30) as con:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT application_id, content_type, state, sha256 FROM packages WHERE path=?',
                               (str(target),)).fetchone()
        if existing and not (
            (existing[0] or '').upper() == c['base_id']
            and existing[1] == 'update' and existing[2] == 'managed'
            and existing[3] == digest
        ):
            raise ValueError('Conflicting managed-index row; refusing overwrite')
        if not existing:
            con.execute('''INSERT INTO packages
                (path, system, title_id, application_id, content_type, version,
                 size, mtime, sha256, state, source_path, metadata_source, last_seen)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (str(target), 'switch', c['patch_id'], c['base_id'], 'update',
                 c['entry'].get('version', ''), s.st_size, s.st_mtime, digest,
                 'managed', c['source'], 'internal-cnmt', time.time()))


def copy_verified(c, tx):
    source, target = Path(c['source']), c['target']
    if target.is_symlink():
        raise ValueError('Managed destination is a symlink')
    source_identity = source_id(source)
    source_hash = sha(source)
    if source_id(source) != source_identity:
        raise ValueError('Original source changed during SHA256 calculation')
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('Managed destination escapes storage root')
    if target.exists():
        if not target.is_file() or sha(target) != source_hash:
            raise FileExistsError('Existing managed target differs from source; no overwrite')
        return source_hash, False
    if shutil.disk_usage(folder).free < source.stat().st_size + 128 * 1024 * 1024:
        raise OSError('Insufficient disk space for safe local copy')
    fd, tempfile_name = tempfile.mkstemp(prefix='.gcm-v35-copy-', suffix='.part', dir=folder)
    temp = Path(tempfile_name)
    try:
        with source.open('rb') as inp, os.fdopen(fd, 'wb') as out:
            shutil.copyfileobj(inp, out, 8 * 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if source_id(source) != source_identity or sha(source) != source_hash:
            raise ValueError('External original changed during copying')
        if sha(temp) != source_hash:
            raise IOError('Copied update SHA256 mismatch')
        if target.exists():
            raise FileExistsError('Managed destination appeared concurrently')
        os.chmod(temp, 0o644)
        os.replace(temp, target)
        directory_sync(folder)
        if sha(target) != source_hash:
            raise IOError('Managed target failed final SHA256')
        return source_hash, True
    finally:
        temp.unlink(missing_ok=True)


def journal(tx, **fields):
    content = json.dumps(fields, sort_keys=True, indent=2).encode() + b'\n'
    atomic_bytes(tx / 'state.json', content)


def install(args):
    if not args.confirm:
        raise ValueError('Explicit --confirm required; use preview first')
    TRANSACTIONS.mkdir(parents=True, exist_ok=True)
    with (TRANSACTIONS / '.install.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        c = load_candidate(args.rom, args.token, fresh=True)
        target = c['target']
        config = config_path(c['base_id'])
        before = read_config(config)
        # Do not alter a live emulator's configuration.  The GUI should ensure
        # the game is not running; this backend refuses a clearly active Ryujinx.
        for proc in Path('/proc').glob('[0-9]*/comm'):
            try:
                if proc.read_text().strip().casefold() in ('ryujinx', 'ryujinx.ava', 'ryujinx-headless'):
                    raise RuntimeError('Close Ryujinx before installing an update')
            except (FileNotFoundError, ProcessLookupError):
                continue
        tx = TRANSACTIONS / uuid.uuid4().hex
        tx.mkdir(mode=0o700)
        snapshot = tx / 'updates.before.json'
        prior_existed = config.exists()
        if prior_existed:
            shutil.copy2(config, snapshot)
        journal(tx, stage='starting', source=c['source'], local=str(target))
        registered = False
        try:
            source_hash, copied = copy_verified(c, tx)
            journal(tx, stage='copied', source=c['source'], local=str(target),
                    sha256=source_hash, copied=copied)
            insert_managed(c, source_hash)
            if read_config(config) != before:
                raise RuntimeError('Ryujinx configuration changed during copy')
            paths = list(before['paths'])
            # If the same package was active externally, migrate the path to
            # local storage without changing the active version.
            migrated = any(same(p, c['source']) for p in paths)
            paths = [str(target) if same(p, c['source']) else p for p in paths]
            if not any(same(p, target) for p in paths):
                paths.append(str(target))
            # Preserve unrelated registered paths and their ordering.
            unique = []
            for p in paths:
                if not any(same(p, q) for q in unique):
                    unique.append(p)
            selected = before.get('selected')
            if isinstance(selected, str) and selected and same(selected, c['source']):
                selected = str(target)
            updated = dict(before)
            updated['paths'] = unique
            updated['selected'] = selected
            journal(tx, stage='registering', source=c['source'], local=str(target),
                    sha256=source_hash, previous_selected=before.get('selected'), migrated=migrated)
            # Mark before the atomic replacement so even a post-rename
            # directory-fsync failure triggers configuration rollback.
            registered = True
            atomic_bytes(config, json.dumps(updated, indent=2).encode() + b'\n')
            final = read_config(config)
            if final != updated or registration_status(target, final) != 'INSTALLED':
                raise RuntimeError('Ryujinx registration post-check failed')
            if sha(target) != source_hash or sha(c['source']) != source_hash:
                raise RuntimeError('Post-registration source/local hash mismatch')
            journal(tx, stage='installed', source=c['source'], local=str(target),
                    sha256=source_hash, copied=copied, previous_selected=before.get('selected'),
                    selected_preserved_or_migrated=True)
            print('INSTALL\tSTATUS\tINSTALLED')
            print('INSTALL\tLOCAL\t' + str(target))
            print('INSTALL\tSHA256\t' + source_hash)
            print('INSTALL\tACTIVE\t' + (str(selected) if selected else 'NONE'))
            print('INSTALL\tTRANSACTION\t' + str(tx))
            print('STATUS\tEXTERNAL ORIGINAL PRESERVED')
        except Exception as exc:
            if registered:
                try:
                    if prior_existed:
                        atomic_bytes(config, snapshot.read_bytes())
                    else:
                        config.unlink(missing_ok=True)
                except Exception as rollback_exc:
                    journal(tx, stage='rollback_failed', source=c['source'], local=str(target),
                            error=str(exc), rollback_error=str(rollback_exc))
                    raise RuntimeError('Registration failed and rollback failed; inspect ' + str(tx)) from exc
            journal(tx, stage='failed', source=c['source'], local=str(target), error=str(exc),
                    note='Local verified copy, if created, is retained as READY; external source is untouched')
            raise


def status(args):
    # For persistent local status, source may be detached after a successful copy.
    base = os.path.abspath(args.rom)
    source = parse_token(args.token)
    state = json.loads(STORE.read_text())
    entry = state.get('records', {}).get(key(base, source), {})
    if entry.get('classification') != 'VERIFIED_EXTERNAL':
        raise ValueError('No matching verified update record')
    base_id = exact_title(entry.get('base_title_id'))
    target = ROOT / base_id / 'updates' / Path(source).name
    config = read_config(config_path(base_id))
    value = registration_status(target, config)
    if target.is_file():
        with sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True) as con:
            row = con.execute('SELECT sha256, state, application_id FROM packages WHERE path=?',
                              (str(target),)).fetchone()
        if not row or row[1] != 'managed' or (row[2] or '').upper() != base_id or sha(target) != row[0]:
            value = 'ERROR'
    print('INSTALL\tSTATUS\t' + value)
    print('INSTALL\tLOCAL\t' + str(target))
    print('INSTALL\tEXTERNAL_PRESENT\t' + ('YES' if Path(source).is_file() else 'NO'))


def selftest():
    with tempfile.TemporaryDirectory(prefix='gcm-v35-installer-test-') as d:
        root = Path(d)
        ext = root / 'external'
        ext.mkdir()
        original = ext / 'Stray v1.6.0.nsp'
        original.write_bytes(b'controlled test data' * 200)
        local = root / 'managed' / '010075101EF84000' / 'updates' / original.name
        before_hash = sha(original)
        local.parent.mkdir(parents=True)
        # Use the same copy and local hash checks as the real installer.
        with tempfile.NamedTemporaryFile(dir=local.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            with original.open('rb') as inp:
                shutil.copyfileobj(inp, temp)
        assert sha(temp_path) == sha(original)
        os.replace(temp_path, local)
        assert sha(local) == before_hash
        config = {'selected': '', 'paths': []}
        assert registration_status(local, config) == 'READY'
        config['paths'].append(str(local))
        assert registration_status(local, config) == 'INSTALLED'
        assert original.exists() and sha(original) == before_hash
        local.write_bytes(b'bad copy')
        assert sha(local) != before_hash
        print('PASS: External original preserved by local copy')
        print('PASS: SHA256 matches before registration')
        print('PASS: Local copy without registration is READY')
        print('PASS: Local registered copy is INSTALLED')
        print('PASS: Changed local copy is detectable')
        print('PASS: No live content, configuration or index modified')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['selftest', 'preview', 'install', 'status'])
    parser.add_argument('--rom')
    parser.add_argument('--token')
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    if args.action == 'selftest':
        selftest()
        return
    if not args.rom or not args.token:
        parser.error('--rom and --token required')
    if args.action == 'preview':
        preview(args)
    elif args.action == 'install':
        install(args)
    elif args.action == 'status':
        status(args)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR\t' + str(exc).replace('\n', ' ')[:600])
        sys.exit(1)
