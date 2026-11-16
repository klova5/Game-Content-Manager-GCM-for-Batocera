"""GCM v3.6 isolated supervisor process-safety module.

Provides process locking, marker inspection, and process-group
cleanup. Self-tests launch only temporary Python dummy processes.

No live EmulationStation functionality is exposed.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

from pathlib import Path

from gcm36_frontend_exit_policy import decide_exit

BASE = Path(__file__).resolve().parent
REPORT = BASE / "gcm36-supervisor-safety-test.json"


class SupervisorLock:
    def __init__(self, path):
        self.path = Path(path)
        self.fd = None

    def acquire(self):
        self.fd = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
            0o600,
        )

        try:
            fcntl.flock(
                self.fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def release(self):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def read_markers(mapping):
    return {
        name
        for name, path in mapping.items()
        if Path(path).exists()
    }


def process_is_live(pid):
    try:
        stat = Path(
            f"/proc/{pid}/stat"
        ).read_text()
    except (FileNotFoundError, ProcessLookupError):
        return False

    # A zombie process has stopped executing, even if it has
    # not yet been reaped by its parent.
    state = stat.rsplit(") ", 1)[1].split()[0]

    return state != "Z"


def terminate_session_group(process, pgid):
    """Terminate the isolated session, including descendants."""

    if (
        pgid <= 1
        or pgid != process.pid
        or pgid == os.getpgrp()
    ):
        raise RuntimeError(
            "Refusing unsafe process-group termination"
        )

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass

    # A terminated session leader may have surviving children.
    # Escalate for anything remaining in its isolated group.
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass

    if process.poll() is None:
        process.wait(timeout=3)


def wait_for_file(path, timeout=5):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return True
        time.sleep(0.02)

    return False


DUMMY_SESSION = r"""
import subprocess
import sys
import time

grandchild = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

with open(sys.argv[1], "w") as stream:
    stream.write(str(grandchild.pid))
    stream.flush()

time.sleep(60)
"""


def test_lock(root):
    lockpath = root / "supervisor.lock"

    print("===== SINGLE-INSTANCE LOCK =====")

    with SupervisorLock(lockpath):
        check = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--probe-lock",
                str(lockpath),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert check.returncode == 73, (
            "Second supervisor unexpectedly acquired lock"
        )

        print("PASS: First supervisor owns lock")
        print("PASS: Concurrent supervisor rejected")

    check = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--probe-lock",
            str(lockpath),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert check.returncode == 0

    print("PASS: Lock released after supervisor exits")


def test_markers(root):
    print()
    print("===== LIFECYCLE MARKERS =====")

    mapping = {
        name: root / name
        for name in (
            "restart",
            "shutdown",
            "reboot",
            "stop",
            "service_shutdown",
        )
    }

    assert read_markers(mapping) == set()

    mapping["restart"].touch()

    assert read_markers(mapping) == {"restart"}

    decision = decide_exit(
        0,
        100,
        markers=read_markers(mapping),
    )

    assert decision["action"] == "RELAUNCH_GCM"

    print("PASS: Restart request detected")

    mapping["shutdown"].touch()

    decision = decide_exit(
        0,
        100,
        markers=read_markers(mapping),
    )

    assert decision["action"] == "LEAVE_FOR_SHUTDOWN"

    print("PASS: Shutdown overrides restart")

    mapping["shutdown"].unlink()
    mapping["restart"].unlink()
    mapping["stop"].touch()

    decision = decide_exit(
        0,
        100,
        markers=read_markers(mapping),
    )

    assert decision["action"] == "RESTORE_ORIGINAL"

    print("PASS: Manual stop selects safe fallback")


def test_process_group(root):
    print()
    print("===== ISOLATED PROCESS GROUP =====")

    child_pid_file = root / "dummy-child.pid"

    parent = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            DUMMY_SESSION,
            str(child_pid_file),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    pgid = parent.pid
    child_pid = None

    try:
        assert wait_for_file(child_pid_file), (
            "Dummy session did not create child"
        )

        child_pid = int(
            child_pid_file.read_text().strip()
        )

        assert parent.poll() is None
        assert process_is_live(child_pid)

        assert os.getpgid(parent.pid) == pgid
        assert os.getpgid(child_pid) == pgid

        print("PASS: Dummy session launched")
        print("PASS: Parent and child share isolated group")

    finally:
        terminate_session_group(parent, pgid)

    deadline = time.monotonic() + 5

    while (
        child_pid is not None
        and process_is_live(child_pid)
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)

    assert parent.poll() is not None
    assert child_pid is not None
    assert not process_is_live(child_pid), (
        "Dummy child survived group cleanup"
    )

    print("PASS: Dummy parent terminated")
    print("PASS: Dummy child terminated")
    print("PASS: No active dummy process left behind")


def self_test():
    print("===== GCM V3.6 SUPERVISOR SAFETY =====")

    with tempfile.TemporaryDirectory(
        prefix="gcm36-step113-"
    ) as directory:
        root = Path(directory)

        test_lock(root)
        test_markers(root)
        test_process_group(root)

    report = {
        "schema_version": 1,
        "project": "GCM v3.6",
        "mode": "isolated_self_test",
        "single_instance_lock": "PASS",
        "lock_release": "PASS",
        "marker_priority": "PASS",
        "isolated_process_group": "PASS",
        "descendant_cleanup": "PASS",
        "real_frontend_started": False,
        "production_supervisor_modified": False,
        "installation_enabled": False,
    }

    with REPORT.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    print()
    print("===== FINAL SAFETY RESULTS =====")
    print("Single-instance locking: PASS")
    print("Restart/shutdown marker handling: PASS")
    print("Complete session cleanup: PASS")
    print("Real EmulationStation launched: NO")
    print("Production supervisor modified: NO")
    print("Installation enabled: NO")
    print("Report:", REPORT)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--probe-lock":
        try:
            with SupervisorLock(sys.argv[2]):
                return 0
        except BlockingIOError:
            return 73

    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0

    raise SystemExit(
        "Only --self-test and --probe-lock are supported"
    )


if __name__ == "__main__":
    sys.exit(main())
