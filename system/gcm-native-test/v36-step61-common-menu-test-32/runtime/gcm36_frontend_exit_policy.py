"""GCM v3.6 frontend lifecycle policy.

Pure decision logic only. No process launching, termination,
filesystem modification or service management.
"""

RAPID_EXIT_SECONDS = 30
MAX_CONSECUTIVE_RAPID_EXITS = 3

VALID_MARKERS = frozenset({
    "stop",
    "service_cancel",
    "service_shutdown",
    "shutdown",
    "reboot",
    "restart",
})


def decide_exit(
    exit_code,
    uptime_seconds,
    rapid_exits_before=0,
    markers=(),
):
    """Classify frontend exit and return a safe supervisor action."""

    flags = frozenset(markers)

    if flags - VALID_MARKERS:
        raise ValueError("Unknown lifecycle marker")

    if uptime_seconds < 0 or rapid_exits_before < 0:
        raise ValueError("Invalid lifecycle counters")

    rapid = uptime_seconds < RAPID_EXIT_SECONDS

    streak = (
        rapid_exits_before + 1
        if rapid
        else 0
    )

    def result(action, reason, delay=0):
        return {
            "action": action,
            "reason": reason,
            "exit_code": exit_code,
            "uptime_seconds": uptime_seconds,
            "consecutive_rapid_exits": streak,
            "restart_delay_seconds": delay,
            "restore_original": (
                action == "RESTORE_ORIGINAL"
            ),
        }

    # Power operations must not launch a replacement frontend.
    if flags & {"service_shutdown", "shutdown", "reboot"}:
        return result(
            "LEAVE_FOR_SHUTDOWN",
            "SYSTEM_POWER_OPERATION"
        )

    # An explicit service/manual stop restores the safe frontend.
    if flags & {"stop", "service_cancel"}:
        return result(
            "RESTORE_ORIGINAL",
            "EXPLICIT_STOP"
        )

    # Prevent repeated rapid exits from creating a restart loop.
    if streak >= MAX_CONSECUTIVE_RAPID_EXITS:
        return result(
            "RESTORE_ORIGINAL",
            "RAPID_EXIT_LIMIT_REACHED"
        )

    if "restart" in flags:
        return result(
            "RELAUNCH_GCM",
            "FRONTEND_RESTART_REQUEST",
            delay=2
        )

    if exit_code == 0:
        return result(
            "RELAUNCH_GCM",
            "CLEAN_FRONTEND_EXIT",
            delay=2
        )

    return result(
        "RELAUNCH_GCM",
        "UNEXPECTED_FRONTEND_EXIT",
        delay=5
    )
