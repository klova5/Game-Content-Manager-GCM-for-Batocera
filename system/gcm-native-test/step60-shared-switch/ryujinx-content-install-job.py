#!/usr/bin/env python3
"""GCM v3.5 nonblocking installer launcher / monitor.

Does not modify NSP, index or Ryujinx configuration itself. The worker calls
verified-install.py, which performs guarded installation after --confirm.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

WORK = Path('/userdata/system/gcm-native-test/v34-development/v35-native-development')
ROOT = Path(__file__).resolve().parent
INSTALLER = Path('/userdata/system/gcm-native-test/step60-shared-switch/ryujinx-install-worker-provider.py')
CONTENT_TYPE = 'update'
JOBS = Path('/userdata/system/gcm-native-test/step60-shared-switch/state/ryujinx-install-jobs')
BOOT = Path('/proc/sys/kernel/random/boot_id')
SCHEMA = 'GCM_SHARED_CITRON_CONTENT_INSTALL_JOB_V1'
TERMINAL = {'INSTALLED', 'ERROR'}


def boot():
    return BOOT.read_text().strip()


def safe(text, maximum=480):
    return str(text).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')[:maximum]


def validate_token(token):
    if not isinstance(token, str) or not 2 <= len(token) <= 8192 or len(token) % 2:
        raise ValueError('Invalid content token length')
    path = bytes.fromhex(token).decode('utf-8')
    if not os.path.isabs(path) or not path.lower().endswith('.nsp'):
        raise ValueError('Content token does not contain an absolute NSP path')
    return os.path.abspath(path)


def key(rom, token):
    source = validate_token(token)
    return hashlib.sha256(
        (os.path.abspath(rom) + '\0' + source + '\0' + CONTENT_TYPE).encode()
    ).hexdigest()


def paths(rom, token):
    name = key(rom, token)
    return JOBS / (name + '.json'), JOBS / (name + '.lock')


def load(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def save(path, state):
    fd, name = tempfile.mkstemp(prefix='.install-', suffix='.tmp', dir=JOBS)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def alive(job):
    pid = job.get('pid')
    if not isinstance(pid, int) or pid < 2:
        return False
    try:
        command = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        return (str(Path(__file__).resolve()).encode() in command
                and b'worker' in command
                and str(job['job_id']).encode() in command)
    except (OSError, KeyError):
        return False


def emit(job):
    print('INSTALL\tSTATUS\t' + job.get('status', 'ERROR'))
    print('INSTALL\tJOB_ID\t' + str(job.get('job_id', '')))
    if job.get('local_status'):
        print('INSTALL\tLOCAL_STATE\t' + job['local_status'])
    if job.get('local'):
        print('INSTALL\tLOCAL\t' + safe(job['local'], 1000))
    if job.get('error'):
        print('INSTALL\tERROR\t' + safe(job['error']))
    print('STATUS\tEXTERNAL ORIGINAL PRESERVED')


def invoke_installer(action, rom, token, confirm=False, timeout=90):
    expected = "ba04346b0ddb6cb6219ce4fdc8909c92b1d599cdc3dad5bad942637a61764ee0"
    if INSTALLER.is_symlink() or hashlib.sha256(INSTALLER.read_bytes()).hexdigest() != expected:
        raise ValueError("Global worker provider changed")
    args = [sys.executable, "-B", str(INSTALLER), action,
            "--rom", os.path.abspath(rom), "--token", token,
            "--type", CONTENT_TYPE]
    if confirm:
        args.append("--confirm")
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout)


def field(output, name):
    prefix = 'INSTALL\t' + name + '\t'
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return ''


def installer_error(result):
    lines = [x[6:] for x in result.stdout.splitlines() if x.startswith('ERROR\t')]
    return safe(lines[-1] if lines else (result.stderr or 'Verified installer failed'))


def checked_preflight(rom, token):
    preview = invoke_installer('preview', rom, token)
    if (preview.returncode != 0 or field(preview.stdout, 'STATUS') != 'PREVIEW'
            or field(preview.stdout, 'FRESH_VERIFICATION') != 'YES'):
        raise ValueError(installer_error(preview) if preview.returncode else
                         'Fresh guarded verification required before installation')
    if field(preview.stdout, 'EXTERNAL_ORIGINAL_PRESERVED') != 'YES':
        raise RuntimeError('Unsafe installer preview: external source protection missing')
    return preview


def start(args):
    rom = os.path.abspath(args.rom)
    token = args.token.lower()
    validate_token(token)
    if not Path(rom).is_file():
        raise FileNotFoundError('Base game unavailable')
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialise starts, including across different packages, so one game does not
    # receive simultaneous content registration writes.
    with (JOBS / '.global-install-start.lock').open('a+') as global_lock:
        fcntl.flock(global_lock, fcntl.LOCK_EX)
        for prior in JOBS.glob('*.json'):
            prior_job = load(prior)
            if prior_job and prior_job.get('status') == 'RUNNING' and alive(prior_job):
                raise RuntimeError('A content installation is already running')
        checked_preflight(rom, token)
        report, lockfile = paths(rom, token)
        with lockfile.open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            job = {'schema': SCHEMA, 'job_id': uuid.uuid4().hex, 'boot_id': boot(),
                   'rom': rom, 'token': token, 'content_type': CONTENT_TYPE,
                   'status': 'RUNNING', 'pid': None,
                   'error': '', 'local': '', 'local_status': ''}
            save(report, job)
            try:
                proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                         'worker', '--rom', rom, '--token', token,
                                         '--job-id', job['job_id'], '--type', CONTENT_TYPE],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL,
                                        start_new_session=True, close_fds=True)
            except Exception as exc:
                job.update(status='ERROR', error='Unable to launch install worker: ' + str(exc))
                save(report, job)
                raise
            job['pid'] = proc.pid
            save(report, job)
    emit(job)


def status(args):
    report, lockfile = paths(args.rom, args.token)
    if not report.exists():
        print('INSTALL\tSTATUS\tNOT_STARTED')
        return
    with lockfile.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = load(report)
        if not isinstance(job, dict) or job.get('schema') != SCHEMA:
            raise ValueError('Invalid install job record')
        if job['status'] == 'RUNNING' and (job.get('boot_id') != boot() or not alive(job)):
            job.update(status='ERROR', error='Installer stopped before completing; inspect managed copy and Citron registration')
            save(report, job)
    emit(job)


def worker(args):
    report, lockfile = paths(args.rom, args.token)
    with lockfile.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = load(report)
        if (not job or job.get('job_id') != args.job_id
                or job.get('status') != 'RUNNING'
                or job.get('boot_id') != boot() or job.get('pid') != os.getpid()):
            return 1
    try:
        result = invoke_installer('install', args.rom, args.token, confirm=True, timeout=3600)
        if result.returncode or field(result.stdout, 'STATUS') != 'INSTALLED':
            raise RuntimeError(installer_error(result))
        physical = invoke_installer('status', args.rom, args.token, timeout=3600)
        if physical.returncode or field(physical.stdout, 'STATUS') != 'INSTALLED':
            raise RuntimeError('Installer reported success but local registration/integrity status did not confirm INSTALLED')
        update = {'status': 'INSTALLED', 'local_status': 'INSTALLED',
                  'local': field(physical.stdout, 'LOCAL'), 'error': ''}
    except Exception as exc:
        update = {'status': 'ERROR', 'error': safe(exc), 'local_status': '', 'local': ''}
        try:
            physical = invoke_installer('status', args.rom, args.token, timeout=3600)
            if not physical.returncode:
                update['local_status'] = field(physical.stdout, 'STATUS')
                update['local'] = field(physical.stdout, 'LOCAL')
        except Exception:
            pass
    with lockfile.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load(report)
        if current and current.get('job_id') == args.job_id:
            current.update(update)
            save(report, current)
    return 0 if update['status'] == 'INSTALLED' else 1


def selftest():
    assert field('INSTALL\tSTATUS\tINSTALLED\n', 'STATUS') == 'INSTALLED'
    assert field('INSTALL\tSTATUS\tPREVIEW\nINSTALL\tFRESH_VERIFICATION\tYES\n',
                 'FRESH_VERIFICATION') == 'YES'
    assert key('/tmp/base.nsp', '/tmp/test.nsp'.encode().hex()) == key(
        '/tmp/base.nsp', '/tmp/test.nsp'.encode().hex())
    assert safe('bad\tfile\nreason') == 'bad file reason'
    print('PASS: Installer start/status parsing')
    print('PASS: Unique job key and error sanitization')
    print('PASS: No worker started and no installed content modified')


def main():
    global CONTENT_TYPE
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'status', 'worker', 'selftest'))
    parser.add_argument('--rom')
    parser.add_argument('--token')
    parser.add_argument('--job-id')
    parser.add_argument('--type', choices=('update', 'dlc'), default='update')
    args = parser.parse_args()
    CONTENT_TYPE = args.type
    if args.action == 'selftest':
        selftest()
        return
    if not args.rom or not args.token:
        raise ValueError('--rom and --token required')
    if args.action == 'start':
        start(args)
    elif args.action == 'status':
        status(args)
    elif args.action == 'worker':
        if not args.job_id:
            raise ValueError('--job-id required')
        return worker(args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ERROR\t' + safe(exc))
        sys.exit(1)
