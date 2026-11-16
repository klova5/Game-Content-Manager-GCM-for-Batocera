"""GCM v3.6 isolated supervisor rehearsal.

Tests process exit monitoring and lifecycle decisions.
Live EmulationStation launch is deliberately unavailable.
"""

import subprocess
import sys
import time

from gcm36_frontend_exit_policy import decide_exit

POWER = {"shutdown", "reboot", "service_shutdown"}
STOP = {"stop", "service_cancel"}


class SessionRehearsal:
    def __init__(
        self,
        launcher,
        marker_reader,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.launcher = launcher
        self.marker_reader = marker_reader
        self.clock = clock
        self.sleep = sleep

        self.history = []
        self.streak = 0

    def finish(self, action, reason):
        return {
            "action": action,
            "reason": reason,
            "launches": len(self.history),
            "history": self.history,
        }

    def run(self, max_launches=6):
        for _ in range(max_launches):

            markers = set(self.marker_reader())

            if markers & POWER:
                return self.finish(
                    "LEAVE_FOR_SHUTDOWN",
                    "PRE_LAUNCH_POWER_REQUEST",
                )

            if markers & STOP:
                return self.finish(
                    "RESTORE_ORIGINAL",
                    "PRE_LAUNCH_STOP_REQUEST",
                )

            started = self.clock()

            try:
                child = self.launcher()
            except Exception as exc:
                return self.finish(
                    "RESTORE_ORIGINAL",
                    "LAUNCH_FAILED:" + type(exc).__name__,
                )

            while True:
                code = child.poll()
                markers = set(self.marker_reader())

                if code is not None:
                    child.wait()
                    break

                if markers & (POWER | STOP):
                    child.terminate()

                    try:
                        code = child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        code = child.wait(timeout=3)

                    break

                self.sleep(0.01)

            elapsed = max(
                0,
                self.clock() - started,
            )

            # Recheck markers after the child exits.
            # A shutdown could arrive at the same moment.
            markers = set(self.marker_reader())

            decision = decide_exit(
                code,
                elapsed,
                self.streak,
                markers,
            )

            self.streak = decision[
                "consecutive_rapid_exits"
            ]

            self.history.append({
                "exit_code": code,
                "uptime_seconds": round(elapsed, 3),
                "action": decision["action"],
                "reason": decision["reason"],
                "rapid_exit_streak": self.streak,
            })

            if decision["action"] != "RELAUNCH_GCM":
                return self.finish(
                    decision["action"],
                    decision["reason"],
                )

            self.sleep(
                decision["restart_delay_seconds"]
            )

        # Rehearsal-only session budget.
        return self.finish(
            "RESTORE_ORIGINAL",
            "REHEARSAL_SESSION_LIMIT",
        )


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeChild:
    def __init__(self, clock, code, uptime):
        self.clock = clock
        self.code = code
        self.uptime = uptime
        self.used = False

    def poll(self):
        if not self.used:
            self.clock.now += self.uptime
            self.used = True

        return self.code

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.code = -15

    def kill(self):
        self.code = -9


class FakeLauncher:
    def __init__(self, clock, outcomes):
        self.clock = clock
        self.outcomes = list(outcomes)
        self.count = 0

    def __call__(self):
        if self.count >= len(self.outcomes):
            raise RuntimeError(
                "No additional test outcomes"
            )

        code, uptime = self.outcomes[self.count]
        self.count += 1

        return FakeChild(
            self.clock,
            code,
            uptime,
        )


def test(
    name,
    outcomes,
    markers,
    expected_action,
    expected_launches,
):
    clock = FakeClock()
    launcher = FakeLauncher(clock, outcomes)

    rehearsal = SessionRehearsal(
        launcher,
        lambda: markers(launcher),
        clock,
        lambda seconds: None,
    )

    result = rehearsal.run()

    assert result["action"] == expected_action, (
        name,
        result,
    )

    assert result["launches"] == expected_launches, (
        name,
        result,
    )

    print("PASS:", name)
    print(
        " Action:", result["action"],
        "| Launches:", result["launches"],
    )

    return result


def self_test():
    print(
        "===== ISOLATED SUPERVISOR TESTS ====="
    )

    rapid = test(
        "Three rapid exits trigger safe fallback",
        [(0, 2), (0, 3), (0, 2)],
        lambda launcher: set(),
        "RESTORE_ORIGINAL",
        3,
    )

    assert [
        row["reason"]
        for row in rapid["history"]
    ] == [
        "CLEAN_FRONTEND_EXIT",
        "CLEAN_FRONTEND_EXIT",
        "RAPID_EXIT_LIMIT_REACHED",
    ]

    test(
        "Stable session resets rapid-exit count",
        [
            (1, 2),
            (0, 40),
            (1, 2),
            (1, 2),
            (1, 2),
        ],
        lambda launcher: set(),
        "RESTORE_ORIGINAL",
        5,
    )

    test(
        "Shutdown prevents initial launch",
        [(0, 10)],
        lambda launcher: {"shutdown"},
        "LEAVE_FOR_SHUTDOWN",
        0,
    )

    test(
        "Manual stop prevents initial launch",
        [(0, 10)],
        lambda launcher: {"stop"},
        "RESTORE_ORIGINAL",
        0,
    )

    test(
        "Reboot after exit prevents relaunch",
        [(0, 50)],
        lambda launcher: (
            {"reboot"} if launcher.count else set()
        ),
        "LEAVE_FOR_SHUTDOWN",
        1,
    )

    test(
        "Manual stop after exit selects fallback",
        [(0, 50)],
        lambda launcher: (
            {"stop"} if launcher.count else set()
        ),
        "RESTORE_ORIGINAL",
        1,
    )

    test(
        "Requested restart relaunches frontend",
        [
            (0, 45),
            (0, 1),
            (0, 1),
            (0, 1),
        ],
        lambda launcher: (
            {"restart"}
            if launcher.count == 1
            else set()
        ),
        "RESTORE_ORIGINAL",
        4,
    )

    print()
    print(
        "===== REAL DUMMY PROCESS TEST ====="
    )

    def launch_dummy():
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.exit(0)",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    real = SessionRehearsal(
        launch_dummy,
        lambda: set(),
        sleep=lambda seconds: time.sleep(
            min(seconds, 0.01)
        ),
    )

    result = real.run(max_launches=4)

    assert result["action"] == "RESTORE_ORIGINAL"
    assert result["reason"] == "RAPID_EXIT_LIMIT_REACHED"
    assert result["launches"] == 3

    assert all(
        row["exit_code"] == 0
        for row in result["history"]
    )

    print(
        "PASS: Three dummy processes launched"
    )
    print(
        "PASS: Process exits detected and reaped"
    )
    print(
        "PASS: Relaunch limit enforced"
    )
    print(
        "PASS: Terminal decision returned safely"
    )

    print()
    print("===== REHEARSAL SAFETY =====")
    print("Batocera frontend launched: NO")
    print("Production supervisor modified: NO")
    print("Original frontend restored: NO")
    print("GCM installation attempted: NO")
    print(
        "Live mode: DISABLED"
    )


if __name__ == "__main__":
    if sys.argv[1:] != ["--self-test"]:
        raise SystemExit(
            "Rehearsal only: use --self-test"
        )

    self_test()
