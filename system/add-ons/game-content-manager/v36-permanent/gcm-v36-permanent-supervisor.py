#!/usr/bin/env python3
"""Permanent, supervised GCM v3.6 frontend (derived from the v3.5 permanent supervisor)."""

import faulthandler
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

WORK = Path("/userdata/system/gcm-native-test/v34-development/v35-native-development")
BIN = Path("/userdata/system/add-ons/game-content-manager/v36-permanent/emulationstation-gcm-v36")
RUNTIME = Path("/userdata/system/gcm-native-test/v36-step61-common-menu-test-32/runtime")
LOG = Path("/userdata/system/logs/gcm-v36-permanent.log")

STATE = Path("/run/gcm-v36-permanent.json")
STOP = Path("/run/gcm-v36-permanent.stop")
SHUTDOWN = Path("/run/gcm-v36-permanent-shutdown.marker")
LOCK = Path("/run/gcm-v35-graphical-test.lock")
FLAG = Path("/var/run/emulationstation-standalone")

# No time limit for permanent deployment

V34_SHA = "b36c766420752fc0530f17fef4bc52995328f863485fd4b2a90403558abf5cd3"
V36_SHA = "b9d747269d85d306db4caaaf380c37aa06110c4e85e300e97bded56e5c4f846a"


def checksum(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log(message):
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    with LOG.open("a") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def cmdline(pid):
    try:
        return [
            part.decode("utf-8", "replace")
            for part in (Path("/proc") / str(pid) / "cmdline")
                .read_bytes().split(b"\0")
            if part
        ]
    except OSError:
        return []


def processes():
    for path in Path("/proc").iterdir():
        if path.name.isdigit():
            args = cmdline(int(path.name))
            if args:
                yield int(path.name), args


def v34_pids():
    return [
        pid for pid, args in processes()
        if Path(args[0]).name == "emulationstation"
    ]


def wrapper_pids():
    return [
        pid for pid, args in processes()
        if "/usr/bin/emulationstation-standalone" in args[:2]
    ]


def running(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def save(phase, **details):
    data = {
        "supervisor_pid": os.getpid(),
        "phase": phase,
        "updated_at": datetime.now().isoformat(),
        **details,
    }
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(temporary, STATE)
    log(f"STATUS: {phase} {details}")


def wait_for(test, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if test():
            return True
        time.sleep(0.5)
    return test()


def graphical_environment(pid):
    environment = os.environ.copy()

    try:
        raw = (Path("/proc") / str(pid) / "environ").read_bytes()
        original = dict(
            item.decode("utf-8", "replace").split("=", 1)
            for item in raw.split(b"\0")
            if b"=" in item
        )
    except OSError:
        original = {}

    for key in (
        "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
        "LANG", "LC_ALL", "PATH", "SDL_VIDEODRIVER",
        "DRI_PRIME",
    ):
        if key in original:
            environment[key] = original[key]

    environment["HOME"] = "/userdata/system"
    environment.setdefault("DISPLAY", ":0")
    environment.setdefault("XAUTHORITY", "//.Xauthority")
    environment.setdefault("XDG_RUNTIME_DIR", "/var/run")

    environment.pop("LD_TRACE_LOADED_OBJECTS", None)
    environment.pop("LD_LIBRARY_PATH", None)

    return environment


def frontend_pids():
    """The GCM frontend processes themselves (not anything they started)."""
    target = str(BIN)
    return [pid for pid, args in processes() if any(a == target for a in args)]


def terminate_test_session(child):
    # 61TG: stop only the GCM frontend and its dbus session wrapper. A reboot or power-off that
    # the frontend started runs in the same process group and must be left to finish (killing the
    # whole group stalled Batocera's reboot halfway).
    if child is None:
        return
    targets = set(frontend_pids())
    if child.poll() is None:
        targets.add(child.pid)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        log("GCM frontend did not exit in 10 s; stopping it")
    leftover = set(frontend_pids())
    if child.poll() is None:
        leftover.add(child.pid)
    for pid in leftover:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def restore(env, child):
    if SHUTDOWN.exists():
        save("system_shutdown")
        log("Service shutdown: stopping v3.6 without restarting v3.4")
        terminate_test_session(child)
        return True

    save("restoring_v34")

    terminate_test_session(child)

    # Re-enable the standard Batocera frontend restart mechanism.
    FLAG.touch()

    if wrapper_pids():
        log("Existing Batocera wrapper detected; waiting for v3.4")
        if wait_for(lambda: bool(v34_pids()), 75):
            save("v34_restored", v34_pids=v34_pids())
            return True

        # Do not create a second wrapper while one is active.
        if wrapper_pids():
            save("recovery_attention_required",
                 reason="Existing wrapper has not restarted v3.4")
            return False

    if v34_pids():
        # Do not launch a duplicate frontend.
        save("recovery_attention_required",
             reason="v3.4 running without its standard wrapper")
        return False

    log("Starting original Batocera frontend wrapper")

    with LOG.open("ab") as output:
        subprocess.Popen(
            ["/usr/bin/emulationstation-standalone"],
            cwd="/userdata",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    if wait_for(lambda: bool(v34_pids()), 90):
        save("v34_restored", v34_pids=v34_pids())
        return True

    save("recovery_attention_required",
         reason="Original frontend did not appear within 90 seconds")
    return False


def supervise():
    child = None
    env = None
    # 61TG: always leave a trace: who started us, and why we stop (signal, crash or normal end).
    log(f"Supervisor {os.getpid()} starting (parent {os.getppid()})")
    try:
        trace = LOG.open("a")
        faulthandler.enable(file=trace, all_threads=True)
    except OSError:
        pass

    def note_signal(number, _frame):
        log(f"Supervisor received signal {signal.Signals(number).name}")
        STOP.touch()

    for number in (signal.SIGHUP, signal.SIGQUIT):
        signal.signal(number, note_signal)

    with LOCK.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("ERROR: Another GCM graphical supervisor is active")
            return 1

        def request_stop(_signal, _frame):
            STOP.touch()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        try:
            if checksum("/usr/bin/emulationstation") != V34_SHA:
                raise RuntimeError("Live v3.4 checksum mismatch")

            if checksum(BIN) != V36_SHA:
                raise RuntimeError("Permanent v3.6 checksum mismatch")
            if not RUNTIME.is_dir():
                raise RuntimeError("GCM v3.6 runtime folder missing")

            for path in (
                "/tmp/shutdown.please",
                "/tmp/reboot.please",
                "/tmp/restart.please",
                "/tmp/suspend.please",
            ):
                if Path(path).exists():
                    raise RuntimeError("Pending system action: " + path)

            # During boot the standard Batocera frontend may
            # start and restart before initialization is complete.
            # Require a stable PID and wrapper for 12 seconds.
            old = []
            stable_pid = None
            stable_seconds = 0

            for waited in range(180):
                found = v34_pids()

                ready = (
                    len(found) == 1
                    and len(wrapper_pids()) == 1
                    and FLAG.exists()
                )

                if ready:
                    if found[0] == stable_pid:
                        stable_seconds += 1
                    else:
                        stable_pid = found[0]
                        stable_seconds = 1

                    if stable_seconds >= 12:
                        old = found
                        log("Original frontend stable for 12 seconds")
                        break
                else:
                    stable_pid = None
                    stable_seconds = 0

                if STOP.exists() or SHUTDOWN.exists():
                    log("Startup cancelled before frontend switch")
                    return 0

                if waited % 30 == 0:
                    log("Waiting for stable original frontend")

                time.sleep(1)

            if not old:
                raise RuntimeError(
                    "Original Batocera frontend did not stabilize"
                )

            env = graphical_environment(old[0])
            STOP.unlink(missing_ok=True)
            SHUTDOWN.unlink(missing_ok=True)

            save("switching_to_v36", original_pid=old[0])

            # Stop the automatic restart loop before ending v3.4.
            FLAG.unlink()
            os.kill(old[0], signal.SIGTERM)

            if not wait_for(
                lambda: not v34_pids() and not wrapper_pids(),
                50,
            ):
                raise RuntimeError(
                    "Original frontend/wrapper did not stop cleanly"
                )

            if STOP.exists():
                log("Startup cancelled before v3.6 started")
                return 0

            log("Launching GCM v3.6")
            env["GCM_V36_RUNTIME_DIR"] = str(RUNTIME)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["PYTHONNOUSERSITE"] = "1"

            with LOG.open("ab") as output:
                child = subprocess.Popen(
                    [
                        "dbus-run-session", "--",
                        str(BIN),
                        "--no-startup-game",
                        "--exit-on-reboot-required",
                        "--windowed",
                    ],
                    cwd="/userdata",
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

            if child.poll() is not None:
                raise RuntimeError("v3.6 exited during startup")

            save("v36_persistent_running",
                 deployment="persistent",
                 session_pid=child.pid)


            while True:
                if STOP.exists():
                    log("Manual test stop requested")
                    break

                if child.poll() is not None:
                    log(f"v3.6 exited with code {child.returncode}")
                    break

                time.sleep(1)


        except Exception as exc:
            log(f"ERROR: {type(exc).__name__}: {exc}")

        finally:
            if env is not None:
                try:
                    restore(env, child)
                except Exception as exc:
                    save("recovery_attention_required",
                         reason=f"{type(exc).__name__}: {exc}")

    return 0


def start():
    if STATE.exists():
        try:
            old = json.loads(STATE.read_text())
            pid = int(old.get("supervisor_pid", 0))
            if running(pid):
                raise SystemExit(
                    "ERROR: Graphical test supervisor already running"
                )
        except (ValueError, json.JSONDecodeError):
            pass

    with LOG.open("ab") as output:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "supervise"],
            cwd="/userdata",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=output,
            start_new_session=True,
        )

    print("GCM v3.6 permanent supervisor started")
    print("Supervisor PID:", child.pid)
    print("Mode: persistent (no timeout)")
    print("Log:", LOG)


def status():
    if not STATE.exists():
        print("No graphical test state found")
        return

    data = json.loads(STATE.read_text())
    print(json.dumps(data, indent=2))
    print("Supervisor alive:",
          running(int(data.get("supervisor_pid", 0))))
    print("Current v3.4 PIDs:", v34_pids())


def stop():
    if not STATE.exists():
        print("No graphical test found")
        return

    data = json.loads(STATE.read_text())
    if not running(int(data.get("supervisor_pid", 0))):
        print("Supervisor not running; no stop request sent")
        return

    STOP.touch()
    print("Stop requested; supervisor will restore v3.4")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "start":
        start()
    elif action == "supervise":
        try:
            code = supervise()
        except BaseException as exc:
            log(f"Supervisor stopped by {type(exc).__name__}: {exc}")
            raise
        log(f"Supervisor {os.getpid()} finished")
        sys.exit(code)
    elif action == "status":
        status()
    elif action == "stop":
        stop()
    else:
        raise SystemExit("Usage: script.py start|status|stop")
