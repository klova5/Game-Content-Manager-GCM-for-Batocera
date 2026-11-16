"""Background managed-copy jobs; never install or activate content."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT_TYPE = "update"
JOBS = ROOT / "state/platform-copy-jobs/ps4/update"
BOOT = Path("/proc/sys/kernel/random/boot_id")
SCHEMA = "GCM_PLATFORM_COPY_JOB_V1"
TERMINAL = {"READY", "ERROR"}

def dependency():
    path = ROOT / "gcm-platform-managed-copy-measured-v01.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "6cd726f3344398dc9de4d5c8b620865316f03e335eb22b9022ceb5dd2bec26da":
        raise ValueError("Managed-copy dependency changed")
    spec = importlib.util.spec_from_file_location("copy_job_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    st = Path(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def validate_token(token):
    if (
        not isinstance(token, str) or not 2 <= len(token) <= 8192
        or len(token) % 2
        or any(c not in "0123456789abcdef" for c in token)
    ):
        raise ValueError("Invalid copy token")
    source = bytes.fromhex(token).decode("utf-8")
    if not os.path.isabs(source) or chr(0) in source or os.path.abspath(source) != source:
        raise ValueError("Absolute normalized source required")
    return source

def checked_preflight(rom, token):
    preview = dependency().preview(
        rom, validate_token(token), "ps4", CONTENT_TYPE)
    if preview.get("status") != "PREVIEW" or preview.get("registration_performed") is not False:
        raise ValueError("Safe managed-copy preview required")
    return preview

def emit(job):
    print("COPY\tSTATUS\t" + job.get("status", "ERROR"))
    print("COPY\tJOB_ID\t" + str(job.get("job_id", "")))
    result = job.get("copy_result", {})
    if result.get("destination"):
        print("COPY\tLOCAL\t" + safe(result["destination"], 4096))
    if job.get("error"):
        print("COPY\tERROR\t" + safe(job["error"]))
    print("STATUS\tEXTERNAL ORIGINAL PRESERVED")


def boot():
    return BOOT.read_text().strip()

def safe(text, maximum=480):
    return str(text).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')[:maximum]

def key(rom, token):
    source = validate_token(token)
    return hashlib.sha256((os.path.abspath(rom) + '\0' + source).encode()).hexdigest()

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

def start(args):
    rom = os.path.abspath(args.rom)
    token = args.token.lower()
    validate_token(token)
    if not Path(rom).is_file():
        raise FileNotFoundError('Base game unavailable')
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialise starts, including across different packages, so one game does not
    # receive simultaneous managed-copy writes.
    with (JOBS / '.global-copy-start.lock').open('a+') as global_lock:
        fcntl.flock(global_lock, fcntl.LOCK_EX)
        for prior in JOBS.glob('*.json'):
            prior_job = load(prior)
            if prior_job and prior_job.get('status') == 'RUNNING' and alive(prior_job):
                raise RuntimeError('A managed copy is already running')
        checked_preflight(rom, token)
        report, lockfile = paths(rom, token)
        with lockfile.open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            job = {'schema': SCHEMA, 'job_id': uuid.uuid4().hex, 'boot_id': boot(),
                   'rom': rom, 'token': token, 'base_stat_identity': identity(rom), 'status': 'RUNNING', 'pid': None,
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
                job.update(status='ERROR', error='Unable to launch copy worker: ' + str(exc))
                save(report, job)
                raise
            job['pid'] = proc.pid
            save(report, job)
    emit(job)

def status(args):
    report, lockfile = paths(args.rom, args.token)
    if not report.exists():
        print('COPY\tSTATUS\tNOT_STARTED')
        return
    with lockfile.open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = load(report)
        if not isinstance(job, dict) or job.get('schema') != SCHEMA:
            raise ValueError('Invalid install job record')
        if job['status'] == 'RUNNING' and (job.get('boot_id') != boot() or not alive(job)):
            job.update(status='ERROR', error='Copy worker stopped before completing; managed destination requires reassessment')
            save(report, job)
    if job.get("status") == "READY":
        validate_ready(job)
    emit(job)

def validate_ready(job):
    result = job.get("copy_result")
    if not isinstance(result, dict) or job.get("boot_id") != boot():
        raise ValueError("Current copy evidence required")
    destination = Path(result.get("destination", ""))
    if (
        not destination.is_absolute() or not destination.is_file()
        or any(p.is_symlink() for p in (destination, *destination.parents))
        or result.get("state") != "READY"
        or result.get("registration_performed") is not False
        or result.get("source_preserved") is not True
        or result.get("managed_stat_identity") != identity(destination)
        or job.get("base_stat_identity") != identity(job["rom"])
    ):
        raise ValueError("Managed copy or base identity changed")

def worker(args):
    report, lockfile = paths(args.rom, args.token)
    with lockfile.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = load(report)
        if (
            not job or job.get("job_id") != args.job_id
            or job.get("status") != "RUNNING"
            or job.get("boot_id") != boot() or job.get("pid") != os.getpid()
        ):
            return 1
    try:
        if identity(args.rom) != job["base_stat_identity"]:
            raise ValueError("Base metadata changed before copy")
        result = dependency().copy(
            args.rom, validate_token(args.token), "ps4", CONTENT_TYPE)
        candidate = dict(job, status="READY", copy_result=result)
        validate_ready(candidate)
        update = {"status": "READY", "copy_result": result, "error": ""}
    except Exception as exc:
        update = {"status": "ERROR", "error": safe(exc)}
    with lockfile.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load(report)
        if current and current.get("job_id") == args.job_id:
            current.update(update)
            save(report, current)
    return 0 if update["status"] == "READY" else 1

def main():
    global JOBS, CONTENT_TYPE
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "worker"))
    parser.add_argument("--rom", required=True, help="Internal base PARAM.SFO")
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", required=True, choices=("update", "dlc"))
    parser.add_argument("--job-id")
    args = parser.parse_args()
    CONTENT_TYPE = args.type
    JOBS = ROOT / "state/platform-copy-jobs/ps4" / CONTENT_TYPE
    for parent in (JOBS, *JOBS.parents):
        if parent.is_symlink():
            raise ValueError("Symlink copy-job directory rejected")
    validate_token(args.token)
    if args.action == "start":
        start(args)
    elif args.action == "status":
        status(args)
    else:
        if not args.job_id:
            raise ValueError("--job-id required")
        return worker(args)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + safe(exc))
        sys.exit(1)
