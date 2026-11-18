#!/usr/bin/env python3
"""61TE4: Citron DLC uninstall. Citron loads every DLC file in its folders, so a DLC is uninstalled
by deleting its file, which GCM does only for its own copies that Ryujinx does not use.

  uninstall --rom ROM --tokens citron:dlc:APP:TITLE[,...] --confirm

Prints OK\\tUNINSTALLED=n, then one REMOVED or KEPT line per selected DLC (KEPT says why).
"""
import argparse, hashlib, importlib.util, os, re
from pathlib import Path

COMMON = Path(os.environ.get('GCM_COMMON', '/userdata/system/gcm-native-test/step61-common-content'))
GAME_CONTENT = Path(os.environ.get('GCM_GAME_CONTENT', '/userdata/system/game-content'))
INPLACE = COMMON / 'beta-switch-dlc-inplace-v02/gcm-switch-dlc-inplace-v02.py'
INPLACE_SHA = os.environ.get('GCM_INPLACE_SHA', '3561294a91c2b7740c72aa1b3ed03d0fa87fedffeebf74e8f73e389976b8d072')


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


def closed():
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = comm.read_text().strip().casefold()
        except OSError:
            continue
        if name.startswith(('citron', 'eden', 'yuzu', 'ryujinx')):
            raise ValueError('Close emulators before uninstalling DLC')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('uninstall',))
    p.add_argument('--rom', required=True); p.add_argument('--tokens', default=''); p.add_argument('--confirm', action='store_true')
    a = p.parse_args()
    if not a.confirm:
        raise ValueError('Explicit uninstall confirmation required')
    tokens = [t for t in a.tokens.split(',') if t]
    app = app_id(a.rom)
    if not tokens or len(set(tokens)) != len(tokens) or any(not t.upper().startswith('CITRON:DLC:' + app + ':') for t in tokens):
        raise ValueError('Select DLC of this game')
    closed()
    m = module()
    citron = m.citron_fresh(lambda *x: None, heal=False)
    try:
        ryu = m.load('ryujinx_integrity').validate(a.rom)
    except Exception:
        m.ryujinx_fresh(a.rom, lambda *x: None)
        ryu = m.load('ryujinx_integrity').validate(a.rom)
    ryujinx_paths = {os.path.realpath(r['registered_path']) for r in ryu.get('records', []) if r.get('registered_path')}
    plan = []
    for t in tokens:
        title = t.split(':')[3].upper()
        recs = [r for r in citron.get('records', []) if r.get('content_type') == 'dlc'
                and str(r.get('application_id', '')).upper() == app and str(r.get('title_id', '')).upper() == title]
        if not recs:
            plan.append((title, None, 'Not loaded by Citron any more')); continue
        for r in recs:
            path = str(r.get('path') or ''); real = os.path.realpath(path) if path else ''
            name = Path(path).name if path else title
            if r.get('origin') != 'External' or not path:
                plan.append((name, None, 'Installed inside Citron\'s own storage; remove it in Citron'))
            elif not Path(real).is_relative_to(GAME_CONTENT):
                plan.append((name, None, 'Your own file; to uninstall it from Citron, remove it from ' + str(Path(path).parent)))
            elif real in ryujinx_paths:
                plan.append((name, None, 'Ryujinx uses this same GCM copy; uninstall it in Ryujinx first'))
            elif any(q.is_symlink() for q in (Path(path), *Path(path).parents)) or not Path(path).is_file():
                plan.append((name, None, 'GCM copy path is not a plain file'))
            else:
                plan.append((name, path, ''))
    removed = 0
    for name, path, why in plan:
        if path:
            Path(path).unlink(); removed += 1
    if removed:
        m.citron_fresh(lambda *x: None, heal=True)
    print('OK\tUNINSTALLED=' + str(removed))
    for name, path, why in plan:
        print(('REMOVED\t' + name) if path else ('KEPT\t' + name + '\t' + why))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print('ERROR\t' + str(exc).replace('\n', ' ')); raise SystemExit(1)
