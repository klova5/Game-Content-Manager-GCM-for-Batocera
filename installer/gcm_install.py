#!/usr/bin/env python3
"""GCM (Game Content Manager) 4.0 developer preview: install / uninstall on Batocera 44 (x86_64).

  install.sh            check the device, then install GCM (asks before switching the frontend)
  install.sh --check    only check; nothing is written
  install.sh --start    install and switch to GCM 4.0 right away (also starts it at every boot)
  uninstall.sh          return to the standard frontend and remove GCM's program files
  uninstall.sh --purge  also remove GCM's state and the GCM-managed copies in /userdata/system/game-content

Your games, saves and emulator settings are never touched; the standard Batocera frontend stays
installed and is used again after uninstalling.
"""
import hashlib, io, json, os, platform, re, shutil, subprocess, sys, tarfile, time
from pathlib import Path

TARGET = Path(os.environ.get('GCM_TARGET_ROOT', '/'))  # tests install into a scratch folder
HERE = Path(__file__).resolve().parent
SYSTEM = TARGET / 'userdata/system'
V36 = SYSTEM / 'add-ons/game-content-manager/v36-permanent'
RECORD = SYSTEM / 'add-ons/game-content-manager/release-install.json'
SERVICE = SYSTEM / 'services/GCMV36'
STANDARD = TARGET / 'usr/bin/emulationstation'
# These two carry device-specific checksums after installing, so they always differ from the package.
ADAPTED = {'add-ons/game-content-manager/v36-permanent/gcm-v36-permanent-supervisor.py',
           'add-ons/game-content-manager/v36-permanent/gcm-v36-boot-wait.py'}
VERSIONS = ('44',)


def say(*a):
    print(*a, flush=True)


def stop(msg):
    say('STOP: ' + msg); raise SystemExit(1)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(8 << 20), b''):
            h.update(b)
    return h.hexdigest()


def batocera_version():
    for cmd in (['batocera-version'],):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    for p in (TARGET / 'usr/share/batocera/batocera.version',):
        if p.exists():
            return p.read_text().strip()
    return ''


def safe_members(tar):
    for m in tar.getmembers():
        name = Path(m.name)
        if name.is_absolute() or '..' in name.parts or not (m.isfile() or m.isdir()):
            stop('unsafe entry in the package: ' + m.name)
        yield m


def checks():
    manifest = json.loads((HERE / 'manifest.json').read_text())
    if os.geteuid() != 0 and 'GCM_TARGET_ROOT' not in os.environ:
        stop('run as root (Batocera\'s terminal or SSH session is root)')
    if platform.machine() != 'x86_64' and 'GCM_TARGET_ROOT' not in os.environ:
        stop('this build is for x86_64 Batocera only (this device: %s)' % platform.machine())
    version = batocera_version()
    if not version.startswith(VERSIONS) and '--any-version' not in sys.argv:
        stop('this build is for Batocera %s; this device reports "%s"' % (', '.join(VERSIONS), version or 'unknown'))
    if not STANDARD.is_file():
        stop('standard frontend %s not found' % STANDARD)
    binary = HERE / manifest['frontend']['file']
    if sha(binary) != manifest['frontend']['sha256']:
        stop('the downloaded frontend is damaged (checksum differs); download the release again')
    conflicts = []
    with tarfile.open(HERE / manifest['files']['archive']) as tar:
        for m in safe_members(tar):
            dest = SYSTEM / m.name
            if m.isfile() and m.name not in ADAPTED and dest.exists() and (dest.is_symlink() or not dest.is_file() or sha(dest) != manifest['files']['sha256'].get(m.name)):
                conflicts.append(m.name)
    say('Batocera version: %s' % (version or 'unknown'))
    say('GCM files: %d; already present with different content: %d' % (len(manifest['files']['sha256']), len(conflicts)))
    return manifest, conflicts


def patch(path, pattern, value):
    text = path.read_text()
    new, n = re.subn(pattern, lambda m: m.group(1) + value + m.group(3), text)
    if n != 1:
        stop('could not set %s in %s' % (pattern, path.name))
    path.write_text(new)


def install():
    manifest, conflicts = checks()
    if '--check' in sys.argv:
        say('CHECK ONLY: nothing was written')
        for c in conflicts[:10]:
            say('  would replace: ' + c)
        return
    if conflicts and '--replace' not in sys.argv:
        for c in conflicts[:10]:
            say('  different existing file: ' + c)
        stop('%d GCM files already exist with different content (an older GCM?). Run uninstall.sh first, or add --replace.' % len(conflicts))
    written = []
    with tarfile.open(HERE / manifest['files']['archive']) as tar:
        for m in safe_members(tar):
            dest = SYSTEM / m.name
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True); continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = tar.extractfile(m).read()
            if hashlib.sha256(data).hexdigest() != manifest['files']['sha256'][m.name]:
                stop('package file damaged: ' + m.name)
            tmp = dest.with_name('.' + dest.name + '.gcm-new')
            tmp.write_bytes(data); os.chmod(tmp, m.mode & 0o777 or 0o644); os.replace(tmp, dest)
            written.append(m.name)
    V36.mkdir(parents=True, exist_ok=True)
    frontend = V36 / 'emulationstation-gcm-v36'
    tmp = frontend.with_name('.emulationstation-gcm-v36.new')
    shutil.copyfile(HERE / manifest['frontend']['file'], tmp); os.chmod(tmp, 0o755); os.replace(tmp, frontend)
    front_sha, std_sha = manifest['frontend']['sha256'], sha(STANDARD)
    supervisor, boot = V36 / 'gcm-v36-permanent-supervisor.py', V36 / 'gcm-v36-boot-wait.py'
    patch(supervisor, r'(V34_SHA = ")([0-9a-f]{64})(")', std_sha)
    patch(supervisor, r'(V36_SHA = ")([0-9a-f]{64})(")', front_sha)
    patch(boot, r'(EXPECTED = ")([0-9a-f]{64})(")', front_sha)
    (V36 / 'deployment.json').write_text(json.dumps({
        'version': '4.0', 'build': manifest['build'], 'binary': '/userdata/system/add-ons/game-content-manager/v36-permanent/emulationstation-gcm-v36',
        'binary_sha256': front_sha, 'supervisor_sha256': sha(supervisor), 'runtime': manifest['runtime'],
        'timeout_enabled': False, 'fallback': 'Original Batocera EmulationStation', 'startup_service': 'GCMV36',
        'installed': time.strftime('%Y-%m-%d %H:%M:%S'), 'standard_frontend_sha256': std_sha}, indent=2) + '\n')
    if not SERVICE.is_file():
        stop('the package did not contain the GCMV36 service')
    os.chmod(SERVICE, 0o755)
    for d in ('game-content/citron-registered', 'gcm-native-test/step61-common-content/state', 'logs'):
        (SYSTEM / d).mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps({'build': manifest['build'], 'files': written + ['add-ons/game-content-manager/v36-permanent/emulationstation-gcm-v36',
                                  'add-ons/game-content-manager/v36-permanent/deployment.json']}, indent=1) + '\n')
    say('PASS: GCM 4.0 %s installed (%d files)' % (manifest['build'], len(written)))
    switch = V36 / 'gcm-frontend-switch.py'
    if '--start' in sys.argv and 'GCM_TARGET_ROOT' not in os.environ:
        say('Switching to GCM 4.0 (the screen goes to the standard frontend and back, about 1-2 minutes)...')
        raise SystemExit(subprocess.run([sys.executable, '-B', str(switch), 'v36']).returncode)
    shown = '/' + switch.relative_to(TARGET).as_posix()
    say('Start GCM 4.0 now and at every boot with:  python3 %s v36' % shown)
    say('Back to the standard frontend any time with: python3 %s v34' % shown)


def uninstall():
    if not RECORD.exists():
        stop('no GCM release installation found (%s)' % RECORD)
    record = json.loads(RECORD.read_text())
    switch = V36 / 'gcm-frontend-switch.py'
    if switch.exists() and 'GCM_TARGET_ROOT' not in os.environ:
        say('Returning to the standard frontend...')
        subprocess.run([sys.executable, '-B', str(switch), 'v34'])
        subprocess.run(['batocera-services', 'disable', 'GCMV36'], capture_output=True)
    removed = 0
    for rel in record['files']:
        p = SYSTEM / rel
        if p.is_file() and not p.is_symlink():
            p.unlink(); removed += 1
    for p in (SERVICE, RECORD):
        if p.exists():
            p.unlink()
    for root in (SYSTEM / 'gcm-native-test', SYSTEM / 'add-ons/game-content-manager'):
        for d in sorted((x for x in root.rglob('*') if x.is_dir()), key=lambda x: -len(x.parts)) if root.exists() else []:
            if d.name in ('__pycache__',):
                shutil.rmtree(d, ignore_errors=True)
            else:
                try:
                    d.rmdir()
                except OSError:
                    pass
    if '--purge' in sys.argv:
        for d in (SYSTEM / 'gcm-native-test', SYSTEM / 'add-ons/game-content-manager', SYSTEM / 'game-content'):
            shutil.rmtree(d, ignore_errors=True)
        say('PASS: GCM removed, including its state and GCM-managed copies (%d program files)' % removed)
    else:
        say('PASS: GCM program files removed (%d). Kept: GCM state and /userdata/system/game-content (use --purge to remove them)' % removed)


if __name__ == '__main__':
    (uninstall if 'uninstall' in sys.argv[1:2] else install)()
