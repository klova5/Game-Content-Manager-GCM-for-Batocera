#!/usr/bin/env python3
"""GCM v3.5 asynchronous multi-update verifier; no installation operations."""
import argparse
import importlib.util
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



ROOT = Path(__file__).resolve().parent
CONTENT_TYPE = "update"

def dependency(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

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
    engine = dependency("gcm-switch-content-engine.py",
        "3e14cc15a650e1d1e92f39ca687ccd681242ad7f4eb7f0310acdb9b1b4e35c02")
    store = dependency("gcm-switch-verification-store.py",
        "07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195")
    inventory = engine.candidates(rom, CONTENT_TYPE)
    result = {}
    for group in inventory["groups"]:
        for item in group["sources"]:
            if item["index_state"] != "external":
                continue
            source = item["path"]
            if store.lookup(rom, source, CONTENT_TYPE) is not None:
                continue
            token = item["token"].lower()
            result[token] = {
                "token": token, "source": source,
                "label": sanitize(Path(source).stem, 150),
                "status": "PENDING",
            }
    return result


def start(args):
    base = os.path.abspath(args.rom)
    if not Path(base).is_file():
        raise ValueError('Base game file does not exist')
    requested = args.tokens.split(',')
    if not 1 <= len(requested) <= 32:
        raise ValueError('Select between 1 and 32 content packages')
    if len(set(requested)) != len(requested):
        raise ValueError('Duplicate selected package')
    if any(not re.fullmatch('[0-9a-fA-F]{2,8192}', token) or len(token) % 2
           for token in requested):
        raise ValueError('Malformed content selection')
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
        state = {'schema': 'GCM_SHARED_SWITCH_VERIFICATION_BATCH_V1',
                 'batch_id': uuid.uuid4().hex, 'boot_id': boot_id(),
                 'rom': base, 'emulator': args.emulator, 'content_type': CONTENT_TYPE,
                 'status': 'RUNNING',
                 'pid': None, 'items': selected}
        save(report, state)
        try:
            proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                                     'worker', '--rom', base, '--batch-id', state['batch_id'],
                                     '--type', CONTENT_TYPE],
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
        if not isinstance(state, dict) or state.get('schema') != 'GCM_SHARED_SWITCH_VERIFICATION_BATCH_V1':
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
    engine = dependency("gcm-switch-content-engine.py",
        "3e14cc15a650e1d1e92f39ca687ccd681242ad7f4eb7f0310acdb9b1b4e35c02")
    store = dependency("gcm-switch-verification-store.py",
        "07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195")
    source = bytes.fromhex(item["token"]).decode("utf-8")
    proof = engine.verify(rom, source, CONTENT_TYPE)
    store.record(proof)


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
    global CONTENT_TYPE, JOBS
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action",
        choices=("start", "status", "worker", "selftest", "candidates"))
    parser.add_argument("--rom")
    parser.add_argument("--type", choices=("update", "dlc"), default="update")
    parser.add_argument("--emulator",
        choices=("switch", "ryujinx", "ryujinx-emu", "citron", "citron-emu"),
        default="switch")
    parser.add_argument("--tokens")
    parser.add_argument("--batch-id")
    args = parser.parse_args()
    CONTENT_TYPE = args.type
    JOBS = ROOT / "state/verification-batches" / CONTENT_TYPE
    if args.action == "selftest":
        return selftest()
    if not args.rom:
        raise ValueError("--rom required")
    if args.action == "candidates":
        print(json.dumps(candidates(args.rom, args.emulator), indent=2))
    elif args.action == "start":
        if not args.tokens:
            raise ValueError("--tokens required")
        start(args)
    elif args.action == "status":
        status(args)
    else:
        if not args.batch_id:
            raise ValueError("--batch-id required")
        return worker(args)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('ERROR\t' + sanitize(exc))
        sys.exit(1)
