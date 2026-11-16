#!/usr/bin/env python3
"""Choose the frontend: GCM v3.6 (permanent), GCM v3.5 (permanent) or the standard v3.4 frontend.

  python3 gcm-frontend-switch.py status
  python3 gcm-frontend-switch.py v36   use GCM v3.6 now and at every boot
  python3 gcm-frontend-switch.py v35   use GCM v3.5 now and at every boot
  python3 gcm-frontend-switch.py v34   use the standard frontend now and at every boot
Every switch first returns to the standard frontend through the supervisors' own restore
action (never the shutdown-only 'stop'), waits until it is running, then starts the chosen one.
"""
import json, subprocess, sys, time
from pathlib import Path

SERVICES = Path('/userdata/system/services')
NAMES = {'v35': 'GCMV35', 'v36': 'GCMV36'}
STATES = {'v35': Path('/run/gcm-v35-permanent.json'), 'v36': Path('/run/gcm-v36-permanent.json')}
FLAG = Path('/var/run/emulationstation-standalone')
RUN = Path('/run')


def run(*cmd, timeout=60):
    r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


def procs():
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                a = [x.decode('utf-8', 'replace') for x in (p / 'cmdline').read_bytes().split(b'\0') if x]
            except OSError:
                continue
            if a:
                yield int(p.name), a


def standard_running():
    return [pid for pid, a in procs() if Path(a[0]).name == 'emulationstation']


def gcm_frontends():
    return {name: [pid for pid, a in procs() if any(Path(x).name == 'emulationstation-gcm-' + name for x in a)] for name in NAMES}


def alive(pid):
    try:
        return pid > 1 and Path('/proc/%d' % pid).exists()
    except (TypeError, ValueError):
        return False


def supervisor_running(name):
    try:
        data = json.loads(STATES[name].read_text())
        return alive(int(data.get('supervisor_pid') or 0)), data.get('phase')
    except (OSError, ValueError):
        return False, None


def enabled():
    code, out = run('batocera-services', 'list')
    found = {}
    for line in out.splitlines():
        for name in NAMES.values():
            if line.strip().startswith(name + ';') or line.split()[:1] == [name]:
                found[name] = 'on' in line.lower() or '*' in line
    return found, out


def status():
    print('Standard frontend (v3.4) running:', bool(standard_running()), '| marker:', 'present' if FLAG.exists() else 'absent')
    fronts = gcm_frontends()
    for name in NAMES:
        up, phase = supervisor_running(name)
        print('GCM %s: frontend %s, supervisor %s, phase %s' % (name, 'running' if fronts[name] else 'not running', 'alive' if up else 'not running', phase))
    flags, raw = enabled()
    print('Started at boot:', ', '.join('%s=%s' % (k, 'yes' if v else 'no') for k, v in sorted(flags.items())) or raw[:300])


def to_standard():
    for name, service in NAMES.items():
        up, phase = supervisor_running(name)
        if up or gcm_frontends()[name]:
            print('Returning from GCM %s to the standard frontend (restore)...' % name, flush=True)
            code, out = run('bash', str(SERVICES / service), 'restore')
            print('  ' + out.replace('\n', '\n  '))
    deadline = time.time() + 150
    while time.time() < deadline:
        if standard_running() and FLAG.exists() and not any(gcm_frontends().values()):
            time.sleep(3)
            if standard_running() and FLAG.exists():
                print('PASS: standard frontend running', flush=True)
                return True
        time.sleep(1)
    print('STOP: the standard frontend did not come back within 150 s. Restart the device to recover (the frontend chosen for boot starts again).')
    return False


def set_boot(choice):
    for name, service in NAMES.items():
        if not (SERVICES / service).exists():
            continue
        code, out = run('batocera-services', 'enable' if name == choice else 'disable', service)
        print('Boot: %s %s%s' % (service, 'enabled' if name == choice else 'disabled', '' if code == 0 else ' (FAILED: %s)' % out[:200]))


def clear_stale(choice):
    """61TD: a stop request or shutdown marker left behind by an earlier session makes a newly
    started supervisor cancel itself. Remove them only while no supervisor for it is alive."""
    up, phase = supervisor_running(choice)
    if up:
        return
    for p in (RUN / ('gcm-%s-permanent.stop' % choice), RUN / ('gcm-%s-permanent-shutdown.marker' % choice), STATES[choice]):
        if p.exists():
            p.unlink()
            print('Cleared leftover %s' % p, flush=True)


def start(choice):
    service = NAMES[choice]
    clear_stale(choice)
    code, out = run('bash', str(SERVICES / service), 'start')
    print(out)
    deadline = time.time() + 320
    while time.time() < deadline:
        up, phase = supervisor_running(choice)
        if phase == '%s_persistent_running' % choice and up and gcm_frontends()[choice]:
            print('PASS: GCM %s is now the frontend' % choice)
            return True
        if phase in ('recovery_attention_required', 'v34_restored'):
            print('STOP: GCM %s did not start (%s); the standard frontend is back. See /userdata/system/logs/gcm-%s-permanent.log' % (choice, phase, choice))
            return False
        time.sleep(1)
    print('STOP: GCM %s not confirmed within 320 s; check: python3 %s status' % (choice, sys.argv[0]))
    return False


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'status':
        status(); return 0
    if action not in ('v34', 'v35', 'v36'):
        print(__doc__); return 2
    for path in ('/tmp/shutdown.please', '/tmp/reboot.please'):
        if Path(path).exists():
            print('STOP: a shutdown or reboot is pending'); return 1
    if not to_standard():
        return 1
    set_boot(action)
    if action == 'v34':
        print('PASS: standard frontend kept; it is also what starts at boot')
        return 0
    return 0 if start(action) else 1


if __name__ == '__main__':
    sys.exit(main())
