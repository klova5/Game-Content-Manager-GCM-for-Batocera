"""Measured content telemetry; no install or selection authorization."""
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import types
import uuid

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "state/global-content-actions"
_last = {}

def safe(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink progress path rejected")
    return path

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def event(phase, label="", completed=None, total=None, operation=""):
    if not isinstance(phase, str) or not phase or len(phase) > 80:
        raise ValueError("Invalid progress phase")
    if not isinstance(label, str) or not isinstance(operation, str):
        raise ValueError("Invalid progress text")
    if (completed is None) != (total is None):
        raise ValueError("Both byte measurements required")
    if completed is not None and (
        type(completed) is not int or type(total) is not int
        or total < 0 or not 0 <= completed <= total
    ):
        raise ValueError("Invalid measured progress")
    return {
        "schema": "GCM_GLOBAL_ACTION_PROGRESS_V1", "phase": phase,
        "label": label.replace("\n", " ").replace("\t", " ")[:240],
        "operation": operation[:80], "completed_bytes": completed,
        "total_bytes": total,
        "percent": None if completed is None else (
            100 if total == 0 else completed * 100 // total),
    }

def atomic(path, data):
    path = safe(path)
    fd, temporary = tempfile.mkstemp(prefix=".progress-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def process_identity(pid):
    tail = Path("/proc", str(pid), "stat").read_text().rsplit(")", 1)[1].split()
    if tail[0] == "Z":
        raise ValueError("Progress owner has exited")
    return [pid, int(tail[19])]

def owner_location():
    raw = os.environ.get("GCM_ACTION_PROGRESS_PATH")
    if not raw:
        return None
    path = safe(raw)
    if (path.name != "progress.json" or path.parent.parent != JOBS
        or not re.fullmatch(r"[0-9a-f]{32}", path.parent.name)):
        raise ValueError("Dedicated action progress path required")
    return path

def check_owner(path, owner, root_only=False):
    if owner.get("job_id") != path.parent.name or owner.get("boot_id") != boot():
        raise ValueError("Progress job or boot changed")
    state = json.loads(safe(path.parent / "state.json").read_text())
    if state.get("status") != "RUNNING" or state.get("job_id") != owner["job_id"]:
        raise ValueError("Progress action is no longer running")
    if owner.get("root_identity") != process_identity(state["pid"]):
        raise ValueError("Progress root process changed")
    group = os.getpgrp()
    if owner.get("process_group") != group:
        if root_only or owner.get("delegates", {}).get(str(group)) != process_identity(group):
            raise ValueError("Progress session is not delegated")
    if owner.get("active_item", "") != os.environ.get("GCM_PROGRESS_ITEM", ""):
        raise ValueError("Progress belongs to a different queue item")

def update_owner(change, root_only=False):
    path = owner_location()
    if path is None:
        return
    lockpath = safe(path.parent / ".progress-owner.lock")
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        owner_path = safe(path.parent / "progress-owner.json")
        owner = json.loads(owner_path.read_text())
        check_owner(path, owner, root_only)
        change(owner)
        atomic(owner_path, owner)

def activate(token="", label=""):
    def change(owner):
        owner.update(active_item=token, active_label=label, delegates={})
    update_owner(change, root_only=True)
    os.environ["GCM_PROGRESS_ITEM"] = token

def delegate(pid):
    if not os.environ.get("GCM_ACTION_PROGRESS_PATH"):
        return
    if os.getpgid(pid) != pid:
        raise ValueError("Expected a separate provider session")
    identity = process_identity(pid)
    def change(owner):
        delegates = owner.setdefault("delegates", {})
        if len(delegates) >= 64:
            raise ValueError("Too many delegated progress sessions")
        delegates[str(pid)] = identity
    update_owner(change)

def publish(phase, label="", completed=None, total=None, operation=""):
    data = event(phase, label, completed, total, operation)
    local = os.environ.get("GCM_PROGRESS_LOCAL_PATH")
    path = safe(local) if local else owner_location()
    if path is None:
        return data
    now = time.monotonic()
    key = (str(path), phase, label)
    if local:
        if (path.name != "progress.json" or path.parent.parent != ROOT
            or not path.parent.name.startswith("step61-ps4-dlc-preparation-")
            or not re.fullmatch(r"[0-9a-f]{32}", os.environ.get("GCM_PROGRESS_NONCE", ""))):
            raise ValueError("Dedicated sandbox telemetry path required")
        data.update(child_pid=os.getpid(), nonce=os.environ["GCM_PROGRESS_NONCE"],
                    boot_id=boot())
    else:
        owner = json.loads(safe(path.parent / "progress-owner.json").read_text())
        check_owner(path, owner)
        data.update(job_id=owner["job_id"], item_token=owner.get("active_item", ""))
        if owner.get("active_label"):
            data["file_label"] = data["label"]
            data["label"] = owner["active_label"]
    if completed not in (None, 0, total) and now - _last.get(key, 0) < 0.2:
        return data
    data["updated_monotonic"] = now
    atomic(path, data)
    _last[key] = now
    return data

def run_preparation(arguments, work, timeout=3600):
    # The child writes only inside its existing writable sandbox directory.
    # The parent validates and relays the telemetry outside the sandbox.
    work = safe(work)
    nonce = uuid.uuid4().hex
    environment = os.environ.copy()
    environment.update(GCM_PROGRESS_LOCAL_PATH=str(work / "progress.json"),
                       GCM_PROGRESS_NONCE=nonce)
    seen = None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(arguments, stdout=out, stderr=err, env=environment)
        try:
            deadline = time.monotonic() + timeout
            while True:
                path = safe(work / "progress.json")
                if path.is_file():
                    raw = path.read_bytes()
                    if raw != seen:
                        data = json.loads(raw)
                        if (data.get("schema") != "GCM_GLOBAL_ACTION_PROGRESS_V1"
                            or data.get("child_pid") != process.pid
                            or data.get("nonce") != nonce or data.get("boot_id") != boot()):
                            raise ValueError("Sandbox telemetry identity mismatch")
                        publish(data["phase"], data["label"], data["completed_bytes"],
                                data["total_bytes"], data.get("operation", ""))
                        seen = raw
                code = process.poll()
                if code is not None:
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                time.sleep(0.1)
        except BaseException:
            process.kill()
            process.wait()
            raise
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(arguments, code,
            out.read().decode(errors="replace"), err.read().decode(errors="replace"))

def copyfileobj(incoming, outgoing, length=8 * 1024 * 1024):
    size = os.fstat(incoming.fileno()).st_size - incoming.tell()
    if size < 0:
        raise ValueError("Invalid copy source position")
    done = 0
    label = Path(str(getattr(incoming, "name", "content"))).name
    publish("COPYING", label, 0, size)
    while True:
        block = incoming.read(length)
        if not block:
            break
        written = outgoing.write(block)
        if written is not None and written != len(block):
            raise OSError("Short content write")
        done += len(block)
        publish("COPYING", label, done, size)
    if done != size:
        raise ValueError("Copy byte count differs from source")

def instrument_switch(module):
    """Attach counting to the already pinned Switch archive hash primitive.

    The legacy package provider can import this primitive into its own module.
    Its original function still computes and returns the authoritative digest.
    """
    allowed = {
        "citron-package-verify-v03.py", "gcm-v35-auto-preview.py",
        "gcm-v35-internal-integrity.py",
    }
    visited = set()
    def wrapper(original):
        if getattr(original, "_gcm_measured", False):
            return original
        @functools.wraps(original)
        def measured(stream, entry, name):
            size = entry[1]
            done = 0
            publish("VERIFYING CONTENT ARCHIVE", str(name), 0, size)
            class CountingStream:
                def __getattr__(self, key):
                    return getattr(stream, key)
                def read(self, length=-1):
                    nonlocal done
                    data = stream.read(length)
                    done += len(data)
                    publish("VERIFYING CONTENT ARCHIVE", str(name), done, size)
                    return data
            return original(CountingStream(), entry, name)
        measured._gcm_measured = True
        return measured
    def visit(current, depth):
        if id(current) in visited or depth > 4:
            return
        visited.add(id(current))
        for name, value in list(vars(current).items()):
            if isinstance(value, types.ModuleType):
                if Path(str(getattr(value, "__file__", ""))).name in allowed:
                    visit(value, depth + 1)
            elif name == "sha256_member" and callable(value):
                filename = Path(str(getattr(getattr(value, "__code__", None), "co_filename", "")))
                if filename.name == "gcm-v35-internal-integrity.py":
                    # Source pin is checked by the package dependency chain.
                    setattr(current, name, wrapper(value))
    visit(module, 0)
    return module

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def copy_tree(source, destination, label="", operation="ENABLING"):
    source, destination = safe(source).resolve(strict=True), safe(destination)
    if not source.is_dir() or destination.exists():
        raise ValueError("Existing source and unused destination required")
    directories, files, total = [], [], 0
    for current, dirs, names in os.walk(source, followlinks=False):
        for name in dirs:
            item = safe(Path(current) / name)
            if not item.is_dir():
                raise ValueError("Regular source directory required")
            directories.append(item.relative_to(source))
        for name in names:
            item = safe(Path(current) / name)
            st = item.stat()
            if not stat.S_ISREG(st.st_mode):
                raise ValueError("Regular source file required")
            files.append((item, item.relative_to(source), identity(st)))
            total += st.st_size
    destination.mkdir(mode=0o700)
    for path in sorted(directories, key=lambda p: (len(p.parts), str(p))):
        (destination / path).mkdir(mode=0o700)
    done = 0
    publish("COPYING", label, done, total, operation)
    for path, relative, before in files:
        fd = os.open(destination / relative,
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with path.open("rb") as incoming, os.fdopen(fd, "wb") as outgoing:
            if identity(os.fstat(incoming.fileno())) != before:
                raise ValueError("Source changed before copying")
            remaining = before[2]
            while remaining:
                data = incoming.read(min(8 * 1024 * 1024, remaining))
                if not data:
                    raise ValueError("Source truncated")
                if outgoing.write(data) != len(data):
                    raise OSError("Short content write")
                remaining -= len(data)
                done += len(data)
                publish("COPYING", label, done, total, operation)
            if incoming.read(1) or identity(os.fstat(incoming.fileno())) != before:
                raise ValueError("Copy source changed")
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if identity(path.stat()) != before:
            raise ValueError("Copy source identity changed")
    publish("COPYING", label, done, total, operation)
    return {"completed_bytes": done, "total_bytes": total}
