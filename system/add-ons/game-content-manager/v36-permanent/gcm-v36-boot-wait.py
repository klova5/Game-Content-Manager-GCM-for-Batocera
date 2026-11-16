#!/usr/bin/env python3
import fcntl
import hashlib
import importlib.util
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/userdata/system/add-ons/game-content-manager/v36-permanent")
SUP = ROOT / "gcm-v36-permanent-supervisor.py"
BIN = ROOT / "emulationstation-gcm-v36"
CANCEL = Path("/run/gcm-v36-service.cancel")
LOCK = Path("/run/gcm-v36-service-boot.lock")
STATE = Path("/run/gcm-v36-permanent.json")

EXPECTED = "b9d747269d85d306db4caaaf380c37aa06110c4e85e300e97bded56e5c4f846a"

def log(message):
    print("GCMV36:", message, flush=True)

def alive(pid):
    try:
        return pid > 0 and Path(f"/proc/{pid}/stat").is_file() and bool(
            (Path(f"/proc/{pid}/cmdline")).read_bytes().strip(b"\0")
        )
    except (OSError, ValueError):
        return False

def current():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}

def already_running():
    data = current()
    return (
        data.get("phase") == "v36_persistent_running"
        and alive(int(data.get("supervisor_pid") or 0))
    )

def check():
    h = hashlib.sha256()
    with BIN.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1048576), b""):
            h.update(block)
    if h.hexdigest() != EXPECTED:
        raise RuntimeError("Permanent executable checksum mismatch")
    if not SUP.is_file():
        raise RuntimeError("Permanent supervisor missing")
    log("Permanent runtime checks passed")

def trace_signals():
    """61TG: record any signal sent to the boot worker (boot-time starts used to vanish silently)."""
    def note(number, _frame):
        log(f"Boot worker received signal {signal.Signals(number).name}")
        if number == signal.SIGTERM:
            sys.exit(1)
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(number, note)


RETRY_PAUSE = 15
WORKDIR = "/userdata"
SUPERVISOR_LOG = Path("/userdata/system/logs/gcm-v36-permanent.log")


def start_supervisor(attempt):
    """Start the supervisor directly in its own session and return the process."""
    with open(SUPERVISOR_LOG, "ab") as output:
        child = subprocess.Popen(
            [sys.executable, "-B", str(SUP), "supervise"],
            cwd=WORKDIR, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=output, start_new_session=True)
    log(f"Supervisor started (attempt {attempt}, PID {child.pid})")
    return child


def main():
    trace_signals()
    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another startup worker is already active")
            return 0

        check()

        if already_running():
            log("Persistent v3.6 already running")
            return 0

        spec = importlib.util.spec_from_file_location("gcm_permanent", SUP)
        supervisor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervisor)

        log("Waiting for the standard Batocera frontend")

        for second in range(300):
            if CANCEL.exists():
                log("Startup cancelled")
                return 0

            if already_running():
                log("Persistent frontend already active")
                return 0

            if (
                len(supervisor.v34_pids()) == 1
                and len(supervisor.wrapper_pids()) == 1
                and supervisor.FLAG.exists()
            ):
                # Do not interfere with a temporary graphical test.
                with supervisor.LOCK.open("a+") as graphical_lock:
                    try:
                        fcntl.flock(
                            graphical_lock,
                            fcntl.LOCK_EX | fcntl.LOCK_NB
                        )
                    except BlockingIOError:
                        pass
                    else:
                        fcntl.flock(graphical_lock, fcntl.LOCK_UN)
                        break

            time.sleep(1)
        else:
            log("Frontend not ready after 300 seconds; leaving v3.4 untouched")
            return 1

        if CANCEL.exists():
            return 0

        log("Starting permanent GCM v3.6 supervisor")

        # 61TG: start the supervisor directly (its own session), report how it ends, and retry
        # when it stops before GCM is up (boot-time starts used to end without a trace).
        for attempt in range(1, 4):
            child = start_supervisor(attempt)
            for second in range(240):
                if CANCEL.exists():
                    subprocess.run([sys.executable, str(SUP), "stop"])
                    return 0
                if already_running():
                    log("PASS: GCM v3.6 persistent frontend running")
                    return 0
                phase = current().get("phase")
                if phase in ("recovery_attention_required", "v34_restored"):
                    log("Frontend startup failed or recovered: " + phase)
                    return 1
                code = child.poll()
                if code is not None:
                    reason = f"signal {signal.Signals(-code).name}" if code < 0 else f"exit code {code}"
                    log(f"Supervisor stopped before GCM started ({reason})")
                    break
                time.sleep(1)
            else:
                log("Startup confirmation timed out; inspect supervisor status")
                return 1
            if CANCEL.exists():
                return 0
            time.sleep(RETRY_PAUSE)

        log("Supervisor did not stay up after 3 attempts; the standard frontend stays")
        return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log("ERROR: " + str(exc))
        sys.exit(1)
