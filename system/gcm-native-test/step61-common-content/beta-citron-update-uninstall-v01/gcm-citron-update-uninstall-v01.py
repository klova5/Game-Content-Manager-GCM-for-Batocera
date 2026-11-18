#!/usr/bin/env python3
"""61TE2: Citron update uninstall. Citron loads every update file in its folders, so an update is
uninstalled by deleting its file, which GCM does only for its own copies:

  list      --rom ROM                      ROW lines for every Citron update of the game
  preview   --rom ROM --tokens TOKEN       what Uninstall would do for one entry
  uninstall --rom ROM --tokens T1,T2 --confirm

States: REMOVABLE (a GCM copy no other emulator uses; Uninstall deletes it), KEPT_SHARED (a GCM copy
Ryujinx also uses), ORIGINAL (your own file; remove it from its folder yourself), CITRON_NAND
(installed inside Citron's own storage; remove it in Citron).
"""
import argparse, hashlib, importlib.util, json, os, re, sys
from pathlib import Path

COMMON = Path(os.environ.get('GCM_COMMON', '/userdata/system/gcm-native-test/step61-common-content'))
GAME_CONTENT = Path(os.environ.get('GCM_GAME_CONTENT', '/userdata/system/game-content'))
INPLACE = COMMON / 'beta-switch-dlc-inplace-v02/gcm-switch-dlc-inplace-v02.py'
INPLACE_SHA = os.environ.get('GCM_INPLACE_SHA', '3561294a91c2b7740c72aa1b3ed03d0fa87fedffeebf74e8f73e389976b8d072')
EMULATORS = ('citron', 'eden', 'yuzu', 'ryujinx')


def module():
    if any(q.is_symlink() for q in (INPLACE, *INPLACE.parents)) or hashlib.sha256(INPLACE.read_bytes()).hexdigest() != INPLACE_SHA:
        raise ValueError('Switch content module changed')
    spec = importlib.util.spec_from_file_location('gcm_inplace_v02', INPLACE)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def app_id(rom):
    found = re.findall(r'\[([0-9A-Fa-f]{16})\]', Path(rom).name)
    if not found:
        raise ValueError('Game title ID not found in the file name')
    return found[0].upper()


def version(r):
    shown = r.get('display_version')
    return str(shown) if shown else 'version %s' % r.get('internal_version')


def token(path):
    return 'citron-update:' + hashlib.sha256(path.encode()).hexdigest()[:32]


def rows(m, rom):
    app = app_id(rom)
    citron = m.citron_fresh(lambda *a: None, heal=False)
    try:
        ryu = m.load('ryujinx_integrity').validate(rom)
    except Exception:
        m.ryujinx_fresh(rom, lambda *a: None)
        ryu = m.load('ryujinx_integrity').validate(rom)
    ryujinx_paths = {os.path.realpath(r['registered_path']) for r in ryu.get('records', []) if r.get('registered_path')}
    out = []
    for r in citron.get('records', []):
        if r.get('content_type') != 'update' or str(r.get('application_id', '')).upper() != app:
            continue
        path = str(r.get('path') or '')
        label = 'v%s (%s)' % (version(r), Path(path).name if path else 'Citron storage')
        real = os.path.realpath(path) if path else ''
        if r.get('origin') != 'External' or not path:
            state, why = 'CITRON_NAND', 'Installed inside Citron\'s own storage. Remove it in Citron.'
        elif not Path(real).is_relative_to(GAME_CONTENT):
            state, why = 'ORIGINAL', 'This is your own file. To uninstall it from Citron, remove it from: ' + str(Path(path).parent)
        elif real in ryujinx_paths:
            state, why = 'KEPT_SHARED', 'Ryujinx uses this same GCM copy. Uninstall it in Ryujinx first; then it can be removed here.'
        else:
            state, why = 'REMOVABLE', 'Uninstall permanently deletes this GCM copy. Your original package is not touched.'
        out.append({'state': state, 'label': label, 'token': token(path or label), 'path': path, 'why': why})
    return out


def citron_closed():
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = comm.read_text().strip().casefold()
        except OSError:
            continue
        if name.startswith(EMULATORS):
            raise ValueError('Close emulators before uninstalling updates')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('list', 'preview', 'uninstall', 'retained'))
    p.add_argument('--rom', required=True); p.add_argument('--emulator', default='citron-emu')
    p.add_argument('--tokens', default=''); p.add_argument('--confirm', action='store_true')
    a = p.parse_args()
    if a.action == 'retained':
        return  # Citron uninstall keeps nothing to restore
    m = module()
    current = rows(m, a.rom)
    if a.action == 'list':
        for r in current:
            print('\t'.join(('ROW', 'AVAILABLE', r['state'], r['label'].replace('\t', ' '), r['token'])))
        return
    chosen = [t for t in a.tokens.split(',') if t]
    by_token = {r['token']: r for r in current}
    if a.action == 'preview':
        r = by_token.get(a.tokens)
        if not r:
            raise ValueError('Selected update changed; reopen Uninstall')
        print(r['label']); print(r['why']); return
    if not a.confirm:
        raise ValueError('Explicit uninstall confirmation required')
    if not chosen or len(set(chosen)) != len(chosen) or any(t not in by_token for t in chosen):
        raise ValueError('Selected updates changed; reopen Uninstall')
    if any(by_token[t]['state'] != 'REMOVABLE' for t in chosen):
        raise ValueError(next(by_token[t]['why'] for t in chosen if by_token[t]['state'] != 'REMOVABLE'))
    citron_closed()
    for t in chosen:
        path = Path(by_token[t]['path'])
        if any(q.is_symlink() for q in (path, *path.parents)) or not path.is_file() or not path.is_relative_to(GAME_CONTENT):
            raise ValueError('Unsafe GCM copy path: ' + str(path))
    for t in chosen:
        Path(by_token[t]['path']).unlink()
    m.citron_fresh(lambda *a: None, heal=True)
    print('OK\tUNINSTALLED=' + str(len(chosen)))
    for t in chosen:
        print('DELETED GCM COPY:', by_token[t]['path'])


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print('ERROR\t' + str(exc).replace('\n', ' ')); raise SystemExit(1)
