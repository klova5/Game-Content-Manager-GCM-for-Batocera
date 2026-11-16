"""Background PS4 update installation; activation remains unassessed."""
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
JOBS = ROOT / "state/platform-unified-install-jobs/ps4/update"
BOOT = Path("/proc/sys/kernel/random/boot_id")
SCHEMA = "GCM_PLATFORM_INSTALL_JOB_V1"
TERMINAL = {"INSTALLED_FILES_VERIFIED", "ERROR"}

def dependency():
    path = ROOT / "gcm-ps4-install-command-v02.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != (
        "4fbf802c0d25f03dd9bf39b28f77f3db1113131942ea3d71126bd7d0fb06a052"
    ):
        raise ValueError("Installation command dependency changed")
    spec = importlib.util.spec_from_file_location("install_job_provider", path)
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
        raise ValueError("Invalid installation token")
    source = bytes.fromhex(token).decode("utf-8")
    if not os.path.isabs(source) or chr(0) in source or os.path.abspath(source) != source:
        raise ValueError("Absolute normalized source required")
    return source


def checked_preflight(rom, token):
    base, source, managed = source_context(rom, token)
    coordinator = dependency().load("gcm-ps4-version-install.py")
    coordinator.idle()
    transaction = coordinator.load("gcm-ps4-file-transaction-v02.py")
    coordinator.configuration(transaction)
    return {"base": str(base), "source": str(source)}

def emit(job):
    print("INSTALL\tSTATUS\t" + job.get("status", "ERROR"))
    print("INSTALL\tJOB_ID\t" + str(job.get("job_id", "")))
    if job.get("phase"):
        print("INSTALL\tPHASE\t" + safe(job["phase"]))
    if job.get("error"):
        print("INSTALL\tERROR\t" + safe(job["error"]))
    if job.get("recovery_required"):
        print("INSTALL\tRECOVERY_REQUIRED\tYES")
    print("STATUS\tSOURCE PACKAGE PRESERVED")


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
        directory_fd = os.open(JOBS, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
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
    if not Path(rom).is_file() and not Path(rom).is_dir():
        raise FileNotFoundError('Base game unavailable')
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize installation starts across all packages.
    with (JOBS / '.global-install-start.lock').open('a+') as global_lock:
        fcntl.flock(global_lock, fcntl.LOCK_EX)
        for prior in JOBS.glob('*.json'):
            prior_job = load(prior)
            if prior_job and (
                prior_job.get('status') == 'RUNNING'
                or prior_job.get('recovery_required') is True
            ):
                raise RuntimeError('An installation is running or requires recovery assessment')
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
                job.update(status='ERROR', error='Unable to launch installation worker: ' + str(exc))
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
            job.update(status='ERROR', recovery_required=True,
                       error='Installation worker stopped; recovery assessment required')
            save(report, job)
    if job.get("status") == "INSTALLED_FILES_VERIFIED":
        validate_installed(job)
    emit(job)

def validate_installed_managed(job):
    result = job.get("install_result")
    if (
        not isinstance(result, dict)
        or job.get("boot_id") != boot()
        or result.get("status") != "INSTALLED_FILES_VERIFIED"
        or result.get("source_preserved") is not True
        or result.get("configuration_changed") is not False
        or result.get("activation_assessed") is not False
        or result.get("in_game_availability") != "UNVERIFIED"
        or identity(job["rom"]) != job.get("base_stat_identity")
    ):
        raise ValueError("Current installed-file evidence required")
    proof = result["installed_evidence"]
    if (
        proof.get("status") != "GAME_BOUND_INSTALLED_FILES_VERIFIED"
        or proof.get("game_relationship_verified") is not True
        or proof.get("source_bound_installed_files_verified") is not True
        or proof.get("installation_authorized") is not False
    ):
        raise ValueError("Game-bound installed evidence required")
    relationship = proof["relationship_evidence"]
    base = relationship["base_metadata_path"]
    if identity(base) != relationship["base_stat_identity"]:
        raise ValueError("Installed base metadata changed")
    evidence = proof["installed_evidence"]
    installed = Path(evidence["installed_root"])
    for item in evidence["verified_files"]:
        path = installed / item["relative_path"]
        if (
            any(p.is_symlink() for p in (path, *path.parents))
            or identity(path) != item["stat_identity"]
        ):
            raise ValueError("Installed files changed since job verification")
    source = validate_token(job["token"])
    if (
        any(p.is_symlink() for p in (Path(source), *Path(source).parents))
        or identity(source) != evidence["source_manifest"]["stat_identity"]
    ):
        raise ValueError("Managed package changed since installation")


def copy_provider():
    path = ROOT / "gcm-platform-managed-copy.py"
    if (
        path.is_symlink()
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != "84e30b2803c5c7aa2c2d576031567ca66ad260142380e7138e991a97bf787f6a"
    ):
        raise ValueError("Managed-copy provider changed")
    spec = importlib.util.spec_from_file_location("install_managed_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def source_context(rom, token):
    command = dependency()
    source = Path(validate_token(token))
    if any(path.is_symlink() for path in (source, *source.parents)):
        raise ValueError("Symlink installation source rejected")
    source = source.resolve(strict=True)
    game = command.load("gcm-ps4-base-identity.py").identify(rom)
    base = Path(game["metadata_path"])
    managed = (
        Path("/userdata/system/game-content/ps4")
        / game["application_id"] / "updates" / source.name
    )
    if source == managed:
        command.resolve(rom, source)
    else:
        preview = copy_provider().preview(base, source, "ps4", "update")
        if preview.get("registration_performed") is True:
            raise ValueError("Unexpected registration claim")
    return base, source, managed

def ensure_managed(rom, token):
    base, source, managed = source_context(rom, token)
    before = identity(source)
    if source != managed:
        result = copy_provider().copy(base, source, "ps4", "update")
        if (
            result.get("state") != "READY"
            or result.get("source_preserved") is not True
            or result.get("registration_performed") is not False
            or Path(result["destination"]) != managed
        ):
            raise ValueError("Verified managed copy required")
    dependency().resolve(rom, managed)
    if identity(source) != before:
        raise ValueError("Original package changed during copy")
    return managed, str(source), before

def validate_installed(job):
    result = job.get("install_result")
    if not isinstance(result, dict):
        raise ValueError("Installation result required")
    original = validate_token(job["token"])
    if (
        result.get("original_source") != original
        or any(path.is_symlink() for path in (Path(original), *Path(original).parents))
        or identity(original) != result.get("original_source_identity")
    ):
        raise ValueError("Original source preservation evidence changed")
    record = result["installed_record"]
    if record.get("selection_performed") is not False:
        raise ValueError("Installation must remain separate from selection")
    adjusted = dict(job, token=record["source"].encode("utf-8").hex())
    validate_installed_managed(adjusted)


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
    phase = "PREPARING"

    def record_phase(value):
        with lockfile.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = load(report)
            if (
                not current or current.get("job_id") != args.job_id
                or current.get("status") != "RUNNING"
                or current.get("boot_id") != boot()
                or current.get("pid") != os.getpid()
            ):
                raise ValueError("Installation worker ownership changed")
            current["phase"] = value
            save(report, current)

    try:
        if identity(args.rom) != job["base_stat_identity"]:
            raise ValueError("Launcher changed before installation")
        record_phase(phase)
        command = dependency()
        phase = "COPYING"
        record_phase(phase)
        managed, original_source, original_identity = ensure_managed(
            args.rom, args.token)
        phase = "PREPARING"
        record_phase(phase)
        coordinator, base, source = command.resolve(args.rom, managed)
        coordinator.idle()
        transaction = coordinator.load("gcm-ps4-file-transaction-v02.py")
        coordinator.configuration(transaction)
        payload = coordinator.load(
            "gcm-platform-content-service-v03.py").verify_game_content(
                base, source, "ps4")
        if payload["content_type"] != "update":
            raise ValueError("Verified game-matching update required")
        prepared = command.prepare(source)
        phase = "INSTALLING"
        record_phase(phase)
        result = coordinator.install(base, source, prepared)
        if identity(original_source) != original_identity:
            raise ValueError("Original package changed before installation completed")
        result["original_source"] = original_source
        result["original_source_identity"] = original_identity
        candidate = dict(job, status="INSTALLED_FILES_VERIFIED",
                         install_result=result)
        validate_installed(candidate)
        update = {
            "status": "INSTALLED_FILES_VERIFIED", "phase": "COMPLETE",
            "install_result": result, "error": "", "recovery_required": False,
        }
    except Exception as exc:
        update = {
            "status": "ERROR", "phase": phase, "error": safe(exc),
            "recovery_required": phase == "INSTALLING",
        }
    with lockfile.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = load(report)
        if current and current.get("job_id") == args.job_id:
            current.update(update)
            save(report, current)
    return 0 if update["status"] == "INSTALLED_FILES_VERIFIED" else 1

def main():
    global JOBS, CONTENT_TYPE
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "worker"))
    parser.add_argument("--rom", required=True, help="PS4 game launcher or game directory")
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", required=True, choices=("update",))
    parser.add_argument("--job-id")
    args = parser.parse_args()
    CONTENT_TYPE = args.type
    JOBS = ROOT / "state/platform-install-jobs/ps4" / CONTENT_TYPE
    for parent in (JOBS, *JOBS.parents):
        if parent.is_symlink():
            raise ValueError("Symlink installation-job directory rejected")
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
