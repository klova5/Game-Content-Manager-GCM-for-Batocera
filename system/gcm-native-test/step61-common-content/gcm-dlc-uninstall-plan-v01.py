"""Read-only, game-bound DLC uninstall preview; no uninstall executor."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
from pathlib import Path

COMMON = Path('/userdata/system/gcm-native-test/step61-common-content')
MANAGED = Path('/userdata/system/game-content/ps4')
ADDONS = Path('/userdata/roms/ps4/DLC')

def safe(value):
    path = Path(value)
    if not path.is_absolute() or Path(os.path.abspath(path)) != path:
        raise ValueError('Absolute normalized path required')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink path rejected: ' + str(item))
    return path

def identity(path):
    s = safe(path).lstat()
    return [s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def snapshot(path):
    path = safe(path)
    if not path.exists():
        return None
    result = {'.': identity(path)}
    if not stat.S_ISDIR(result['.'][2]):
        raise ValueError('Installed directory required: ' + str(path))
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            item = safe(Path(directory) / name)
            entry = identity(item)
            if not (stat.S_ISDIR(entry[2]) or stat.S_ISREG(entry[2])):
                raise ValueError('Unsupported installed entry: ' + str(item))
            result[item.relative_to(path).as_posix()] = entry
    return result

def identify(rom):
    path = safe(COMMON / 'gcm-ps4-base-identity.py')
    if hashlib.sha256(path.read_bytes()).hexdigest() != 'f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438':
        raise ValueError('Base identity dependency changed')
    spec = importlib.util.spec_from_file_location('uninstall_identity', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.identify(rom)

def preview(rom, emulator, tokens):
    if emulator != 'shadps4':
        raise ValueError('Uninstall preview adapter unavailable for this emulator')
    game = identify(str(safe(rom)))
    title = game['application_id']
    if not re.fullmatch(r'CUSA[0-9]{5}', title):
        raise ValueError('Internal PS4 game identity required')
    directory = safe(MANAGED / title / 'installed-dlc')
    wanted = set(tokens)
    if len(wanted) != len(tokens):
        raise ValueError('Duplicate selection token')
    rows, seen = [], set()
    paths = sorted(directory.glob('*.json')) if directory.exists() else []
    for path in paths:
        safe(path)
        before = identity(path)
        record = json.loads(path.read_text())
        digest = record.get('package_sha256', '')
        label = record.get('entitlement_label', '')
        source = safe(record['source'])
        installed = safe(record['installed_root'])
        if (record.get('schema') != 'GCM_PS4_INSTALLED_DLC_V1'
            or record.get('application_id') != title
            or not re.fullmatch(r'[0-9a-f]{64}', digest)
            or path.name != digest + '.json'
            or installed != directory / digest
            or source.parent != MANAGED / title / 'dlc'
            or not re.fullmatch(r'[A-Z0-9_]{16}', label)
            or record.get('content_id', '')[-20:] != '_00-' + label
            or label in seen):
            raise ValueError('Conflicting DLC record: ' + str(path))
        seen.add(label)
        token = str(source).encode().hex()
        if wanted and token not in wanted:
            continue
        enabled = safe(ADDONS / title / label)
        stored_tree, enabled_tree = snapshot(installed), snapshot(enabled)
        if identity(path) != before:
            raise ValueError('DLC record changed during preview')
        rows.append({'label': label, 'token': token,
                     'record': str(path), 'record_identity': before,
                     'stored_root': str(installed), 'stored_snapshot': stored_tree,
                     'enabled_root': str(enabled), 'enabled_snapshot': enabled_tree,
                     'preserved_package': str(source),
                     'repair_required': stored_tree is None})
    if wanted - {row['token'] for row in rows}:
        raise ValueError('Selected DLC is not registered for this game')
    transactions = []
    for path in sorted((COMMON / 'state/ps4-dlc-selection').glob('transaction-*.json')):
        safe(path)
        record = json.loads(path.read_text())
        if record.get('application_id') == title and record.get('status') not in ('COMPLETE', 'ROLLED_BACK'):
            transactions.append(str(path))
    return {'schema': 'GCM_DLC_UNINSTALL_PREVIEW_V1', 'application_id': title,
            'emulator': emulator, 'rows': rows,
            'unfinished_transactions': transactions,
            'execution_available': False, 'changes_performed': False}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rom', required=True)
    parser.add_argument('--emulator', required=True)
    parser.add_argument('--token', action='append', default=[])
    args = parser.parse_args()
    result = preview(args.rom, args.emulator, args.token)
    print('GAME:', result['application_id'])
    for row in result['rows']:
        stored = row['stored_snapshot']
        enabled = row['enabled_snapshot']
        size = lambda tree: sum(v[3] for v in (tree or {}).values() if stat.S_ISREG(v[2]))
        print('DLC:', row['label'])
        print('  STORE:', row['stored_root'], 'BYTES:', size(stored), 'EXISTS:', stored is not None)
        print('  ENABLED:', row['enabled_root'], 'BYTES:', size(enabled), 'EXISTS:', enabled is not None)
        print('  PRESERVE:', row['preserved_package'])
    print('UNFINISHED TRANSACTIONS:', len(result['unfinished_transactions']))
    print('Read-only preview; no packages read, content moved or recovery flags cleared')

if __name__ == '__main__':
    main()
