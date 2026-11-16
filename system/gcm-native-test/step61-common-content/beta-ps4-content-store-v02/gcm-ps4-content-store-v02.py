"""PS4 COPY SELECTED TO GCM: batch import of installed shadPS4 content into GCM storage (v02).

v02: content the GCM installed itself (a GCM record and managed copy exist) shows IN GCM even
when its files have not been re-checked this session; v01 showed it as CHECK NEEDED.

list      rows for the GCM picker: IN_GCM (a ready GCM copy exists, or GCM itself installed it),
          EXTERNAL (installed by the emulator only; can be copied) or CHECK (needs assessment).
copy      copies the selected items one after another with the existing pinned Install Locally
          action (game-bound, file-by-file hashed, originals and enablement untouched).
leftovers read-only report of the GCM parking folders inside shadPS4's DLC folder.
The PS4 storage layout (game-content/ps4/<GAME ID>/...) is unchanged.
"""
import argparse, hashlib, importlib.util, json, os, sys, time
from pathlib import Path

ROOT = Path('/userdata/system/gcm-native-test/step61-common-content')
LOCAL = (ROOT / 'gcm-global-install-locally-v01.py', '58043a4915c5c0890bdd061935bad7d6e2c4070f27e65a86e9b6b93f029eb2b2')
ADDONS = Path('/userdata/roms/ps4/DLC')
PARKING = ('.gcm-transactions', '.gcm-uninstalled')
_MOD = []


def provider():
    if not _MOD:
        path, want = LOCAL
        if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != want:
            raise ValueError('PS4 copy dependency changed: ' + path.name)
        spec = importlib.util.spec_from_file_location('gcm_ps4_install_locally', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        _MOD.append(module)
    return _MOD[0]


def token(row):
    return 'existing:' + row['source_root'].encode().hex()


def label(row):
    text = ('Update ' + str(row['version'])) if row['content_type'] == 'update' else ('DLC ' + str(row['content_id'])[-16:])
    return ''.join(c if 32 <= ord(c) != 127 and c != '\t' else ' ' for c in text)[:150]


def stored_copy(m, row, doc):
    """A ready GCM copy of this installed item whose files and original are unchanged (any boot)."""
    copier = m.load('gcm-installed-content-copy-v01.py')
    parent = copier.safe(m.MANAGED / 'ps4' / doc['application_id'] / 'imported-installed' / row['content_type'])
    for path in sorted(parent.glob('*/record.json')) if parent.exists() else []:
        copier.safe(path)
        if path.stat().st_size > 64 * 1024 * 1024:
            continue
        record = json.loads(path.read_text())
        if (record.get('schema') != 'GCM_IMPORTED_INSTALLED_CONTENT_V1' or record.get('import_ready') is not True
                or record.get('original_root') != row['source_root'] or record.get('binding') != row['binding']):
            continue
        managed = copier.safe(record['managed_root'])
        if managed != path.parent / 'files':
            continue
        if copier.tree(Path(row['source_root'])) == record['source_snapshot'] and copier.tree(managed) == record['managed_snapshot']:
            return record
    return None


def rows(rom, kind):
    m = provider()
    doc = m.inventory(rom, 'shadps4')
    out = []
    for row in doc['rows']:
        if row['content_type'] != kind:
            continue
        if not row.get('install_locally_available', False):
            # GCM installed it: its package and installed copy live in game-content/ps4/<ID>/.
            state = 'IN_GCM' if row.get('managed_status') in ('MANAGED_FILES_VERIFIED', 'MANAGED_RECORD_UNASSESSED') else 'CHECK'
        elif m.local_copy(row, doc) or stored_copy(m, row, doc):
            state = 'IN_GCM'
        else:
            state = 'EXTERNAL'
        out.append({'token': token(row), 'label': label(row), 'state': state, 'row': row})
    return out


def copy(rom, kind, tokens, log):
    m = provider()
    listed = {r['token']: r for r in rows(rom, kind)}
    unknown = [t for t in tokens if t not in listed]
    if unknown:
        raise ValueError('%d selected item(s) are not installed content of this game' % len(unknown))
    copied = skipped = 0
    for t in tokens:
        if listed[t]['state'] == 'IN_GCM':
            skipped += 1; continue
        if listed[t]['state'] != 'EXTERNAL':
            raise ValueError(listed[t]['label'] + ' needs an installed-file check before copying')
        start = time.time()
        m.execute(rom, [t], confirmed=True, progress=None)
        copied += 1
        log('COPIED %s in %.1fs' % (listed[t]['label'], time.time() - start))
    return {'copied': copied, 'skipped': skipped}


def size(path):
    total = files = 0
    for root, dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(root, n)).st_size; files += 1
            except OSError:
                pass
    return total, files


def leftovers():
    """Report parking folders; a folder is 'referenced' if any GCM state file names it."""
    names = {}
    state = ROOT / 'state'
    for p in state.rglob('*.json'):
        try:
            if p.is_file() and not p.is_symlink() and p.stat().st_size <= 8 * 1024 * 1024:
                text = p.read_text(errors='replace')
                for park in PARKING:
                    if park in text:
                        names.setdefault(park, []).append((p, text))
        except OSError:
            pass
    report, total = [], 0
    for park in PARKING:
        base = ADDONS / park
        if not base.is_dir() or base.is_symlink():
            continue
        for d in sorted(base.iterdir()):
            b, n = size(d); total += b
            refs = [str(p.relative_to(state)) for p, text in names.get(park, []) if d.name in text]
            inside = sorted(x.name for x in d.iterdir())[:4] if d.is_dir() else []
            report.append((park, d.name, b, n, time.strftime('%Y-%m-%d', time.localtime(d.stat().st_mtime)), refs, inside))
    return report, total


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('list', 'copy', 'leftovers'))
    p.add_argument('--rom'); p.add_argument('--emulator'); p.add_argument('--type', choices=('dlc', 'update'))
    p.add_argument('--tokens', default=''); p.add_argument('--confirm', action='store_true')
    a = p.parse_args()
    if a.action == 'leftovers':
        report, total = leftovers()
        for park, name, b, n, day, refs, inside in report:
            print('\t'.join(('LEFTOVER', park, name, '%.2f GB' % (b / 1e9), str(n) + ' files', day,
                             ('REFERENCED BY ' + ', '.join(refs[:3])) if refs else 'NOT REFERENCED', ' '.join(inside))))
        print('OK\tLEFTOVERS\t%d folders\t%.2f GB\tnothing changed' % (len(report), total / 1e9))
        return
    if a.emulator != 'shadps4' or not a.rom or not a.type:
        raise ValueError('--rom, --emulator shadps4 and --type required')
    if a.action == 'list':
        for r in rows(a.rom, a.type):
            print('\t'.join(('ROW', 'AVAILABLE', r['state'], r['label'], r['token'])))
        return
    if not a.confirm:
        raise ValueError('Confirm the copy with --confirm')
    tokens = [t for t in a.tokens.split(',') if t]
    if not tokens or len(tokens) != len(set(tokens)):
        raise ValueError('Select at least one item, each once')
    result = copy(a.rom, a.type, tokens, lambda *x: print(*x, file=sys.stderr, flush=True))
    print('OK\tCOPY\t' + '\t'.join('%s=%s' % (k.upper(), v) for k, v in result.items()))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR\t' + str(exc).replace('\t', ' ').replace('\n', ' ')[:400])
        sys.exit(1)
