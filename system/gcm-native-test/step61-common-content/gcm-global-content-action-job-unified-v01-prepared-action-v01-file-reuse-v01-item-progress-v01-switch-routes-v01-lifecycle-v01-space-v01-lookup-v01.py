"""Shared background DLC installation queues and Apply Changes jobs."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
SWITCH = ROOT.parent / "step60-shared-switch"
JOBS = ROOT / "state/global-content-actions"
SCHEMA = "GCM_GLOBAL_CONTENT_ACTION_V1"
PINS = {
    str(ROOT / "gcm-platform-global-backend-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-lifecycle-v01-space-v01-lookup-v01.py"):
        "60674337130b16e42bc4f851ad3f7ada84b64d5a7b64fcccfa2c0a31b7031d6e",
    str(SWITCH / "citron-global-backend-measured-v01-switch-routes-v01-space-v01-lookup-v01.py"):
        "3334779c3d15d3dd11ab8d099796596540eca7d049f7ec0aa58a244fdd7ff3f9",
    str(SWITCH / "ryujinx-global-backend-measured-v01-switch-routes-v01-space-v01.py"):
        "1156b3a49ae5b1aecaff48ec29626258c24af43de7e2de8bf6fbeec2cceb9980",
    str(SWITCH / "citron-content-install-job-measured-v01-space-v01.py"):
        "bd8c97b7de85be2d7c847289b6fdeb2416bdd391744ba4153d7bdb53f9bff0ac",
    str(SWITCH / "ryujinx-content-install-job-measured-v01-switch-routes-v01-space-v01.py"):
        "fad75ae31295b83994edf2a159769d57bc412f0f02a225e8d2ed705567cec1a9",
}


# Optional measured telemetry; original content checks remain authoritative.
def _gcm_progress():
    import hashlib as _hashlib
    import importlib.util as _importlib
    from pathlib import Path as _Path
    if "_GCM_PROGRESS_MODULE" not in globals():
        _path = _Path('/userdata/system/gcm-native-test/step61-common-content/gcm-global-action-progress-measured-v01.py')
        if _path.is_symlink() or _hashlib.sha256(_path.read_bytes()).hexdigest() != 'd51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
            raise ValueError("Measured progress dependency changed")
        _spec = _importlib.spec_from_file_location("gcm_measured_progress", _path)
        _module = _importlib.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        globals()["_GCM_PROGRESS_MODULE"] = _module
    return globals()["_GCM_PROGRESS_MODULE"]

def clean(value, maximum=400):
    return str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")[:maximum]

def safe(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink action path rejected")
    return path

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def location(job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid action job ID")
    return safe(JOBS / job_id)

def save(path, data):
    safe(path)
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def read(job_id):
    data = json.loads(safe(location(job_id) / "state.json").read_text())
    if data.get("schema") != SCHEMA or data.get("job_id") != job_id:
        raise ValueError("Invalid action job record")
    return data

def tokens(raw, emulator=None, operation=None):
    values = raw.split(",") if raw else []
    if len(values) > 32 or len(values) != len(set(values)):
        raise ValueError("Select at most 32 unique DLC items")
    for value in values:
        if (operation == "apply" and emulator in ("citron", "citron-emu")
            and re.fullmatch(r"citron:dlc:[0-9A-F]{16}:[0-9A-F]{16}", value)):
            continue
        if not re.fullmatch(r"(?:[0-9a-f]{2}){1,4096}", value):
            raise ValueError("Invalid content selection token")
        decoded = bytes.fromhex(value).decode("utf-8")
        if chr(0) in decoded or not os.path.isabs(decoded) or os.path.abspath(decoded) != decoded:
            raise ValueError("Absolute normalized content token required")
    return values

def provider(emulator):
    if emulator == "shadps4":
        return ROOT / "gcm-platform-global-backend-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-lifecycle-v01-space-v01-lookup-v01.py", None
    if emulator in ("citron", "citron-emu"):
        return SWITCH / "citron-global-backend-measured-v01-switch-routes-v01-space-v01-lookup-v01.py", SWITCH / "citron-content-install-job-measured-v01-space-v01.py"
    if emulator in ("ryujinx", "ryujinx-emu"):
        return SWITCH / "ryujinx-global-backend-measured-v01-switch-routes-v01-space-v01.py", SWITCH / "ryujinx-content-install-job-measured-v01-switch-routes-v01-space-v01.py"
    raise ValueError("Content action adapter unavailable for this emulator")

def checked(path):
    safe(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != PINS[str(path)]:
        raise ValueError("Background action provider changed: " + path.name)
    return str(path)

def call(path, arguments, timeout=180, progress_path=None):
    environment = os.environ.copy()
    if progress_path is not None:
        environment["GCM_ACTION_PROGRESS_PATH"] = str(progress_path)
    result = subprocess.run(
        [sys.executable, "-B", checked(path), *arguments],
        capture_output=True, text=True, timeout=timeout, env=environment)
    errors = [
        line[6:] for line in result.stdout.splitlines()
        if line.startswith("ERROR\t")
    ]
    if result.returncode or errors:
        raise ValueError(clean(errors[-1] if errors else result.stderr or result.stdout))
    return result.stdout

def field(output, name):
    prefix = "INSTALL\t" + name + "\t"
    matches = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if len(matches) > 1:
        raise ValueError("Duplicate installation field")
    return matches[0] if matches else ""

def inventory(job):
    backend = ROOT / 'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py'
    if backend.is_symlink() or hashlib.sha256(backend.read_bytes()).hexdigest() != '77b5e60766957cb015ea2b09e619a8b9f443fdcb54c62a3ca6806b0309c15ce5':
        raise ValueError('Shared menu policy changed')
    PINS[str(backend)] = '77b5e60766957cb015ea2b09e619a8b9f443fdcb54c62a3ca6806b0309c15ce5'

    output = call(backend, [
        "--rom", job["rom"], "--emulator", job["emulator"],
        "--mode", "list", "--type", "dlc", "--refresh-evidence"])
    rows = {}
    for line in output.splitlines():
        if not line.startswith("ROW\t"):
            continue
        parts = line.split("\t")
        if len(parts) != 5 or parts[4] in rows:
            raise ValueError("Invalid global DLC inventory")
        rows[parts[4]] = {
            "section": parts[1], "state": parts[2], "label": clean(parts[3]),
            "token": parts[4],
        }
    return rows

def alive(job):
    if job.get("boot_id") != boot() or type(job.get("pid")) is not int:
        return False
    try:
        proc = Path("/proc") / str(job["pid"])
        command = (proc / "cmdline").read_bytes().split(b"\0")
        status = (proc / "stat").read_text().rsplit(")", 1)[1].split()[0]
        return (
            status != "Z" and os.fsencode(str(Path(__file__).resolve())) in command
            and b"worker" in command and job["job_id"].encode() in command
        )
    except (FileNotFoundError, ProcessLookupError):
        return False

def emit(job):
    progress = {}
    path = location(job["job_id"]) / "progress.json"
    if job["status"] == "RUNNING" and path.exists():
        safe(path)
        progress = json.loads(path.read_text())
        if (
            progress.get("schema") != "GCM_GLOBAL_ACTION_PROGRESS_V1"
            or progress.get("job_id") != job["job_id"]
        ):
            raise ValueError("Action progress belongs to another job")
    items = job.get("items", [])
    complete = sum(item["state"] == "PASS" for item in items)
    ready_items = _apply_items().ready(job)
    if job["status"] == "RUNNING" and job.get("operation") == "apply":
        complete = len(ready_items)
    percent = progress.get("percent")
    if percent is not None and (type(percent) is not int or not 0 <= percent <= 100):
        raise ValueError("Invalid action percentage")
    values = {
        "STATUS": job["status"], "ID": job["job_id"],
        "TOTAL": len(items), "COMPLETE": complete,
        "LABEL": progress.get("label") or job.get("label", ""),
        "PHASE": progress.get("phase") or job.get("phase", ""),
        "PERCENT": -1 if percent is None else percent,
        "RECOVERY_REQUIRED": "YES" if job.get("recovery_required") else "NO",
        "ERROR": job.get("error", ""),
    }
    for name, value in values.items():
        print("ACTION\t" + name + "\t" + clean(value))
    for index, item in enumerate(items, 1):
        item_state = "READY" if item.get("token") in ready_items else item["state"]
        print("\t".join(("ITEM", str(index), item_state, clean(item["label"]))))


PREPARE_PROVIDERS = {'ps4_identity': ('/userdata/system/gcm-native-test/step61-common-content/gcm-ps4-base-identity.py', 'f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438'), 'ps4_verify': ('/userdata/system/gcm-native-test/step61-common-content/gcm-game-content-verification-measured-v01.py', 'e12dd416cff52a6e7fda36c9e203d23429d61d3d8c3633388131d126cb2833cc'), 'ps4_proofs': ('/userdata/system/gcm-native-test/step61-common-content/gcm-platform-verification-store.py', '851961927b00fe9166ee2bb28c7d583a552a032c05fd2c125549e4ebd031deaa'), 'switch_verify': ('/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-content-engine-measured-v01.py', '7120a24469daa2e31316235a79989060777f92701628ce3711ae2b67d9b262c4'), 'switch_proofs': ('/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-store.py', '07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195')}

def prepare_dependency(key):
    import importlib.util
    path,expected=PREPARE_PROVIDERS[key]
    path=safe(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('Preparation provider changed: '+path.name)
    spec=importlib.util.spec_from_file_location('prepare_'+key,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def verify_for_preparation(job,item):
    source=Path(bytes.fromhex(item['token']).decode('utf-8'))
    if not source.is_absolute() or source.is_symlink():raise ValueError('Regular absolute preparation source required')
    source=safe(source).resolve(strict=True)
    if job['emulator']=='shadps4':
        identity=prepare_dependency('ps4_identity').identify(job['rom'])
        proof=prepare_dependency('ps4_verify').verify(identity['metadata_path'],source,'ps4')
        if proof.get('application_id')!=identity['application_id']:
            raise ValueError('Preparation proof belongs to another game')
        store=prepare_dependency('ps4_proofs')
    elif job['emulator'] in ('citron','citron-emu','ryujinx','ryujinx-emu'):
        proof=prepare_dependency('switch_verify').verify(job['rom'],source,'dlc')
        store=prepare_dependency('switch_proofs')
    else:raise ValueError('Preparation unavailable for this adapter')
    if proof.get('status')!='PASS' or proof.get('content_type')!='dlc':
        raise ValueError('Verified game-bound DLC proof required')
    store.record(proof)

def latest(args):
    provider(args.emulator)
    rom=str(safe(args.rom).resolve(strict=True))
    matches=[]
    if JOBS.exists():
        for directory in JOBS.iterdir():
            if directory.is_dir() and re.fullmatch('[0-9a-f]{32}',directory.name):
                job=read(directory.name)
                if (job.get('rom')==rom and job.get('operation')==args.operation
                    and provider(job.get('emulator'))==provider(args.emulator)):
                    matches.append((safe(directory/'state.json').stat().st_mtime_ns,job))
    if not matches:
        print('ACTION\tSTATUS\tNOT_STARTED');return
    job=max(matches,key=lambda pair:pair[0])[1]
    emit(job)

def start(args):
    if not args.confirm:
        raise ValueError("Confirmed content action required")
    provider(args.emulator)
    selected = tokens(args.tokens or "", args.emulator, args.operation)
    if args.operation in ("install", "prepare") and not selected:
        raise ValueError("Select DLC to verify and prepare")
    rom = safe(args.rom).resolve(strict=True)
    JOBS.mkdir(mode=0o700, parents=True, exist_ok=True)
    with safe(JOBS / ".start.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for directory in JOBS.iterdir():
            if directory.is_dir() and re.fullmatch(r"[0-9a-f]{32}", directory.name):
                prior = read(directory.name)
                if prior["status"] == "RUNNING" or prior.get("recovery_required"):
                    raise ValueError("A content action is running or requires recovery assessment")
        job_id = uuid.uuid4().hex
        work = location(job_id)
        work.mkdir(mode=0o700)
        job = {
            "schema": SCHEMA, "job_id": job_id, "boot_id": boot(),
            "rom": str(rom), "emulator": args.emulator,
            "operation": args.operation, "tokens": selected,
            "status": "RUNNING", "phase": "QUEUED", "pid": None,
            "items": [], "error": "", "mutation_started": False,
            "recovery_required": False,
        }
        save(work / "state.json", job)
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", str(Path(__file__).resolve()),
                 "worker", "--job-id", job_id],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            job["pid"] = process.pid
            save(work / "state.json", job)
        except Exception as exc:
            job.update(status="ERROR", error=clean(exc))
            save(work / "state.json", job)
            raise
    emit(job)


def _preparation_lifecycle():
    import importlib.util as _ilu
    _path = Path(__file__).resolve().parent / 'gcm-preparation-lifecycle-v01.py'
    if _path.is_symlink() or hashlib.sha256(_path.read_bytes()).hexdigest() != '7fcc2819de7760fd1a72cdb4043323b4bba2aa70c2a1f8d930ddaf9ffd6d1399':
        raise ValueError('Preparation lifecycle dependency changed')
    _spec = _ilu.spec_from_file_location('gcm_preparation_lifecycle', _path)
    _module = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    return _module

def worker(job_id):
    with safe(JOBS / ".start.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = read(job_id)
        if (
            job["status"] != "RUNNING" or job["boot_id"] != boot()
            or job["pid"] != os.getpid()
        ):
            return 1
    work = location(job_id)
    state = work / "state.json"
    save(work / "progress-owner.json", {
        "job_id": job_id, "boot_id": boot(), "process_group": os.getpgrp(),
        "root_identity": _gcm_progress().process_identity(os.getpid()),
        "active_item": "", "delegates": {},
    })
    os.environ["GCM_ACTION_PROGRESS_PATH"] = str(work / "progress.json")
    os.environ["GCM_PROGRESS_ITEM"] = ""
    provider_failure = None
    try:
        _gcm_progress().publish("READING INVENTORY")
        job["phase"] = "READING INVENTORY"
        save(state, job)
        rows = inventory(job)
        selected = set(job["tokens"])
        if job["operation"] in ("install", "prepare"):
            allowed_states = ("UNVERIFIED", "VERIFIED_EXTERNAL") if job["operation"] == "prepare" else ("VERIFIED_EXTERNAL",)
            allowed_sections = ("DETECTED", "AVAILABLE") if job["operation"] == "prepare" else ("AVAILABLE",)
            if any(token not in rows or rows[token]["section"] not in allowed_sections
                   or rows[token]["state"] not in allowed_states for token in selected):
                raise ValueError("Only matching unprepared DLC candidates may be queued")
            job["items"] = [
                dict(rows[token], state="WAITING") for token in job["tokens"]]
        else:
            installed = {
                token: row for token, row in rows.items()
                if row["section"] == "AVAILABLE" and row["state"] in ("ENABLED", "DISABLED")
            }
            if not selected.issubset(installed):
                raise ValueError("Only installed DLC may be enabled")
            job["items"] = [
                dict(row, state="WAITING",
                     operation="ENABLING" if token in selected else "DISABLING")
                for token, row in installed.items()
                if (row["state"] == "ENABLED") != (token in selected)
            ]
        save(state, job)
        backend, installer = provider(job["emulator"])
        if job["operation"] in ("install", "prepare"):
            for item in job["items"]:
                _gcm_progress().activate(item["token"], item["label"])
                _gcm_progress().publish("PREPARING INSTALLATION", item["label"])
                item["state"] = "RUNNING"
                job.update(label=item["label"], phase="VERIFYING" if job["operation"] == "prepare" else "PREPARING")
                save(state, job)
                if job["operation"] == "prepare":
                    verify_for_preparation(job,item)
                job.update(phase="PREPARING DLC", mutation_started=True)
                save(state,job)
                def installation(action):
                    if installer is None:
                        return call(backend, [
                            "--rom", job["rom"], "--emulator", job["emulator"],
                            "--mode", "install-job", "--action", action,
                            "--type", "dlc", "--token", item["token"]])
                    return call(installer, [
                        action, "--rom", job["rom"], "--token", item["token"],
                        "--type", "dlc"])
                reply = installation("start")
                install_id = field(reply, "JOB_ID")
                if field(reply, "STATUS") != "RUNNING" or not install_id:
                    raise ValueError("DLC installation did not start")
                deadline = time.monotonic() + 7200
                while True:
                    time.sleep(1)
                    reply = installation("status")
                    if field(reply, "JOB_ID") != install_id:
                        raise ValueError("Installation job identity changed")
                    status = field(reply, "STATUS")
                    job["phase"] = field(reply, "PHASE") or "INSTALLING"
                    save(state, job)
                    if status == "INSTALLED":
                        break
                    if status != "RUNNING":
                        # The provider job ID was matched above; missing acknowledgements
                        # remain conservative and cannot waive recovery assessment.
                        provider_failure = {'status': status, 'job_id': install_id,
                            'phase': field(reply, 'PHASE'),
                            'recovery_required': field(reply, 'RECOVERY_REQUIRED')}
                        raise ValueError(field(reply, "ERROR") or "DLC installation failed")
                    if time.monotonic() > deadline:
                        raise ValueError("Installation monitoring timed out; assess provider job")
                item["state"] = "PASS"
                _gcm_progress().activate("")
                _gcm_progress().publish("ITEM COMPLETE", item["label"], 1, 1)
                save(state, job)
        else:
            job.update(phase="APPLYING DLC CHANGES", mutation_started=True)
            save(state, job)
            reply = call(backend, [
                "--rom", job["rom"], "--emulator", job["emulator"],
                "--mode", "commit", "--type", "dlc",
                "--selected-available", ",".join(job["tokens"]),
                "--selected-detected", "", "--confirm",
            ], timeout=7200, progress_path=work / "progress.json")
            if not reply.startswith("OK\t"):
                raise ValueError("DLC Apply Changes did not confirm success")
            for item in job["items"]:
                _apply_items().mark_ready(item["token"])
            final = inventory(job)
            enabled = {
                token for token, row in final.items()
                if row["section"] == "AVAILABLE" and row["state"] == "ENABLED"
            }
            if enabled != selected:
                raise ValueError("Final enabled DLC selection disagrees")
            for item in job["items"]:
                item["state"] = "PASS"
        job.update(status="PASS", phase="COMPLETE", error="", recovery_required=False)
    except Exception as exc:
        for item in job.get("items", []):
            if item["state"] == "RUNNING":
                item["state"] = "ERROR"
        job.update(
            status="ERROR", error=clean(exc, 1200),
            recovery_required=_preparation_lifecycle().recovery_required(
                bool(job.get("mutation_started")), provider_failure))
        if provider_failure is not None:
            job['provider_failure'] = provider_failure
    save(state, job)
    return 0 if job["status"] == "PASS" else 1

def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "worker", "latest"))
    parser.add_argument("--rom")
    parser.add_argument("--emulator")
    parser.add_argument("--operation", choices=("install", "prepare", "apply"))
    parser.add_argument("--tokens", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--job-id")
    args = parser.parse_args()
    if args.action == "latest":
        if not args.rom or not args.emulator or args.operation not in ("prepare", "apply", "install"):
            raise ValueError("Game, emulator and operation required for status lookup")
        latest(args)
    elif args.action == "start":
        if not args.rom or not args.emulator or not args.operation:
            raise ValueError("Game, emulator and operation required")
        start(args)
    elif args.action == "worker":
        return worker(args.job_id)
    else:
        job = read(args.job_id)
        if job["status"] == "RUNNING" and not alive(job):
            job.update(
                status="ERROR", error="Background action stopped; assess recovery",
                recovery_required=bool(job.get("mutation_started")))
            save(location(args.job_id) / "state.json", job)
        emit(job)
    return 0


def _apply_items():
    import importlib.util
    path=ROOT/'gcm-global-apply-item-progress.py'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='c814cf20d5def03eb1b3437a75df24eeaa73095690feb7541a1bc91d445e95c7':
        raise ValueError('Shared Apply item progress dependency changed')
    spec=importlib.util.spec_from_file_location('apply_item_telemetry',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + clean(exc, 1200))
        sys.exit(1)
