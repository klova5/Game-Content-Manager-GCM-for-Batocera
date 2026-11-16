"""Switch DLC: register in place on Verify, and keep verified snapshots fresh by proof reuse.

Ryujinx: verified DLC are added to the game's dlc.json at their current path, disabled.
Citron: the DLC's folder is added to Citron's external content folders; every DLC in it
is first added to the game's disabled list, so nothing turns on until Apply Changes.

Snapshots (the state files the existing validators read) are rebuilt with the same pinned
verifiers and the same checks as the existing refresh functions, except that a file whose
identity (real path, device, inode, size, modification and change time) is unchanged keeps
its earlier proof instead of being hashed again. New or changed files are always verified.
No existing GCM module is modified.
"""
import argparse, concurrent.futures, contextlib, copy, fcntl, hashlib, importlib.util, json, os, re, struct, subprocess, sys, tempfile, time, uuid
from pathlib import Path

T = Path('/userdata/system/gcm-native-test')
SWITCH = T / 'step60-shared-switch'
COMPONENTS = T / 'step59-citron-backend-v01'
STATE = T / 'step61-common-content/state/switch-dlc-inplace'
PINS = {
    'ryujinx_integrity': (SWITCH / 'ryujinx-installed-integrity-v02-measured-v01.py', '144796758dcdd3934b2b7a8655509c676f147d0ba6891c83f8591977225ce694'),
    'citron_catalog': (SWITCH / 'citron-content-catalog.py', '6dec4983cb4c7f6707d4604db6f11c5cca01d0d1a9e2f20169e5df84d1e79cfa'),
    'citron_integrity': (SWITCH / 'citron-installed-integrity.py', '987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc'),
    'citron_rows': (SWITCH / 'citron-installed-rows-measured-v01.py', 'c6e0536e81ae6841afbe205301f3a212963af76cd4da12fe3a8c29f3190d846e'),
    'citron_plan': (SWITCH / 'citron-content-plan.py', 'aae0d5039b57b6a497d4415a0268bcb69195fe218e7adef52ad6e7f194be6b56'),
    'citron_transaction': (SWITCH / 'citron-configuration-transaction-measured-v01.py', '57fdca525e8ef8b5bb8a051050d3ac48cd8189dfb3f4114c43ceb6ba9ef05bdd'),
    'citron_toggle': (COMPONENTS / 'citron-dlc-toggle.py', '28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf'),
    'citron_nand': (COMPONENTS / 'citron-nand-verify-v02.py', '35ca963cd6326d486de4e610773c080892c8246e3836f84d0c849457b6abab60'),
}
WORKERS = 4
_LOADED = {}


def load(key):
    if key not in _LOADED:
        path, expected = PINS[key]
        if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('In-place dependency changed: ' + path.name)
        spec = importlib.util.spec_from_file_location('inplace_' + key, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED[key] = module
    return _LOADED[key]


def boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def safe(p):
    p = Path(p)
    if not p.is_absolute() or any(q.is_symlink() for q in (p, *p.parents)):
        raise ValueError('Unsafe path: ' + str(p))
    return p


def atomic(path, data):
    path = safe(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
        if path.exists():
            os.chmod(temp, path.stat().st_mode & 0o7777)
        os.replace(temp, path)
        d = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, data):
    atomic(path, (json.dumps(data, indent=2) + '\n').encode())


def parallel(fn, items):
    """Run fn over items with a few threads (NSTool and hashing are I/O bound)."""
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                results[item] = (True, future.result())
            except Exception as exc:
                results[item] = (False, str(exc).replace('\n', ' ')[:400])
    return results


def process_running(prefixes):
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            name = (proc / 'comm').read_text().strip().casefold()
        except OSError:
            continue
        if any(name.startswith(p) for p in prefixes):
            return True
    return False


def transaction_folder(kind):
    folder = safe(STATE / 'transactions' / (kind + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]))
    folder.mkdir(mode=0o700, parents=True)
    return folder


def pfs0_nca_names(path):
    """Non-metadata NCA names inside an NSP (bounded PFS0 directory read)."""
    with open(path, 'rb') as f:
        head = f.read(16)
        if len(head) != 16 or head[:4] != b'PFS0':
            raise ValueError('Not a PFS0 NSP: ' + str(path))
        count, size, _ = struct.unpack('<III', head[4:])
        if not 0 < count <= 4096 or size > 1024 * 1024:
            raise ValueError('Invalid NSP directory: ' + str(path))
        table, strings = f.read(count * 24), f.read(size)
        if len(table) != count * 24 or len(strings) != size:
            raise ValueError('Truncated NSP directory: ' + str(path))
    names = []
    for i in range(count):
        _, _, offset, _ = struct.unpack_from('<QQII', table, i * 24)
        end = strings.find(b'\0', offset)
        names.append(strings[offset:end if end >= 0 else len(strings)].decode('utf-8', 'replace'))
    ncas = [n for n in names if n.lower().endswith('.nca') and not n.lower().endswith('.cnmt.nca')]
    if not ncas or any(not re.fullmatch(r'[0-9a-fA-F]{32}\.nca', n) for n in ncas):
        raise ValueError('Unexpected DLC content files in ' + str(path))
    return ncas


# ------------------------------------------------------------------ Ryujinx
def ryujinx_ensure_closed():
    if process_running(('ryujinx',)):
        raise ValueError('Close Ryujinx before changing content')


def ryujinx_read_store(integrity):
    if integrity.STORE.is_symlink() or integrity.STORE.parent.is_symlink():
        raise ValueError('Unsafe Ryujinx integrity state path')
    try:
        data = json.loads(integrity.STORE.read_text())
    except FileNotFoundError:
        return {}
    if data.get('schema') != 'GCM_RYUJINX_SHARED_VERIFIED_INVENTORY_V1':
        return {}
    return data.get('games', {})


def ryujinx_store_update(roms, fresh_proofs=None, progress=print):
    """Same output and checks as ryujinx refresh(), reusing proofs of unchanged files.

    Games already in the store and not named in roms are kept; validate() rechecks
    their registrations and file identities before any use."""
    integrity = load('ryujinx_integrity')
    adapter, engine = integrity.dependencies()
    prior_games = ryujinx_read_store(integrity)
    fresh_proofs = fresh_proofs or {}
    targets = sorted({str(Path(r).resolve(strict=True)) for r in roms})
    games = {}
    reused = verified = 0
    for rom in targets:
        snapshot = adapter.snapshot(rom)
        files = integrity.capture(snapshot)
        prior = prior_games.get(rom) or {}
        prior_ids = prior.get('file_identities', {})
        prior_records = {r['registered_path']: r for r in prior.get('records', [])}
        rows = snapshot['registered']['updates'] + snapshot['registered']['dlc']

        def check(item):
            source = Path(item['path'])
            kind = item['content_type']
            if not source.is_file():
                raise ValueError('Registered package is unavailable')
            if source.resolve(strict=True) == Path(rom):
                raise ValueError('Registered base-game container requires bundled-content inspection')
            if source.suffix.lower() != '.nsp':
                raise ValueError('Registered container type requires additional support')
            old = prior_records.get(str(source))
            if old is not None and prior_ids.get(item['path']) == files.get(item['path']) and old.get('content_type') == kind:
                return dict(old), True
            given = fresh_proofs.get(os.path.realpath(source))
            if given is not None and given[0] == files.get(item['path']):
                return dict(given[1]), False
            return engine.verify(rom, source, kind), False

        outcome = parallel(lambda i: check(rows[i]), range(len(rows)))
        records, problems = [], []
        for index, item in enumerate(rows):
            ok, value = outcome[index]
            source = Path(item['path'])
            kind = item['content_type']
            if ok:
                proof, was_reused = value
                if (proof['application_id'] != snapshot['application_id'] or proof['content_type'] != kind
                        or proof.get('status') != 'PASS' or proof.get('primary_content_hashes_verified') is not True
                        or proof.get('present_content_hashes_verified') is not True):
                    ok, value = False, 'Registered package proof mismatch'
                else:
                    proof.update(registered_path=str(source), origin='Ryujinx', active=bool(item.get('active')))
                    records.append(proof)
                    reused += was_reused
                    verified += not was_reused
            if not ok:
                problems.append({'path': str(source), 'content_type': kind, 'error': value})
        if adapter.snapshot(rom) != snapshot or integrity.capture(snapshot) != files:
            raise ValueError('Ryujinx configuration or registered files changed')
        games[rom] = {'native_snapshot': snapshot, 'file_identities': files, 'records': records,
                      'problems': problems, 'all_registered_content_verified': not problems}
    for rom, game in prior_games.items():
        if rom not in games:
            games[rom] = game
    for rom in targets:
        game = games[rom]
        if adapter.snapshot(rom) != game['native_snapshot'] or integrity.capture(game['native_snapshot']) != game['file_identities']:
            raise ValueError('Library changed before snapshot completion')
    write_json(integrity.STORE, {'schema': 'GCM_RYUJINX_SHARED_VERIFIED_INVENTORY_V1', 'boot_id': boot(), 'games': games})
    progress('RYUJINX SNAPSHOT: games=%d reused=%d verified=%d' % (len(targets), reused, verified))
    return games


def ryujinx_fresh(rom, progress=print):
    integrity = load('ryujinx_integrity')
    try:
        return integrity.validate(rom)
    except (ValueError, FileNotFoundError, json.JSONDecodeError):
        pass
    games = ryujinx_read_store(integrity)
    current = boot()
    try:
        stale_boot = json.loads(integrity.STORE.read_text()).get('boot_id') != current
    except (FileNotFoundError, json.JSONDecodeError):
        stale_boot = True
    roms = [str(Path(rom).resolve(strict=True))]
    if stale_boot:
        roms += [r for r in games if Path(r).exists()]
    ryujinx_store_update(roms, progress=progress)
    return integrity.validate(rom)


def ryujinx_register(rom, packages, progress=print):
    """packages: [(source_path, dlc_title_id)] already identity-checked for this game."""
    integrity = load('ryujinx_integrity')
    adapter, engine = integrity.dependencies()
    rom = str(Path(rom).resolve(strict=True))
    lock = SWITCH / 'state/ryujinx-configuration.lock'
    safe(lock).parent.mkdir(mode=0o700, exist_ok=True)
    with lock.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        ryujinx_ensure_closed()
        ryujinx_fresh(rom, progress)
        snapshot = adapter.snapshot(rom)
        app = snapshot['application_id']
        config = snapshot['configuration']['dlc']
        path = safe(config['path'])
        raw, document = adapter.read_configuration(path, [])
        registered_paths = {os.path.realpath(e['path']) for e in document}
        registered_titles = {int(n['title_id']) for e in document for n in e.get('dlc_nca_list', []) if isinstance(n, dict) and 'title_id' in n}
        wanted = []
        for source, title in packages:
            title_value = int(title, 16)
            if not (0x1000 <= title_value - int(app, 16) <= 0x1FFF):
                raise ValueError('DLC title does not belong to this game: ' + title)
            if os.path.realpath(source) in registered_paths or title_value in registered_titles:
                continue
            wanted.append((str(Path(source)), title.upper()))
        if not wanted:
            progress('RYUJINX: nothing new to register')
            return {'registered': 0, 'already': len(packages), 'failed': []}
        progress('RYUJINX: verifying %d new DLC' % len(wanted))
        checks = parallel(lambda s: (integrity.identity(s), engine.verify(rom, Path(s), 'dlc')), [s for s, _ in wanted])
        failed, entries, proofs = [], [], {}
        for source, title in wanted:
            ok, value = checks[source]
            if not ok or value[1].get('title_id') != title or value[1].get('application_id') != app or value[1].get('status') != 'PASS':
                failed.append({'source': source, 'error': value if not ok else 'Proof does not match DLC identity'})
                continue
            entries.append({'path': source, 'dlc_nca_list': [
                {'path': '/' + n, 'title_id': int(title, 16), 'is_enabled': False} for n in pfs0_nca_names(source)]})
            proofs[os.path.realpath(source)] = value
        if not entries:
            return {'registered': 0, 'already': len(packages) - len(wanted), 'failed': failed}
        folder = transaction_folder('ryujinx')
        if raw is not None:
            atomic(folder / 'dlc.before.json', raw)
        write_json(folder / 'journal.json', {'state': 'prepared', 'application_id': app, 'config': str(path), 'added': entries})
        ryujinx_ensure_closed()
        current, _ = adapter.read_configuration(path, [])
        if current != raw:
            raise ValueError('Ryujinx DLC configuration changed during planning')
        after = list(document) + entries
        try:
            atomic(path, (json.dumps(after, indent=2) + '\n').encode())
            ryujinx_store_update([rom], proofs, progress)
            game = integrity.validate(rom)
            added = {os.path.realpath(e['path']) for e in entries}
            ok_paths = {os.path.realpath(r['registered_path']) for r in game['records']}
            if not added <= ok_paths or any(r['active'] for r in game['records'] if os.path.realpath(r['registered_path']) in added):
                raise ValueError('Registered DLC did not verify as disabled')
        except BaseException as exc:
            if raw is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, raw)
            try:
                ryujinx_store_update([rom], progress=progress)
            finally:
                write_json(folder / 'journal.json', {'state': 'rolled_back', 'error': str(exc)[:400], 'application_id': app})
            raise
        write_json(folder / 'journal.json', {'state': 'registered_disabled', 'application_id': app, 'config': str(path), 'added': entries})
        progress('RYUJINX: registered %d DLC disabled' % len(entries))
        return {'registered': len(entries), 'already': len(packages) - len(wanted), 'failed': failed, 'transaction': str(folder)}


# ------------------------------------------------------------------ Citron
def citron_catalog_build(prior, progress=print, known_items=None):
    """Same records as citron-content-catalog refresh(), reusing unchanged files."""
    catalog = load('citron_catalog')
    packages, parser, reader = catalog.dependencies()
    before_config = hashlib.sha256(catalog.CONFIG.read_bytes()).hexdigest()
    before = catalog.capture()
    prior_ids = (prior or {}).get('inventory', {}).get('files', {})
    prior_records = {r['path']: r for r in (prior or {}).get('records', [])}
    jobs, order, seen = [], [], set()
    for location in before['roots']:
        for path in catalog.files_under(location['path'], location['depth']):
            if str(path) in seen:
                continue
            seen.add(str(path))
            order.append((location, path))
    def one(index):
        location, path = order[index]
        origin = location['origin']
        unchanged = prior_ids.get(str(path)) == before['files'].get(str(path)) and str(path) in prior_ids
        if unchanged:
            old = prior_records.get(str(path))
            if old is None or old.get('registered_root') == location['path']:
                return (dict(old) if old else None), True
        if origin == 'External':
            if path.suffix.lower() in ('.xci', '.xcz', '.nsz', '.nca', '.dnsp', '.dxci'):
                raise ValueError('External container reader required: ' + str(path))
            if path.suffix.lower() != '.nsp':
                return None, False
            given = (known_items or {}).get(os.path.realpath(path))
            item = given[1] if given is not None and given[0] == catalog.identity(path) else reader.identify(path)
            if item['content_type'] not in ('update', 'dlc'):
                return None, False
            return {**item, 'origin': origin, 'path': str(path), 'registered_root': location['path'],
                    'stat_identity': catalog.identity(path), 'primary_content_hashes_verified': False}, False
        if not re.fullmatch(r'[0-9a-fA-F]{32}(?:\.cnmt)?\.nca', path.name):
            raise ValueError('Unhandled NAND registration file: ' + str(path))
        result = subprocess.run([str(reader.NSTOOL), '-k', str(reader.KEYSET), '-t', 'nca', str(path)], capture_output=True, text=True, timeout=30)
        kinds = re.findall(r'^\s*Content Type:\s*([A-Za-z0-9_]+)\s*$', result.stdout, re.MULTILINE)
        if result.returncode or len(kinds) != 1:
            raise ValueError('NAND header inspection failed: ' + str(path))
        if kinds[0] != 'Meta':
            return None, False
        if not 0 < path.stat().st_size <= reader.MAX_META_NCA:
            raise ValueError('Metadata size outside bounds')
        digest = packages.digest(path)
        if digest[:32] != path.name.split('.')[0].lower():
            raise ValueError('NAND metadata content ID/hash mismatch')
        with tempfile.TemporaryDirectory(prefix='citron-catalog-') as temporary:
            extracted = subprocess.run([str(reader.NSTOOL), '-k', str(reader.KEYSET), '-t', 'nca', '-x', '/0', temporary, str(path)], capture_output=True, timeout=90)
            found = list(Path(temporary).rglob('*.cnmt'))
            if extracted.returncode or len(found) != 1:
                raise ValueError('NAND metadata extraction failed')
            cnmt = parser.parse_cnmt(found[0])
        if cnmt['type'] not in (0x81, 0x82):
            return None, False
        return {'application_id': cnmt['related_id'], 'title_id': cnmt['title_id'],
                'content_type': 'update' if cnmt['type'] == 0x81 else 'dlc', 'internal_version': cnmt['version'],
                'origin': origin, 'path': str(path), 'registered_root': location['path'],
                'stat_identity': catalog.identity(path), 'metadata_sha256': digest,
                'primary_content_hashes_verified': False}, False
    outcome = parallel(one, range(len(order)))
    records, reused = [], 0
    for index in range(len(order)):
        ok, value = outcome[index]
        if not ok:
            raise ValueError(value)
        record, was_reused = value
        reused += was_reused
        if record is not None:
            records.append(record)
    if catalog.capture() != before or hashlib.sha256(catalog.CONFIG.read_bytes()).hexdigest() != before_config:
        raise ValueError('Registrations or configuration changed during catalog creation')
    progress('CITRON CATALOG: files=%d reused=%d records=%d' % (len(order), reused, len(records)))
    return {'schema': 'GCM_CITRON_GLOBAL_METADATA_CATALOG_V1', 'boot_id': boot(), 'inventory': before,
            'records': records, 'content_integrity_complete': False}


def citron_integrity_build(metadata, prior, fresh_proofs=None, progress=print):
    """Same records as citron-installed-integrity refresh(), reusing unchanged files."""
    integrity = load('citron_integrity')
    catalog = integrity.catalog_module()
    packages, _, reader = catalog.dependencies()
    nand = load('citron_nand')
    fresh_proofs = fresh_proofs or {}
    prior_records = {r['path']: r for r in (prior or {}).get('records', [])}
    before_config = catalog.CONFIG.read_bytes()
    items = metadata['records']
    def one(index):
        item = items[index]
        path = Path(item['path'])
        if catalog.identity(path) != item['stat_identity']:
            raise ValueError('Registration changed before verification')
        old = prior_records.get(str(path))
        if (old is not None and old.get('stat_identity') == item['stat_identity'] and old.get('origin') == item['origin']
                and all(old.get(k) == item.get(k) for k in ('application_id', 'title_id', 'internal_version', 'content_type'))):
            return {k: v for k, v in old.items()}, True
        given = fresh_proofs.get(os.path.realpath(path))
        if given is not None and given[0] == item['stat_identity'] and item['origin'] == 'External':
            proof = dict(given[1])
        elif item['origin'] == 'External':
            proof = packages.verify(path, item['application_id'], item['content_type'])
        else:
            proof = nand.verify_record(item['registered_root'], path, item['application_id'], item['content_type'])
            if item['content_type'] == 'update':
                proof.update(integrity.control_display(proof, packages, reader))
        if (proof['application_id'] != item['application_id'] or proof['title_id'] != item['title_id']
                or proof['internal_version'] != item['internal_version']
                or proof.get('primary_content_hashes_verified') is not True
                or proof.get('present_content_hashes_verified') is not True
                or catalog.identity(path) != item['stat_identity']):
            raise ValueError('Installed content proof mismatch: ' + str(path))
        return {**item, **proof, 'origin': item['origin'], 'registered_root': item['registered_root'],
                'path': str(path), 'stat_identity': catalog.identity(path)}, False
    outcome = parallel(one, range(len(items)))
    records, reused = [], 0
    for index in range(len(items)):
        ok, value = outcome[index]
        if not ok:
            raise ValueError(value)
        records.append(value[0]); reused += value[1]
    if catalog.capture() != metadata['inventory'] or catalog.CONFIG.read_bytes() != before_config:
        raise ValueError('Content or configuration changed during integrity verification')
    progress('CITRON SNAPSHOT: records=%d reused=%d verified=%d' % (len(records), reused, len(records) - reused))
    return {'schema': 'GCM_CITRON_GLOBAL_VERIFIED_INVENTORY_V1', 'boot_id': boot(), 'inventory': metadata['inventory'],
            'records': records, 'content_integrity_complete': True}


def citron_read(path):
    try:
        return json.loads(safe(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def citron_store_update(fresh_proofs=None, progress=print, known_items=None):
    catalog, integrity = load('citron_catalog'), load('citron_integrity')
    metadata = citron_catalog_build(citron_read(catalog.STORE), progress, known_items)
    result = citron_integrity_build(metadata, citron_read(integrity.STORE), fresh_proofs, progress)
    write_json(catalog.STORE, metadata)
    write_json(integrity.STORE, result)
    integrity.validate()
    return result


def citron_fresh(progress=print):
    integrity = load('citron_integrity')
    try:
        return integrity.validate()
    except (ValueError, FileNotFoundError, json.JSONDecodeError, KeyError):
        return citron_store_update(progress=progress)


def citron_external_dirs(text):
    size = re.findall(r'^Paths\\external_content_dirs\\size=(\d+)$', text, re.MULTILINE)
    if len(size) != 1:
        raise ValueError('Citron external folder count not found exactly once')
    count = int(size[0])
    dirs = []
    for i in range(1, count + 1):
        found = re.findall(r'^Paths\\external_content_dirs\\%d\\path=(.*)$' % i, text, re.MULTILINE)
        if len(found) != 1:
            raise ValueError('Citron external folder %d missing' % i)
        dirs.append(found[0])
    return count, dirs


def citron_add_dirs(text, folders):
    count, dirs = citron_external_dirs(text)
    for folder in folders:
        if any(c in folder for c in '\n\r"\\,;=') or folder != folder.strip():
            raise ValueError('Folder name needs Qt escaping; move or rename it: ' + folder)
        count += 1
        line = 'Paths\\external_content_dirs\\%d\\path=%s' % (count, folder)
        pattern = re.compile(r'^Paths\\external_content_dirs\\%d\\path=.*$' % count, re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda m: line, text, count=1)
        else:
            text = re.sub(r'^(Paths\\external_content_dirs\\size=)\d+$', lambda m: line + '\n' + m.group(0), text, count=1, flags=re.MULTILINE)
    text = re.sub(r'^(Paths\\external_content_dirs\\size=)\d+$', lambda m: m.group(1) + str(count), text, count=1, flags=re.MULTILINE)
    return text


def citron_register(rom, packages_in, progress=print):
    """packages_in: [(source_path, dlc_title_id)] identity-checked for this game."""
    catalog, integrity = load('citron_catalog'), load('citron_integrity')
    rows, plan, transaction, toggle = load('citron_rows'), load('citron_plan'), load('citron_transaction'), load('citron_toggle')
    packages, _, reader = catalog.dependencies()
    config = safe(catalog.CONFIG)
    lock = safe(STATE / 'citron-register.lock')
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock.open('a+') as handle:  # transaction.commit takes its own configuration lock
        fcntl.flock(handle, fcntl.LOCK_EX)
        toggle.ensure_closed()
        state = citron_fresh(progress)
        registered = {os.path.realpath(r['path']) for r in state['records'] if r['origin'] == 'External'}
        roots = [Path(r['path']) for r in state['inventory']['roots'] if r['origin'] == 'External']
        sources = [(s, t) for s, t in packages_in if os.path.realpath(s) not in registered]
        if not sources:
            progress('CITRON: nothing new to register')
            return {'registered': 0, 'already': len(packages_in), 'failed': []}
        folders = sorted({str(Path(os.path.realpath(s)).parent) for s, _ in sources})
        new_folders = [f for f in folders if not any(Path(f) == r or Path(f).is_relative_to(r) for r in roots)]
        for f in new_folders:
            if any(r.is_relative_to(Path(f)) for r in roots):
                raise ValueError('Folder contains an existing Citron folder; register a narrower folder: ' + f)
        # Citron registers every package in a folder, so every package in a new folder must verify.
        scan = []
        for f in new_folders:
            for path in catalog.files_under(f, 15):
                if path.suffix.lower() in ('.xci', '.xcz', '.nsz', '.nca', '.dnsp', '.dxci'):
                    raise ValueError('Folder holds a container Citron cannot register as external content: ' + str(path))
                if path.suffix.lower() == '.nsp':
                    scan.append(str(path))
        progress('CITRON: verifying %d packages in %d new folder(s)' % (len(scan), len(new_folders)))
        def check(path):
            item = reader.identify(Path(path))
            if item['content_type'] not in ('update', 'dlc'):
                return item, None
            stats = catalog.identity(Path(path))
            proof = packages.verify(Path(path), item['application_id'], item['content_type'])
            if stats != catalog.identity(Path(path)) or proof.get('title_id') != item['title_id']:
                raise ValueError('Package changed or identity disagrees')
            return item, (stats, proof, item)
        results = parallel(check, scan)
        bad = {p: v for p, (ok, v) in results.items() if not ok}
        if bad:
            raise ValueError('%d package(s) in the folder failed verification, e.g. %s: %s' % (len(bad), *next(iter(bad.items()))))
        proofs, identities, new_titles = {}, {}, {}
        for path, (_, (item, proof)) in results.items():
            if proof is not None:
                proofs[os.path.realpath(path)] = proof[:2]
                identities[os.path.realpath(path)] = (proof[0], proof[2])
                if item['content_type'] == 'dlc':
                    new_titles.setdefault(item['application_id'], set()).add(item['title_id'])
        known = {(r['application_id'], r['title_id']) for r in state['records'] if r['content_type'] == 'dlc'}
        folder = transaction_folder('citron')
        original = config.read_bytes()
        atomic(folder / 'qt-config.before.ini', original)
        write_json(folder / 'journal.json', {'state': 'prepared', 'folders': new_folders, 'titles': {a: sorted(t) for a, t in new_titles.items()}})
        try:
            # 1. Disable the new DLC titles first, so nothing turns on when the folder appears.
            for app, titles in sorted(new_titles.items()):
                fresh = sorted(t for t in titles if (app, t) not in known)
                old = rows.disabled_keys(app)
                if 'DLC' in old or not fresh:
                    continue
                desired = old + [plan.dlc_key(t) for t in fresh if plan.dlc_key(t) not in old]
                current_hash = hashlib.sha256(config.read_bytes()).hexdigest()
                def result_check(a=app, d=desired):
                    if rows.disabled_keys(a) != d:
                        raise ValueError('Disabled list post-check failed')
                transaction.commit(app, desired, current_hash, operation='set-dlc', config_path=config,
                                   validate_inventory=lambda: None, validate_result=result_check)
            # 2. Add the folder(s) to Citron's external content list.
            if new_folders:
                toggle.ensure_closed()
                text = config.read_bytes().decode('utf-8')
                atomic(config, citron_add_dirs(text, new_folders).encode('utf-8'))
                configured = [r['path'] for r in catalog.storage() if r['origin'] == 'External']
                if any(str(Path(f).resolve()) not in configured for f in new_folders):
                    raise ValueError('Citron did not read back the new external folder')
            # 3. Rebuild the snapshot (new packages reuse the proofs computed above).
            result = citron_store_update(proofs, progress, identities)
            for app, titles in new_titles.items():
                disabled = set(rows.disabled_keys(app))
                for t in titles:
                    if (app, t) not in known and 'DLC' not in disabled and plan.dlc_key(t) not in disabled:
                        raise ValueError('A newly registered DLC is not disabled: ' + t)
            missing = [s for s, _ in sources if os.path.realpath(s) not in {os.path.realpath(r['path']) for r in result['records']}]
            if missing:
                raise ValueError('Selected DLC not registered by its folder: ' + missing[0])
        except BaseException as exc:
            toggle.ensure_closed()
            atomic(config, original)
            try:
                citron_store_update(progress=progress)
            finally:
                write_json(folder / 'journal.json', {'state': 'rolled_back', 'error': str(exc)[:400], 'folders': new_folders})
            raise
        write_json(folder / 'journal.json', {'state': 'registered_disabled', 'folders': new_folders,
                                             'titles': {a: sorted(t) for a, t in new_titles.items()}})
        count = sum(len(t) for t in new_titles.values())
        progress('CITRON: registered %d package(s) disabled from %d folder(s)' % (len(scan), len(new_folders)))
        return {'registered': len(sources), 'folder_packages': len(scan), 'dlc_titles': count, 'folders': new_folders,
                'failed': [], 'transaction': str(folder)}


# ------------------------------------------------------------------ entry points
def canonical(emulator):
    e = {'ryujinx-emu': 'ryujinx', 'citron': 'citron-emu'}.get(emulator, emulator)
    if e not in ('ryujinx', 'citron-emu'):
        raise ValueError('In-place DLC registration supports Ryujinx and Citron')
    return e


def register(rom, emulator, packages, progress=print):
    if canonical(emulator) == 'ryujinx':
        return ryujinx_register(rom, packages, progress)
    return citron_register(rom, packages, progress)


def fresh(rom, emulator, progress=print):
    if canonical(emulator) == 'ryujinx':
        ryujinx_fresh(rom, progress)
    else:
        citron_fresh(progress)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('fresh', 'register'))
    p.add_argument('--rom', required=True)
    p.add_argument('--emulator', required=True)
    p.add_argument('--package', action='append', default=[], help='PATH=DLC_TITLE_ID (register only)')
    p.add_argument('--confirm', action='store_true')
    a = p.parse_args()
    log = lambda *x: print(*x, file=sys.stderr, flush=True)
    if a.action == 'fresh':
        fresh(a.rom, a.emulator, log)
        print('OK\tFRESH')
        return
    if not a.confirm:
        raise ValueError('Confirm registration with --confirm')
    packages = []
    for item in a.package:
        path, _, title = item.rpartition('=')
        if not path or not re.fullmatch('[0-9A-Fa-f]{16}', title):
            raise ValueError('Use --package PATH=DLC_TITLE_ID')
        packages.append((path, title.upper()))
    result = register(a.rom, a.emulator, packages, log)
    print('OK\tREGISTERED=%d\tFAILED=%d' % (result['registered'], len(result['failed'])))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR\t' + str(exc).replace('\t', ' ').replace('\n', ' ')[:400])
        sys.exit(1)
