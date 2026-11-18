#!/usr/bin/env python3
"""61TE3: after an uninstall, the item leaves Available; a scan finds it again as Detected.

  before --rom ROM --emulator EMU --type update|dlc   note the current Available entries (run before uninstalling)
  after  --rom ROM --emulator EMU --type update|dlc   forget the entries this uninstall made available

Uses the existing GCM entry-removal step (the former Delete): it hides the entry and resets its
verification. Only entries that appeared because of this uninstall and that are menu entries only
(no files of their own) are forgotten; files are handled by gcm-uninstall-cleanup-v01.
"""
import argparse, hashlib, importlib.util, json, os, sys, time
from pathlib import Path

COMMON = Path(os.environ.get('GCM_COMMON', '/userdata/system/gcm-native-test/step61-common-content'))
TOOLS = {'managed': ('beta-managed-delete-v01/gcm-managed-delete-v01.py', os.environ.get('GCM_MANAGED_SHA', 'ca432cd7fd51ab8a969fbc4e1b87c1ff831541b133d013e8185d20794e90ba0a')),
         'bridge': ('beta-dlc-stage-separation-v04/delete-bridge.py', os.environ.get('GCM_BRIDGE_SHA', '5a824bfd57be37607837faf0dfdadac3d2d6110614d3510f1a17af7fb14fc655')),
         'inplace': ('beta-switch-dlc-inplace-v02/gcm-switch-dlc-inplace-v02.py', os.environ.get('GCM_INPLACE_SHA', '3561294a91c2b7740c72aa1b3ed03d0fa87fedffeebf74e8f73e389976b8d072'))}
STATE = COMMON / 'state/uninstall-forget'
SWITCH = ('ryujinx', 'citron-emu', 'citron')


def load(key):
    rel, want = TOOLS[key]
    p = COMMON / rel
    if any(q.is_symlink() for q in (p, *p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest() != want:
        raise ValueError('GCM component changed: ' + rel)
    spec = importlib.util.spec_from_file_location('forget_' + key, p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def registered(rom, emulator, kind):
    """Refresh the Ryujinx/Citron records (they go stale after a reboot or change) and return the
    entry identifiers of what is registered now, in the form the entry tool uses."""
    if emulator not in SWITCH:
        return set()
    ip = load('inplace')
    ids = set()
    if emulator == 'ryujinx':
        try:
            state = ip.load('ryujinx_integrity').validate(rom)
        except Exception:
            ip.ryujinx_fresh(rom, lambda *a: None)
            state = ip.load('ryujinx_integrity').validate(rom)
        for r in state.get('records', []):
            if r.get('content_type') == kind and r.get('registered_path'):
                h = r['registered_path'].encode().hex()
                ids.update((h, 'existing:' + h))
    else:
        state = ip.citron_fresh(lambda *a: None, heal=False) or {}
        for r in state.get('records', []):
            if r.get('content_type') != kind:
                continue
            if kind == 'dlc':
                ids.add('citron:dlc:' + str(r.get('application_id', '')).upper() + ':' + str(r.get('title_id', '')))
            else:
                ids.add('citron:update:' + str(r.get('title_id', '')) + ':' + str(r.get('internal_version')) + ':' + str(r.get('origin', ''))
                        + ':' + hashlib.sha256(str(r.get('path', '')).encode()).hexdigest()[:16])
            if r.get('path'):
                h = str(r['path']).encode().hex()
                ids.update((h, 'existing:' + h))
    return ids


def inventory(rom, emulator, kind):
    d = load('bridge' if kind == 'dlc' and emulator in SWITCH else 'managed')
    m = d.workflow().provider(); s = d.scope(m, rom, emulator, kind)
    return d, m, s, d.available_inventory(m, s)


def record(s):
    return STATE / (hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest() + '.json')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('before', 'after'))
    p.add_argument('--rom', required=True); p.add_argument('--emulator', required=True); p.add_argument('--type', choices=('update', 'dlc'), required=True)
    a = p.parse_args()
    now = registered(a.rom, a.emulator, a.type)
    d, m, s, rows = inventory(a.rom, a.emulator, a.type)
    path = record(s)
    if a.action == 'before':
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp'); tmp.write_text(json.dumps({'scope': s, 'tokens': sorted(r['token'] for r in rows), 'registered': sorted(now), 'time': time.time()}))
        os.replace(tmp, path)
        print('OK\tNOTED=%d' % len(rows)); return
    if not path.exists():
        print('OK\tFORGOTTEN=0\tno earlier list'); return
    before = json.loads(path.read_text())
    if before.get('scope') != s or time.time() - before.get('time', 0) > 6 * 3600:
        path.unlink(); print('OK\tFORGOTTEN=0\tearlier list does not match'); return
    known = set(before['tokens'])
    gone = set(before.get('registered', [])) - now
    new = [r for r in rows if not r.get('blocked') and not r.get('roots')
           and (r['token'] not in known or set(r.get('gui_tokens', [])) & gone)]
    done = 0
    for i in range(0, len(new), 32):
        chunk = [r['token'] for r in new[i:i + 32]]
        d.execute(m, s, chunk, confirmed=True)
        done += len(chunk)
    path.unlink()
    print('OK\tFORGOTTEN=%d' % done)
    for r in new:
        print('FORGOTTEN:', r['label'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERROR\t' + str(exc).replace('\n', ' ')); raise SystemExit(1)
