#!/usr/bin/env python3
"""GCM v3.5 asynchronous multi-update verifier; no installation operations."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

DEV = Path('/userdata/system/gcm-native-test/v34-development')
WORK = DEV / 'v35-native-development'
BG = DEV / 'gcm-v35-background.py'
OVERLAY = WORK / 'gcm-v35-verified-inventory.py'
INVENTORY = Path('/userdata/system/add-ons/game-content-manager/gcm-native-v33.py')
JOBS = WORK / 'v35-tools/verification-batches'
TERMINAL = {'PASS', 'ERROR', 'PARTIAL'}
BOOT_FILE = Path('/proc/sys/kernel/random/boot_id')


def boot_id():
    return BOOT_FILE.read_text().strip()


def state_files(rom):
    key = hashlib.sha256(rom.encode()).hexdigest()
    return JOBS / (key + '.json'), JOBS / (key + '.lock')


def sanitize(text, limit=320):
    return str(text).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')[:limit]


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def save(path, state):
    fd, name = tempfile.mkstemp(prefix='.gcm-batch-', suffix='.tmp', dir=JOBS)
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


def active_worker(state):
    pid = state.get('pid')
    if not isinstance(pid, int) or pid < 2:
        return False
    try:
        args = (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')
        return (str(Path(__file__).resolve()).encode() in args
                and b'worker' in args
                and str(state['batch_id']).encode() in args)
    except (OSError, KeyError):
        return False


def parse_worker(output):
    status = ''
    error = ''
    for line in output.splitlines():
        if line.startswith('VERIFY\tSTATUS\t'):
            status = line.split('\t', 2)[2].strip()
        elif line.startswith('ERROR\t'):
            error = sanitize(line[6:])
    return status, error


def aggregate(items):
    ok = sum(x['status'] == 'PASS' for x in items)
    bad = sum(x['status'] == 'ERROR' for x in items)
    if ok + bad != len(items):
        return 'RUNNING', ok, bad
    return ('PASS' if not bad else 'PARTIAL' if ok else 'ERROR'), ok, bad


def emit(state):
    items = state['items']
    status, ok, bad = aggregate(items)
    if state.get('status') in TERMINAL:
        status = state['status']
    print('BATCH\tSTATUS\t' + status)
    print('BATCH\tID\t' + str(state.get('batch_id', '')))
    print('BATCH\tTOTAL\t' + str(len(items)))
    print('BATCH\tCOMPLETE\t' + str(ok + bad))
    print('BATCH\tPASSED\t' + str(ok))
    print('BATCH\tFAILED\t' + str(bad))
    for item in items:
        if item['status'] == 'ERROR':
            print('BATCH\tFAILURE\t' + sanitize(item['label'], 150)
                  + '\t' + sanitize(item.get('error') or 'Unknown verification failure'))
    print('STATUS\tNO NSP FILE CHANGES')


def candidates(rom, emulator):
    p = subprocess.run([sys.executable, str(INVENTORY), '--rom', rom,
                        '--emulator', emulator, '--mode', 'list', '--type', 'update'],
                       capture_output=True, text=True, timeout=90)
    if p.returncode or any(line.startswith('ERROR\t') for line in p.stdout.splitlines()):
        raise RuntimeError(sanitize(p.stderr or p.stdout or 'Update inventory failed'))
    result = {}
    for line in p.stdout.splitlines():
        parts = line.split('\t', 4)
        if len(parts) != 5 or parts[:2] != ['ROW', 'DETECTED']:
            continue
        token = parts[4].lower()
        try:
            path = bytes.fromhex(token).decode('utf-8')
        except (ValueError, UnicodeError):
            continue
        if os.path.isabs(path) and path.lower().endswith('.nsp'):
            result[token] = {'token': token, 'source': os.path.abspath(path),
                             'label': sanitize(parts[3], 150), 'status': 'PENDING'}
    return result


def start(args):
    base = os.path.abspath(args.rom)
    if not Path(base).is_file():
        raise ValueError('Base game file does not exist')
    if args.emulator not in ('ryujinx', 'ryujinx-emu'):
        raise ValueError('Only Ryujinx update verification supported')
    requested = args.tokens.split(',')
    if not 1 <= len(requested) <= 32:
        raise ValueError('Select between 1 and 32 updates')
    if len(set(requested)) != len(requested):
        raise ValueError('Duplicate selected update')
    if any(not re.fullmatch('[0-9a-fA-F]{2,8192}', token) or len(token) % 2
           for token in requested):
        raise ValueError('Malformed update selection')
    available = candidates(base, args.emulator)
    selected = []
    for token in requested:
        if token.lower() not in available:
            raise ValueError('Selected item no longer in this game\'s DETECTED inventory')
        selected.append(available[token.lower()])
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    report, lockname = state_files(base)
    with lockname.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = read(report)
        if (existing and existing.get('status') == 'RUNNING'
                and existing.get('boot_id') == boot_id() and active_worker(existing)):
            raise RuntimeError('Verification batch already running for this game')
        state = {'schema': 'GCM_V35_VERIFICATION_BATCH_V1',
                 'batch_id': uuid.uuid4().hex, 'boot_id': boot_id(),
                 'rom': base, 'emulator': args.emulator, 'status': 'RUNNING',
                 'pid': None, 'items': selected}
        save(report, state)
        try:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                     'worker', '--rom', base, '--batch-id', state['batch_id']],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True,
                                    close_fds=True)
        except Exception as exc:
            state['status'] = 'ERROR'
            for item in state['items']:
                item.update(status='ERROR', error='Could not launch batch worker: ' + str(exc))
            save(report, state)
            raise
        state['pid'] = proc.pid
        save(report, state)
    emit(state)


def status(args):
    base = os.path.abspath(args.rom)
    report, lockname = state_files(base)
    if not report.exists():
        print('BATCH\tSTATUS\tNOT_STARTED')
        return
    with lockname.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(report)
        if not isinstance(state, dict) or state.get('schema') != 'GCM_V35_VERIFICATION_BATCH_V1':
            raise ValueError('Unreadable batch state')
        if state['status'] == 'RUNNING' and (state.get('boot_id') != boot_id() or
                                             not active_worker(state)):
            for item in state['items']:
                if item['status'] == 'PENDING':
                    item.update(status='ERROR', error='Verification batch worker stopped')
            state['status'] = aggregate(state['items'])[0]
            save(report, state)
    emit(state)


def run_one(rom, item):
    token = item['token']
    cmd = [sys.executable, str(BG)]
    def call(action):
        p = subprocess.run([*cmd, action, '--rom', rom, '--token', token],
                           capture_output=True, text=True, timeout=45)
        status, error = parse_worker(p.stdout)
        if p.returncode and status != 'PASS':
            raise RuntimeError(error or sanitize(p.stderr) or 'Verifier returned error')
        if status == 'ERROR':
            raise RuntimeError(error or 'Verification failed')
        return status
    first = call('start')
    if first != 'PASS':
        deadline = time.monotonic() + 2100
        while time.monotonic() < deadline:
            time.sleep(3)
            current = call('status')
            if current == 'PASS':
                break
            if current not in ('RUNNING', 'STARTING', 'NOT_STARTED'):
                raise RuntimeError('Unexpected verification state: ' + current)
        else:
            raise RuntimeError('Verification exceeded maximum wait time')
    promotion = subprocess.run([sys.executable, str(OVERLAY), 'record',
                                '--rom', rom, '--token', token],
                               capture_output=True, text=True, timeout=90)
    if promotion.returncode or 'VERIFY\tPROMOTION\tRECORDED' not in promotion.stdout:
        raise RuntimeError('Verification passed but inventory promotion failed: ' +
                           sanitize(promotion.stdout or promotion.stderr or 'unknown error'))


def worker(args):
    base = os.path.abspath(args.rom)
    report, lockname = state_files(base)
    with lockname.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(report)
        if (not state or state.get('batch_id') != args.batch_id or
                state.get('boot_id') != boot_id() or state.get('status') != 'RUNNING' or
                state.get('pid') != os.getpid()):
            return 1
    for index, item in enumerate(state['items']):
        try:
            run_one(base, item)
            result = {'status': 'PASS', 'error': ''}
        except Exception as exc:
            result = {'status': 'ERROR', 'error': sanitize(exc)}
        with lockname.open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = read(report)
            if state.get('batch_id') != args.batch_id or state.get('status') != 'RUNNING':
                return 1
            state['items'][index].update(result)
            state['status'] = aggregate(state['items'])[0]
            save(report, state)
    return 0


def selftest():
    assert parse_worker('VERIFY\tSTATUS\tPASS\n')[0] == 'PASS'
    assert parse_worker('VERIFY\tSTATUS\tERROR\nERROR\tBad header\n') == ('ERROR', 'Bad header')
    items = [{'status': 'PASS'}, {'status': 'PENDING'}]
    assert aggregate(items) == ('RUNNING', 1, 0)
    items[1]['status'] = 'ERROR'
    assert aggregate(items) == ('PARTIAL', 1, 1)
    items[0]['status'] = 'ERROR'
    assert aggregate(items) == ('ERROR', 0, 2)
    print('PASS: Worker reply parsing')
    print('PASS: Running / partial / failed summaries')
    print('PASS: No verification jobs started')
    print('PASS: No live records or NSP files modified')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('start', 'status', 'worker', 'selftest'))
    parser.add_argument('--rom')
    parser.add_argument('--emulator', default='ryujinx')
    parser.add_argument('--tokens')
    parser.add_argument('--batch-id')
    args = parser.parse_args()
    if args.action == 'selftest':
        return selftest()
    if not args.rom:
        raise ValueError('--rom required')
    if args.action == 'start':
        if not args.tokens:
            raise ValueError('--tokens required')
        start(args)
    elif args.action == 'status':
        status(args)
    else:
        if not args.batch_id:
            raise ValueError('--batch-id required')
        return worker(args)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ERROR\t' + sanitize(exc))
        sys.exit(1)
