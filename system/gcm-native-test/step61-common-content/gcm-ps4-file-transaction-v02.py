"""Journaled PS4 file replacement with explicit target binding and rollback."""
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import tempfile

ROOT = Path(__file__).resolve().parent

def safe(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Symlink transaction path rejected")
    return path

def workdir(path):
    path = safe(path).resolve(strict=True)
    if (
        path.parent != ROOT.resolve()
        or not path.name.startswith("step61-ps4-transaction-")
        or not path.is_dir()
    ):
        raise ValueError("Dedicated transaction rehearsal directory required")
    return path

def member(work, relative):
    if (
        not isinstance(relative, str)
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or any(c in relative for c in ("\\", ":", chr(0)))
        or any(ord(c) < 32 or ord(c) == 127 for c in relative)
    ):
        raise ValueError("Unsafe transaction member")
    return safe(work / relative)


def checked_target(path):
    if path is None:
        return None
    path = safe(path).resolve(strict=True)
    if (
        path.parent != Path("/userdata/roms/ps4")
        or re.fullmatch(r"CUSA[0-9]{5}", path.name) is None
        or not path.is_dir()
    ):
        raise ValueError("Existing PS4 game directory required")
    return path

def target_path(work, relative, target_root):
    local = member(work, relative)
    suffix = local.relative_to(work / "game")
    return safe((target_root if target_root is not None else work / "game") / suffix)

def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def read(path):
    path = safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Regular transaction file required")
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    identity = lambda st: (
        st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if identity(before) != identity(after) or identity(safe(path).stat()) != identity(before):
        raise ValueError("Transaction file changed while reading")
    return {
        "sha256": digest.hexdigest(),
        "size": before.st_size,
        "mode": stat.S_IMODE(before.st_mode),
    }

def same(actual, expected):
    return (
        actual["sha256"] == expected["sha256"]
        and actual["size"] == expected["size"]
    )

def copy_new(source, destination, expected):
    source, destination = safe(source), safe(destination)
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        destination_fd = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            expected.get("mode", 0o600))
    except BaseException:
        os.close(source_fd)
        raise
    with os.fdopen(source_fd, "rb") as incoming, os.fdopen(destination_fd, "wb") as outgoing:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            outgoing.write(block)
        outgoing.flush()
        os.fchmod(outgoing.fileno(), expected.get("mode", 0o600))
        os.fsync(outgoing.fileno())
    if not same(read(destination), expected):
        raise ValueError("Transaction copy verification failed")
    sync(destination.parent)

def save(work, journal):
    fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=work)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(journal, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, work / "journal.json")
        sync(work)
    finally:
        if temporary.exists():
            temporary.unlink()

def lock(work, target_root=None):
    location = (ROOT / "ps4-installation-transaction.lock"
                if target_root is not None else work / "transaction.lock")
    fd = os.open(location,
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd

def apply(work, changes, stop_after=None, target_root=None):
    work = workdir(work)
    target_root = checked_target(target_root)
    lock_fd = lock(work, target_root)
    try:
        if (work / "journal.json").exists():
            raise ValueError("Existing transaction journal requires inspection")
        if not changes or len(changes) > 1000:
            raise ValueError("Bounded nonempty change list required")
        records, used = [], set()
        for number, change in enumerate(changes):
            target = target_path(work, change["target"], target_root)
            staged = member(work, change["staged"])
            target.relative_to(target_root if target_root is not None else work / "game")
            staged.relative_to(work / "staged")
            if not target.parent.is_dir() or target == staged:
                raise ValueError("Existing target parent required")
            for candidate in (target, staged):
                folded = str(candidate).casefold()
                if folded in used:
                    raise ValueError("Duplicate transaction path")
                used.add(folded)
            after = read(staged)
            if not same(after, change):
                raise ValueError("Staged file differs from requested hash or size")
            before = read(target) if target.exists() else None
            if target_root is not None:
                if "expected_before" not in change:
                    raise ValueError("Explicit previous-target evidence required")
                wanted = change["expected_before"]
                if (before is None) != (wanted is None):
                    raise ValueError("Target existence differs from preview")
                if before is not None and not same(before, wanted):
                    raise ValueError("Target content differs from preview")
            records.append({
                "target": change["target"],
                "staged": str(staged.relative_to(work)),
                "backup": "backups/" + str(number),
                "before": before, "after": after,
            })
        backups = work / "backups"
        backups.mkdir()
        for record in records:
            if record["before"] is not None:
                copy_new(target_path(work, record["target"], target_root),
                         member(work, record["backup"]), record["before"])
        journal = {
            "schema": "GCM_PS4_FILE_TRANSACTION_V2",
            "target_root": str(target_root) if target_root is not None else None,
            "state": "PREPARED", "records": records,
            "explicit_target_supplied": target_root is not None,
        }
        save(work, journal)
        for number, record in enumerate(records):
            target = target_path(work, record["target"], target_root)
            staged = member(work, record["staged"])
            if record["before"] is None:
                if target.exists():
                    raise ValueError("Previously absent target appeared")
            elif not same(read(target), record["before"]):
                raise ValueError("Target changed before replacement")
            if not same(read(staged), record["after"]):
                raise ValueError("Staged file changed before replacement")
            journal["state"] = "APPLYING"
            journal["next_record"] = number
            save(work, journal)
            os.replace(staged, target)
            sync(target.parent)
            if not same(read(target), record["after"]):
                raise ValueError("Replacement read-back failed")
            if stop_after == number + 1:
                raise RuntimeError("SIMULATED_INTERRUPTION")
        journal["state"] = "APPLIED"
        save(work, journal)
        return journal
    finally:
        os.close(lock_fd)

def rollback(work, target_root=None):
    work = workdir(work)
    target_root = checked_target(target_root)
    lock_fd = lock(work, target_root)
    try:
        journal_path = safe(work / "journal.json")
        journal = json.loads(journal_path.read_text())
        if (
            journal.get("schema") != "GCM_PS4_FILE_TRANSACTION_V2"
            or journal.get("target_root") != (
                str(target_root) if target_root is not None else None)
            or journal.get("explicit_target_supplied") is not (target_root is not None)
            or journal.get("state") not in
                ("PREPARED", "APPLYING", "APPLIED", "ROLLING_BACK", "ROLLED_BACK")
        ):
            raise ValueError("Unsupported transaction journal")
        # Check every target and backup before changing any target.
        for record in journal["records"]:
            target = target_path(work, record["target"], target_root)
            target.relative_to(target_root if target_root is not None else work / "game")
            before = record["before"]
            if before is not None:
                backup = member(work, record["backup"])
                backup.relative_to(work / "backups")
                if not same(read(backup), before):
                    raise ValueError("Rollback backup differs from original")
            if target.exists():
                actual = read(target)
                if not same(actual, record["after"]) and (
                    before is None or not same(actual, before)
                ):
                    raise ValueError("Rollback target has unexpected content")
            elif before is not None:
                raise ValueError("Rollback replacement target missing")
        journal["state"] = "ROLLING_BACK"
        save(work, journal)
        for record in reversed(journal["records"]):
            target = target_path(work, record["target"], target_root)
            before = record["before"]
            if before is None:
                if target.exists():
                    target.unlink()
                    sync(target.parent)
            elif not same(read(target), before):
                fd, temporary = tempfile.mkstemp(prefix=".restore-", dir=target.parent)
                os.close(fd)
                temporary = Path(temporary)
                temporary.unlink()
                copy_new(member(work, record["backup"]), temporary, before)
                os.replace(temporary, target)
                sync(target.parent)
        journal["state"] = "ROLLED_BACK"
        save(work, journal)
        return journal
    finally:
        os.close(lock_fd)
