#!/usr/bin/env python3
"""GCM v3.5 isolated scan controller. Never installs or transfers files."""

import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

DEV = Path("/userdata/system/gcm-native-test/v34-development")
WORK = DEV / "v35-native-development"
BACKEND = WORK / "gcm-v35-backend.py"
STATE = WORK / "v35-scan-state.json"
LOG = WORK / "v35-scan-worker.log"
CONTROL_LOCK = WORK / "v35-scan-control.lock"
SCAN_LOCK = Path("/tmp/gcm-v34-full-scan.lock")

MODES = {"fast", "deep", "both"}
ACTIVE = {"starting", "running", "cancelling"}
BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def write_state(state):
    fd, temp = tempfile.mkstemp(prefix=".v35-scan-", dir=WORK)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(state, out, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, STATE)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"state": "idle", "phase": "NOT STARTED"}


def proc_identity(pid):
    try:
        proc = Path("/proc") / str(pid)
        args = (proc / "cmdline").read_bytes().split(b"\0")
        script = str(Path(__file__).resolve()).encode()

        if script not in args or b"_run" not in args:
            return None

        stat = (proc / "stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None


def same_worker(data):
    pid = data.get("pid")
    return (
        isinstance(pid, int)
        and pid > 1
        and bool(data.get("pid_start"))
        and proc_identity(pid) == data["pid_start"]
    )


def group_exists(data):
    pid = data.get("pid")
    if not isinstance(pid, int) or pid < 2:
        return False
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False


def backend():
    spec = importlib.util.spec_from_file_location(
        "gcm_v35_scan_backend", BACKEND
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update(data, **fields):
    with CONTROL_LOCK.open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        latest = read_state()

        if latest.get("job_id") != data.get("job_id"):
            return False

        if latest.get("state") == "cancelling":
            return False

        latest.update(fields)
        write_state(latest)
        return True


def refreshed():
    data = read_state()

    if (
        data.get("state") in ACTIVE
        and data.get("boot_id") != BOOT_ID
    ):
        data.update(
            state="interrupted",
            phase="PREVIOUS BOOT",
            error="Previous-boot scanner state is no longer active",
        )
        write_state(data)
        return data

    if data.get("state") in ACTIVE and data.get("pid"):
        if data["state"] == "cancelling":
            if not group_exists(data):
                data.update(
                    state="cancelled",
                    phase="CANCELLED",
                    finished=time.time(),
                )
                write_state(data)
        elif not same_worker(data) and not group_exists(data):
            data.update(
                state="interrupted",
                phase="INTERRUPTED",
                finished=time.time(),
                error="Scan worker exited before reporting completion",
            )
            write_state(data)

    return data


def show():
    # Coordinate status transitions with start, cancel, and worker.
    with CONTROL_LOCK.open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        data = refreshed()
    state = data.get("state", "idle")
    phase = data.get("phase", "NOT STARTED")

    lines = [
        "STATE\t" + state.upper(),
        "Mode: " + str(data.get("mode", "-")),
        "Phase: " + phase,
    ]

    started = data.get("started")
    if started:
        end_time = data.get("finished") or time.time()
        elapsed = int(max(0, end_time - started))
        lines.append("Elapsed: " + str(elapsed) + " seconds")

    if data.get("fast_candidates") is not None:
        lines.append(
            "Fast candidates: " + str(data["fast_candidates"])
        )

    mounts = data.get("mounts") or []

    if phase == "FAST SCAN":
        lines.extend([
            "FAST SCAN [....................] IN PROGRESS",
            "Searching known locations on all storage.",
            "Files remaining: Unknown",
        ])

    if phase == "DEEP INDEX" and mounts:
        done = 0
        current = []

        try:
            statuses = {
                str(row["mount_path"]): row
                for row in backend().deep_index_status("switch")
            }

            for mount in mounts:
                row = statuses.get(mount)

                if not row:
                    continue

                if float(row.get("started") or 0) < float(
                    started or 0
                ) - 0.1:
                    continue

                if row["status"] == "running":
                    current.append(mount)
                elif row["status"] in (
                    "complete", "partial", "error"
                ):
                    done += 1

        except Exception as exc:
            lines.append(
                "Progress temporarily unavailable: "
                + str(exc)[:100]
            )

        total = len(mounts)
        filled = round(20 * done / total)
        bar = "#" * filled + "." * (20 - filled)

        lines.append(
            "DEEP SCAN [" + bar + "] "
            + str(done) + "/" + str(total)
            + " storage locations processed"
        )
        lines.append(
            "Remaining locations: " + str(total - done)
        )

        if current:
            lines.append(
                "Currently scanning: " + ", ".join(current[:2])
            )

        lines.append(
            "Remaining files on current drive: Unknown"
        )

    if data.get("identified_before") is not None:
        lines.append(
            "Previously indexed packages identified: "
            + str(data["identified_before"])
        )

    if data.get("identified_after") is not None:
        lines.append(
            "Newly identified packages: "
            + str(data["identified_after"])
        )

    if phase in (
        "PREPARE INDEX BACKUP",
        "IDENTIFY INDEXED PACKAGES",
        "RECHECK NEW PACKAGES",
    ):
        lines.append("Processing package identities...")

    if data.get("deep_candidates") is not None:
        lines.append(
            "Deep-index candidates: " + str(data["deep_candidates"])
        )

    if data.get("identification_problem_count"):
        lines.append("Unresolved indexed files: "+str(data["identification_problem_count"]))
        for problem in data.get("identification_problems", [])[:3]:
            lines.append("Unresolved: "+str(problem.get("path", ""))+": "+str(problem.get("error", "")))
    if state == "partial":
        lines.append("SCAN FINISHED WITH PROBLEMS; unresolved files remain unverified")
    if state == "complete":
        lines.append("SCAN [####################] COMPLETE")
    elif state == "cancelling":
        lines.append(
            "Waiting for filesystem search to stop safely..."
        )

    if data.get("error"):
        lines.append("Error: " + str(data["error"])[:200])

    print("OK\t" + "\n".join(lines))


def start(mode):
    if mode not in MODES:
        raise ValueError("Choose fast, deep, or both")

    with CONTROL_LOCK.open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        previous = refreshed()

        if previous.get("state") in ACTIVE:
            print("ERROR\tScan already active. Use SCAN STATUS.")
            return 1

        with SCAN_LOCK.open("a+") as shared:
            try:
                fcntl.flock(
                    shared, fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError:
                print("ERROR\tAnother GCM scanner is running.")
                return 1

            job = {
                "schema": "GCM_V35_SCAN_V1",
                "job_id": uuid.uuid4().hex,
                "state": "starting",
                "phase": "STARTING",
                "mode": mode,
                "boot_id": BOOT_ID,
                "started": time.time(),
                "pid": 0,
            }

            write_state(job)

            with LOG.open("ab") as output:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "_run",
                        mode,
                        job["job_id"],
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    pass_fds=(shared.fileno(),),
                )

            identity = None
            for _ in range(40):
                identity = proc_identity(process.pid)
                if identity or process.poll() is not None:
                    break
                time.sleep(0.05)

            if not identity:
                job.update(
                    state="failed",
                    phase="START FAILED",
                    error="Worker exited before startup",
                )
                write_state(job)
                print("ERROR\tUnable to start scan worker")
                return 1

            job.update(
                pid=process.pid,
                pid_start=identity,
            )
            write_state(job)

    print(
        "OK\tSTATE\tRUNNING\nMode: " + mode
        + "\nBackground scanner: " + str(process.pid)
    )
    return 0


def cancel():
    with CONTROL_LOCK.open("a+") as control:
        fcntl.flock(control, fcntl.LOCK_EX)
        data = refreshed()

        if data.get("state") not in ACTIVE:
            print(
                "OK\tSTATE\t"
                + data.get("state", "idle").upper()
                + "\nNo running scan to cancel."
            )
            return 0

        if data["state"] == "cancelling":
            print("OK\tSTATE\tCANCELLING")
            return 0

        if not same_worker(data):
            print(
                "ERROR\tWorker identity not verified; "
                "refusing to signal."
            )
            return 1

        if os.getpgid(data["pid"]) != data["pid"]:
            print(
                "ERROR\tWorker process group mismatch; "
                "refusing cancellation."
            )
            return 1

        previous_phase = data.get("phase", "RUNNING")
        data.update(state="cancelling", phase="CANCELLING")
        write_state(data)

        # Watch cancellation outside the worker's process group.
        # This keeps the menu state current without opening Scan Status.
        try:
            with LOG.open("ab") as output:
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "_watch_cancel",
                        data["job_id"],
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as exc:
            data.update(
                state="running",
                phase=previous_phase,
            )
            write_state(data)
            print("ERROR\\tCannot start cancellation watcher: " + str(exc))
            return 1

        try:
            os.killpg(data["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass

    print(
        "OK\tSTATE\tCANCELLING\n"
        "Cancellation requested. Check SCAN STATUS."
    )
    return 0


def cancellation_watcher(job_id):
    """Wait for the entire cancelled process group to disappear."""
    while True:
        with CONTROL_LOCK.open("a+") as control:
            fcntl.flock(control, fcntl.LOCK_EX)
            data = read_state()

            if data.get("job_id") != job_id:
                return 0

            if data.get("boot_id") != BOOT_ID:
                return 0

            if data.get("state") != "cancelling":
                return 0

            data = refreshed()

            if data.get("state") != "cancelling":
                return 0

        time.sleep(1)



def v35_backup_index(gcm, job_id):
    """Consistent backup before any scan or reconciliation writes."""
    import sqlite3
    import urllib.parse

    original = Path(gcm.DB_PATH)
    if not original.is_file():
        raise RuntimeError("GCM database missing; scan not started")

    folder = WORK / "v35-index-backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    destination = folder / (job_id + "-index.sqlite")

    if destination.exists():
        raise RuntimeError("Index backup already exists")

    fd, temporary = tempfile.mkstemp(
        prefix=".v35-index-", dir=folder
    )
    os.close(fd)

    uri = (
        "file:"
        + urllib.parse.quote(str(original.resolve()), safe="/")
        + "?mode=ro"
    )

    try:
        source = __import__("sqlite3").connect(uri, uri=True)
        target = __import__("sqlite3").connect(temporary)

        try:
            source.backup(target)

            result = target.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

            if result != "ok":
                raise RuntimeError(
                    "SQLite backup integrity check failed"
                )

        finally:
            target.close()
            source.close()

        os.replace(temporary, destination)

    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return destination


def v35_reconcile_index(gcm):
    """Identify unresolved Switch packages using internal CNMT."""
    import importlib.util

    module_path = WORK / "gcm-v35-index-reconcile.py"

    spec = importlib.util.spec_from_file_location(
        "gcm_v35_scan_reconcile", module_path
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Reconciliation module unavailable")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.reconcile(gcm)

    if result.get("conflicts"):
        raise RuntimeError(
            "Index reconciliation detected conflicting records"
        )

    # Individual unreadable/unidentified packages remain problems; valid candidates still scan.
    # Index conflicts remain fatal above. No package is marked verified here.
    return result


def v36_index_problems(before, after):
    unique={}
    for problem in before.get("errors", [])+after.get("errors", []):
        key=(str(problem.get("path", "")), str(problem.get("error", "")))
        unique[key]={"path":key[0], "error":key[1]}
    rows=list(unique.values())
    return {"identification_problem_count":len(rows),
            "identification_problems":rows[:200],
            "identification_problems_truncated":len(rows)>200}


def worker(mode, job_id):
    for _ in range(100):
        data = read_state()
        if (
            data.get("job_id") == job_id
            and data.get("pid") == os.getpid()
        ):
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Worker startup handshake failed")

    initial_phase = "PREPARE INDEX BACKUP"

    if not update(
        data, state="running", phase=initial_phase
    ):
        return 1

    try:
        gcm = backend()

        if not update(data, phase="PREPARE INDEX BACKUP"):
            return 1

        index_backup = v35_backup_index(gcm, job_id)

        if not update(
            data,
            index_backup=str(index_backup),
            phase="IDENTIFY INDEXED PACKAGES",
        ):
            return 1

        before = v35_reconcile_index(gcm)

        if not update(
            data,
            identified_before=before["identified"],
            examined_before=before["examined"],
            unresolved_before=before["unresolved"],
        ):
            return 1

        mounts = [
            str(row["path"])
            for row in gcm.fast_scan_roots("switch")
        ]

        if not update(data, mounts=mounts):
            return 1

        if mode in ("fast", "both"):
            if not update(data, phase="FAST SCAN"):
                return 1

            found, stats = gcm.scan(system="switch")

            if not update(
                data,
                fast_candidates=stats.get(
                    "candidates", len(found)
                ),
            ):
                return 1

        if mode in ("deep", "both"):
            if not update(data, phase="DEEP INDEX"):
                return 1

            if mode == "deep":
                original = gcm._deep_find_command

                def all_roots(
                    root, system, profile, fast_roots
                ):
                    return original(
                        root, system, profile, []
                    )

                gcm._deep_find_command = all_roots

            result = gcm.deep_index(
                "switch", force=True
            )

            if not result.get("started"):
                raise RuntimeError(
                    "Deep index did not start: "
                    + str(result.get("reason"))
                )

            failures = [
                row
                for row in result.get("mounts", [])
                if row.get("status") not in (
                    "complete", "cached"
                )
            ]

            if not update(
                data, phase="RECHECK NEW PACKAGES"
            ):
                return 1

            after = v35_reconcile_index(gcm)

            if not update(
                data,
                identified_after=after["identified"],
                examined_after=after["examined"],
                unresolved_after=after["unresolved"],
            ):
                return 1

            update(
                data,
                state="partial" if failures or before.get("errors") or after.get("errors") else "complete",
                **v36_index_problems(before, after),
                phase="FINISHED",
                deep_candidates=result.get(
                    "candidates", 0
                ),
                finished=time.time(),
                error="; ".join(
                    str(row.get("path")) + ": "
                    + str(row.get("status"))
                    for row in failures
                )[:200],
            )
        else:
            if not update(
                data, phase="RECHECK NEW PACKAGES"
            ):
                return 1

            after = v35_reconcile_index(gcm)

            if not update(
                data,
                identified_after=after["identified"],
                examined_after=after["examined"],
                unresolved_after=after["unresolved"],
            ):
                return 1

            update(
                data,
                state="partial" if before.get("errors") or after.get("errors") else "complete",
                **v36_index_problems(before, after),
                phase="FINISHED",
                finished=time.time(),
            )

    except Exception as exc:
        update(
            data,
            state="failed",
            phase="FAILED",
            finished=time.time(),
            error=str(exc)[:300],
        )
        raise

    return 0


if __name__ == "__main__":
    try:
        action = (
            sys.argv[1]
            if len(sys.argv) > 1
            else "status"
        )

        if action == "status":
            show()
        elif action == "start" and len(sys.argv) == 3:
            sys.exit(start(sys.argv[2]))
        elif action == "cancel":
            sys.exit(cancel())
        elif action == "_watch_cancel" and len(sys.argv) == 3:
            sys.exit(cancellation_watcher(sys.argv[2]))
        elif action == "_run" and len(sys.argv) == 4:
            sys.exit(worker(sys.argv[2], sys.argv[3]))
        else:
            raise ValueError(
                "Usage: start fast|deep|both ; "
                "status ; cancel"
            )

    except Exception as exc:
        print(
            "ERROR\t"
            + type(exc).__name__
            + ": "
            + str(exc)
        )
        sys.exit(1)
