"""GCM Switch content store: one shared copy per package, used by Ryujinx and Citron (v02).

v02: uses the v02 in-place module, which keeps GCM's Citron folder list for the Switch launcher.

Layout:  /userdata/system/game-content/switch/dlc/<GAME TITLE ID>/<package>.nsp
         /userdata/system/game-content/switch/updates/<GAME TITLE ID>/<package>.nsp

copy     Copies selected registered packages into the store. Each copy is hashed while
         it is written and must equal the SHA-256 in the package's verified proof. The
         emulator is then pointed at the copy (Ryujinx dlc.json / updates.json paths;
         Citron: the store folders are on its external list and an original folder is
         taken off the list once every package in it has a store copy). Enabled and
         selected states are unchanged. Originals are never modified or removed.
migrate  Moves GCM's older copies (switch/<ID>/dlc|updates, citron-registered/<ID>/...)
         into the store by rename on the same drive; exact duplicates go to a review
         folder. Registrations are re-pointed in the same transaction.
Every change keeps a backup and journal and rolls back on failure.
"""
import argparse, contextlib, copy as copy_module, fcntl, hashlib, importlib.util, json, os, re, sys, time
from pathlib import Path

GCM = Path('/userdata/system/gcm-native-test')
CONTENT = Path('/userdata/system/game-content')
STORE = CONTENT / 'switch'
OLD_CITRON = CONTENT / 'citron-registered'
REVIEW = STORE / '.gcm-duplicates-review'
INPLACE = (GCM / 'step61-common-content/beta-switch-dlc-inplace-v02/gcm-switch-dlc-inplace-v02.py',
           '3561294a91c2b7740c72aa1b3ed03d0fa87fedffeebf74e8f73e389976b8d072')
KIND_DIR = {'dlc': 'dlc', 'update': 'updates'}
_INPLACE = []


def inplace():
    if not _INPLACE:
        path, expected = INPLACE
        if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Content store dependency changed: ' + path.name)
        spec = importlib.util.spec_from_file_location('gcm_store_inplace', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        _INPLACE.append(module)
    return _INPLACE[0]


def store_dir(kind, app):
    if kind not in KIND_DIR or not re.fullmatch('[0-9A-F]{16}', app):
        raise ValueError('Invalid store location')
    return STORE / KIND_DIR[kind] / app


def in_store(path):
    real = Path(os.path.realpath(path))
    return any(real.is_relative_to(STORE / d) for d in KIND_DIR.values())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def copy_verified(source, destination, expected_sha, progress):
    """Copy source to destination; the bytes written must hash to expected_sha."""
    m = inplace()
    destination = m.safe(destination)
    if destination.exists():
        if sha256_file(destination) != expected_sha:
            raise ValueError('A different file already uses the store name: ' + str(destination))
        return 'reused'
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + '.gcm-copy')
    h = hashlib.sha256()
    before = os.stat(source)
    try:
        with open(source, 'rb') as src, open(temporary, 'xb') as dst:
            for block in iter(lambda: src.read(8 * 1024 * 1024), b''):
                h.update(block); dst.write(block)
            dst.flush(); os.fsync(dst.fileno())
        after = os.stat(source)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Original changed while copying: ' + str(source))
        if h.hexdigest() != expected_sha:
            raise ValueError('Copy does not match the verified package hash: ' + Path(source).name)
        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    progress('COPIED', Path(source).name)
    return 'copied'


def remove_created(paths):
    for path in paths:
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()


def ryujinx_store_proofs(m):
    """Proofs for store files Ryujinx already verified, so Citron can reuse them."""
    integrity = m.load('ryujinx_integrity')
    catalog = m.load('citron_catalog')
    proofs, identities = {}, {}
    for game in m.ryujinx_read_store(integrity).values():
        for r in game.get('records', []):
            path = r['registered_path']
            if in_store(path) and Path(path).is_file():
                stats = catalog.identity(Path(path))
                proofs[os.path.realpath(path)] = (stats, proof_only(r))
                identities[os.path.realpath(path)] = (stats, {k: r[k] for k in ('title_id', 'application_id', 'content_type', 'internal_version') if k in r} | {'identified': True, 'path': path})
    return proofs, identities


def proof_only(record):
    return {k: v for k, v in record.items() if k not in ('registered_path', 'origin', 'active', 'path', 'registered_root', 'stat_identity')}


# ------------------------------------------------------------------ Ryujinx
def ryujinx_repoint(rom, mapping, proofs, progress, kind_label, before_records=None):
    """mapping: {realpath old: new path}. Rewrites dlc.json/updates.json paths in one transaction."""
    m = inplace()
    integrity = m.load('ryujinx_integrity')
    adapter, _ = integrity.dependencies()
    snapshot = adapter.snapshot(rom)
    # Migration moves files first, so it passes the states captured before the move.
    records = before_records if before_records is not None else integrity.validate(rom)['records']
    before_active = {(r['content_type'], os.path.realpath(r['registered_path'])): r['active'] for r in records}
    folder = m.transaction_folder('store-ryujinx')
    originals, written = {}, {}
    for kind in ('updates', 'dlc'):
        path = m.safe(snapshot['configuration'][kind]['path'])
        raw, document = adapter.read_configuration(path, {'selected': None, 'paths': []} if kind == 'updates' else [])
        changed = copy_module.deepcopy(document)
        if kind == 'dlc':
            for entry in changed:
                entry['path'] = mapping.get(os.path.realpath(entry['path']), entry['path'])
        else:
            changed['paths'] = [mapping.get(os.path.realpath(p), p) for p in changed.get('paths', [])]
            if changed.get('selected'):
                changed['selected'] = mapping.get(os.path.realpath(changed['selected']), changed['selected'])
        if changed != document:
            originals[kind] = (path, raw)
            written[kind] = changed
            if raw is not None:
                m.atomic(folder / (kind + '.before.json'), raw)
    if not written:
        return 0
    m.write_json(folder / 'journal.json', {'state': 'prepared', 'kind': kind_label, 'mapping': mapping})
    try:
        for kind, document in written.items():
            path, raw = originals[kind]
            current, _ = adapter.read_configuration(path, None)
            if current != raw:
                raise ValueError('Ryujinx configuration changed during the copy')
            m.atomic(path, (json.dumps(document, indent=2) + '\n').encode())
        m.ryujinx_store_update([rom], proofs, progress)
        after = integrity.validate(rom)
        after_active = {(r['content_type'], os.path.realpath(r['registered_path'])): r['active'] for r in after['records']}
        for (kind, old), active in before_active.items():
            new = os.path.realpath(mapping.get(old, old))
            if after_active.get((kind, new)) != active:
                raise ValueError('Enabled or selected state changed while re-pointing')
    except BaseException as exc:
        for kind, (path, raw) in originals.items():
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                m.atomic(path, raw)
        try:
            m.ryujinx_store_update([rom], progress=progress)
        finally:
            m.write_json(folder / 'journal.json', {'state': 'rolled_back', 'error': str(exc)[:400], 'mapping': mapping})
        raise
    m.write_json(folder / 'journal.json', {'state': 'committed', 'kind': kind_label, 'mapping': mapping})
    return len(mapping)


def ryujinx_rows(rom, kind):
    m = inplace()
    game = m.ryujinx_fresh(rom, lambda *a: None)
    app = game['native_snapshot']['application_id']
    rows = []
    for r in game['records']:
        if r['content_type'] != kind:
            continue
        stored = in_store(r['registered_path'])
        rows.append({'token': str(r['registered_path']).encode().hex(), 'label': Path(r['registered_path']).stem,
                     'state': 'IN_GCM' if stored else 'EXTERNAL', 'record': r, 'app': app})
    return rows


def ryujinx_copy(rom, kind, tokens, progress):
    m = inplace()
    rom = str(Path(rom).resolve(strict=True))
    rows = {row['token']: row for row in ryujinx_rows(rom, kind)}
    unknown = [t for t in tokens if t not in rows]
    if unknown:
        raise ValueError('%d selected item(s) are not registered for this game' % len(unknown))
    mapping, proofs, skipped, copied, created = {}, {}, 0, 0, []
    for t in tokens:
        row = rows[t]
        record = row['record']
        if row['state'] == 'IN_GCM':
            skipped += 1
            continue
        source = record['registered_path']
        destination = store_dir(kind, row['app']) / Path(source).name
        result = copy_verified(source, destination, record['sha256'], progress)
        copied += result == 'copied'
        if result == 'copied':
            created.append(destination)
        mapping[os.path.realpath(source)] = str(destination)
        integrity = m.load('ryujinx_integrity')
        proofs[os.path.realpath(destination)] = (integrity.identity(destination), proof_only(record))
    lock = m.SWITCH / 'state/ryujinx-configuration.lock'
    try:
        with lock.open('a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            m.ryujinx_ensure_closed()
            ryujinx_repoint(rom, mapping, proofs, progress, kind)
    except BaseException:
        remove_created(created)
        raise
    return {'copied': copied, 'reused': len(mapping) - copied, 'skipped': skipped}


# ------------------------------------------------------------------ Citron
def citron_rewrite_dirs(text, dirs):
    for d in dirs:
        if any(c in d for c in '\n\r"\\,;=') or d != d.strip():
            raise ValueError('Folder name needs Qt escaping: ' + d)
    lines = [l for l in text.split('\n') if not re.match(r'^Paths\\external_content_dirs\\\d+\\path=', l)]
    out, done = [], False
    for l in lines:
        if re.match(r'^Paths\\external_content_dirs\\size=\d+$', l):
            if done:
                raise ValueError('Citron external folder count found twice')
            out.extend('Paths\\external_content_dirs\\%d\\path=%s' % (i, d) for i, d in enumerate(dirs, 1))
            out.append('Paths\\external_content_dirs\\size=%d' % len(dirs))
            done = True
        else:
            out.append(l)
    if not done:
        raise ValueError('Citron external folder count not found')
    return '\n'.join(out)


def citron_title_token(app, title):
    return 'citron:dlc:' + app + ':' + title


def citron_rows(rom, kind):
    m = inplace()
    rows_module = m.load('citron_rows')
    state = m.citron_fresh(lambda *a: None)
    application, _ = rows_module.records(rom, kind)
    groups = {}
    for r in state['records']:
        if r['application_id'] != application or r['content_type'] != kind:
            continue
        key = citron_title_token(application, r['title_id']) if kind == 'dlc' else rows_module.update_token(r)
        groups.setdefault(key, []).append(r)
    rows = []
    for token, records in sorted(groups.items()):
        stored = any(r['origin'] == 'External' and in_store(r['path']) for r in records)
        external = [r for r in records if r['origin'] == 'External']
        state_name = 'IN_GCM' if stored else ('EXTERNAL' if external else 'CITRON_NAND')
        label = ('DLC ' + format(int(records[0]['title_id'], 16) & 0xFFF, '04X')) if kind == 'dlc' else ('Version ' + str(records[0]['internal_version']))
        if kind == 'dlc' and external:
            label = Path(external[0]['path']).stem  # the package file name, as on Ryujinx
        rows.append({'token': token, 'label': label, 'state': state_name, 'records': records, 'app': application})
    return rows


def citron_apply_dirs(new_dirs, proofs, identities, progress, purpose):
    m = inplace()
    catalog = m.load('citron_catalog')
    toggle = m.load('citron_toggle')
    config = m.safe(catalog.CONFIG)
    original = config.read_bytes()
    folder = m.transaction_folder('store-citron')
    m.atomic(folder / 'qt-config.before.ini', original)
    m.write_json(folder / 'journal.json', {'state': 'prepared', 'purpose': purpose, 'folders': new_dirs})
    try:
        toggle.ensure_closed()
        text = original.decode('utf-8')
        updated = m.citron_set_dirs(text, new_dirs)
        if updated != text:
            m.atomic(config, updated.encode('utf-8'))
        m.citron_folders_save(new_dirs)
        configured = [r['path'] for r in catalog.storage() if r['origin'] == 'External']
        if configured != [str(Path(d).resolve()) for d in new_dirs]:
            raise ValueError('Citron did not read back the external folder list')
        result = m.citron_store_update(proofs, progress, identities)
    except BaseException as exc:
        toggle.ensure_closed()
        m.atomic(config, original)
        with contextlib.suppress(Exception):
            m.citron_folders_save(m.citron_external_dirs(original.decode('utf-8'))[1])
        try:
            m.citron_store_update(progress=progress)
        finally:
            m.write_json(folder / 'journal.json', {'state': 'rolled_back', 'error': str(exc)[:400]})
        raise
    m.write_json(folder / 'journal.json', {'state': 'committed', 'purpose': purpose, 'folders': new_dirs})
    return result


def citron_target_dirs(current_dirs, records_after):
    """Store folders first in the list; drop an original folder once all its packages are in the store."""
    store = [str(STORE / 'dlc'), str(STORE / 'updates')]
    stored = {(r['title_id'], r['content_type'], r['internal_version'], r.get('sha256')) for r in records_after
              if r['origin'] == 'External' and in_store(r['path'])}
    keep = []
    for d in current_dirs:
        if d in store or os.path.realpath(d) in [os.path.realpath(s) for s in store]:
            continue
        inside = [r for r in records_after if r['origin'] == 'External' and Path(r['path']).is_relative_to(Path(os.path.realpath(d)))]
        if inside and all((r['title_id'], r['content_type'], r['internal_version'], r.get('sha256')) in stored for r in inside):
            continue
        keep.append(d)
    return keep + store


def citron_copy(rom, kind, tokens, progress):
    m = inplace()
    catalog = m.load('citron_catalog')
    rows = {row['token']: row for row in citron_rows(rom, kind)}
    unknown = [t for t in tokens if t not in rows]
    if unknown:
        raise ValueError('%d selected item(s) are not registered for this game' % len(unknown))
    proofs, identities = ryujinx_store_proofs(m)
    copied, reused, skipped, nand, created, selected = 0, 0, 0, 0, [], 0
    for t in tokens:
        row = rows[t]
        if row['state'] == 'IN_GCM':
            skipped += 1
            continue
        external = [r for r in row['records'] if r['origin'] == 'External' and Path(r['path']).is_file()]
        if not external:
            nand += 1
            continue
        record = sorted(external, key=lambda r: r['path'])[0]
        destination = store_dir(kind, row['app']) / Path(record['path']).name
        result = copy_verified(record['path'], destination, record['sha256'], progress)
        copied += result == 'copied'; reused += result == 'reused'; selected += 1
        if result == 'copied':
            created.append(destination)
        stats = catalog.identity(destination)
        proofs[os.path.realpath(destination)] = (stats, proof_only(record))
        identities[os.path.realpath(destination)] = (stats, {k: record[k] for k in ('identified', 'title_id', 'application_id', 'content_type', 'internal_version') if k in record} | {'path': str(destination)})
    if selected:
      try:
        with contextlib.ExitStack() as stack:
            lock = m.safe(m.STATE / 'citron-register.lock'); lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            handle = stack.enter_context(lock.open('a+')); fcntl.flock(handle, fcntl.LOCK_EX)
            text = catalog.CONFIG.read_bytes().decode('utf-8')
            _, current = m.citron_external_dirs(text)
            # First add the store folders (the copies then exist twice), then drop fully copied originals.
            staged = citron_apply_dirs([d for d in current if d not in (str(STORE / 'dlc'), str(STORE / 'updates'))] + [str(STORE / 'dlc'), str(STORE / 'updates')], proofs, identities, progress, 'add store folders')
            target = citron_target_dirs(current, staged['records'])
            if target != [d for d in current if d not in (str(STORE / 'dlc'), str(STORE / 'updates'))] + [str(STORE / 'dlc'), str(STORE / 'updates')]:
                citron_apply_dirs(target, None, None, progress, 'drop fully copied folders')
      except BaseException:
        remove_created(created)
        raise
    return {'copied': copied, 'reused': reused, 'skipped': skipped, 'citron_nand': nand}


# ------------------------------------------------------------------ migration
def old_copies():
    found = []
    for base, owner in ((STORE, 'ryujinx'), (OLD_CITRON, 'citron')):
        if not base.is_dir():
            continue
        for app_dir in sorted(base.iterdir()):
            if not re.fullmatch('[0-9A-F]{16}', app_dir.name):
                continue
            for kind, sub in (('dlc', 'dlc'), ('update', 'updates')):
                folder = app_dir / sub
                if folder.is_dir():
                    for f in sorted(folder.iterdir()):
                        if f.is_file() and f.suffix.lower() == '.nsp' and not f.is_symlink():
                            found.append({'path': f, 'app': app_dir.name, 'kind': kind, 'owner': owner})
    return found


def migrate(roms, dry_run, progress):
    """roms: base ROM paths of games with Ryujinx registrations to re-point."""
    m = inplace()
    hashes = {}
    integrity = m.load('ryujinx_integrity')
    if not roms:
        roms = [r for r in m.ryujinx_read_store(integrity) if Path(r).exists()]
    games = {}
    known = {}
    for rom in roms:
        g = m.ryujinx_fresh(rom, lambda *a: None)
        games[str(Path(rom).resolve())] = g
        for r in g['records']:
            hashes[os.path.realpath(r['registered_path'])] = r['sha256']
            known[os.path.realpath(r['registered_path'])] = r
    cstate = m.citron_fresh(lambda *a: None, heal=not dry_run)  # a dry run writes nothing
    for r in cstate['records']:
        if r['origin'] == 'External':
            hashes[os.path.realpath(r['path'])] = r['sha256']
            known.setdefault(os.path.realpath(r['path']), r)
    plan = []
    for item in old_copies():
        src = item['path']
        sha = hashes.get(os.path.realpath(src)) or sha256_file(src)
        destination = store_dir(item['kind'], item['app']) / src.name
        planned = [p for p in plan if p['destination'] == destination]
        if planned and planned[0]['sha'] != sha:
            raise ValueError('Two different files would share the store name: ' + src.name)
        if destination.exists() and not planned:
            action = 'duplicate' if sha256_file(destination) == sha else 'conflict'
        else:
            action = 'duplicate' if planned else 'move'
        if action == 'conflict':
            raise ValueError('A different file already uses the store name: ' + str(destination))
        plan.append({'source': src, 'destination': destination, 'sha': sha, 'action': action, 'kind': item['kind'], 'owner': item['owner']})
    for p in plan:
        progress('%-9s %s -> %s' % (p['action'].upper(), p['source'], p['destination']))
    if dry_run or not plan:
        return {'planned': len(plan), 'moved': 0, 'duplicates': sum(p['action'] == 'duplicate' for p in plan)}
    m.ryujinx_ensure_closed()
    m.load('citron_toggle').ensure_closed()
    folder = m.transaction_folder('store-migrate')
    review = REVIEW / folder.name
    done = []
    # Ryujinx JSON files as they were before migration, restored if any later step fails.
    adapter, _ = integrity.dependencies()
    saved = []
    for rom in games:
        for kind in ('updates', 'dlc'):
            path = m.safe(games[rom]['native_snapshot']['configuration'][kind]['path'])
            raw, _ = adapter.read_configuration(path, None)
            saved.append((path, raw))
            if raw is not None:
                m.atomic(folder / ('%s-%s.before.json' % (games[rom]['native_snapshot']['application_id'], kind)), raw)
    m.write_json(folder / 'journal.json', {'state': 'prepared', 'plan': [{k: str(v) for k, v in p.items()} for p in plan]})
    try:
        for p in plan:
            p['destination'].parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            if p['action'] == 'move':
                os.rename(p['source'], p['destination'])
            else:
                target = review / p['source'].relative_to(CONTENT)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.rename(p['source'], target)
                p['review'] = target
            done.append(p)
        mapping = {os.path.realpath(p['source']): str(p['destination']) for p in plan}
        for rom, g in games.items():
            old_paths = {os.path.realpath(r['registered_path']) for r in g['records']}
            local = {k: v for k, v in mapping.items() if k in old_paths}
            if local:
                proofs = {os.path.realpath(v): (integrity.identity(v), proof_only(r)) for r in g['records']
                          for k, v in local.items() if os.path.realpath(r['registered_path']) == k}
                ryujinx_repoint(rom, local, proofs, progress, 'migrate', g['records'])
        old_by_path = known
        catalog = m.load('citron_catalog')
        proofs, identities = {}, {}
        for old, new in mapping.items():
            r = old_by_path.get(old)
            if r is not None and Path(new).exists():
                stats = catalog.identity(Path(new))
                proofs[os.path.realpath(new)] = (stats, proof_only(r))
                identities[os.path.realpath(new)] = (stats, {k: r[k] for k in ('identified', 'title_id', 'application_id', 'content_type', 'internal_version') if k in r} | {'path': new})
        _, current = m.citron_external_dirs(catalog.CONFIG.read_bytes().decode('utf-8'))
        target = [d for d in current if os.path.realpath(d) != os.path.realpath(OLD_CITRON)]
        target = [d for d in target if d not in (str(STORE / 'dlc'), str(STORE / 'updates'))] + [str(STORE / 'dlc'), str(STORE / 'updates')]
        citron_apply_dirs(target, proofs, identities, progress, 'migrate')
    except BaseException as exc:
        for p in reversed(done):
            if p['action'] == 'move' and p['destination'].exists() and not p['source'].exists():
                os.rename(p['destination'], p['source'])
            elif p.get('review') and p['review'].exists() and not p['source'].exists():
                os.rename(p['review'], p['source'])
        for path, raw in saved:
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                m.atomic(path, raw)
        try:
            m.ryujinx_store_update(list(games), progress=progress)
        finally:
            m.write_json(folder / 'journal.json', {'state': 'rolled_back', 'error': str(exc)[:400]})
        raise
    for base in (STORE, OLD_CITRON):
        for d in sorted((x for x in base.rglob('*') if x.is_dir()), key=lambda x: len(x.parts), reverse=True) if base.exists() else []:
            if d.is_relative_to(REVIEW) or d.is_relative_to(STORE / 'dlc') or d.is_relative_to(STORE / 'updates') or d.name == 'imported-installed' or 'imported-installed' in d.parts:
                continue
            with contextlib.suppress(OSError):
                d.rmdir()
    with contextlib.suppress(OSError):
        OLD_CITRON.rmdir()
    m.write_json(folder / 'journal.json', {'state': 'committed', 'moved': sum(p['action'] == 'move' for p in plan),
                                           'duplicates_for_review': str(review)})
    return {'planned': len(plan), 'moved': sum(p['action'] == 'move' for p in plan),
            'duplicates': sum(p['action'] == 'duplicate' for p in plan), 'review': str(review)}


# ------------------------------------------------------------------ entry
def canonical(emulator):
    e = {'ryujinx-emu': 'ryujinx', 'citron': 'citron-emu'}.get(emulator, emulator)
    if e not in ('ryujinx', 'citron-emu'):
        raise ValueError('Copy to GCM is not connected for this emulator')
    return e


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('list', 'copy', 'migrate'))
    p.add_argument('--rom', action='append', default=[])
    p.add_argument('--emulator')
    p.add_argument('--type', choices=('dlc', 'update'), default='dlc')
    p.add_argument('--tokens', default='')
    p.add_argument('--confirm', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    log = lambda *x: print(*x, file=sys.stderr, flush=True)
    if a.action == 'migrate':
        if not a.dry_run and not a.confirm:
            raise ValueError('Use --dry-run first, then --confirm')
        result = migrate(a.rom, a.dry_run, lambda *x: print('PLAN' if a.dry_run else 'DONE', *x, flush=True))
        print('OK\tMIGRATE\t' + json.dumps(result))
        return
    if len(a.rom) != 1 or not a.emulator:
        raise ValueError('--rom and --emulator required')
    e = canonical(a.emulator)
    if a.action == 'list':
        rows = ryujinx_rows(a.rom[0], a.type) if e == 'ryujinx' else citron_rows(a.rom[0], a.type)
        for row in rows:
            print('\t'.join(('ROW', 'AVAILABLE', row['state'], row['label'].replace('\t', ' '), row['token'])))
        return
    if not a.confirm:
        raise ValueError('Confirm the copy with --confirm')
    tokens = [t for t in a.tokens.split(',') if t]
    if not tokens or len(tokens) != len(set(tokens)):
        raise ValueError('Select at least one item, each once')
    result = ryujinx_copy(a.rom[0], a.type, tokens, log) if e == 'ryujinx' else citron_copy(a.rom[0], a.type, tokens, log)
    print('OK\tCOPY\t' + '\t'.join('%s=%s' % (k.upper(), v) for k, v in result.items()))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR\t' + str(exc).replace('\t', ' ').replace('\n', ' ')[:400])
        sys.exit(1)
