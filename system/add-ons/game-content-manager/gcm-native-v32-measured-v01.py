#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).with_name("gamecontentctl-measured-v01.py")
SUPPORTED = ("ryujinx", "ryujinx-emu")


def clean(value, limit=110):
    return " ".join(str(value or "").split())[:limit]


def run_backend(*args):
    result = subprocess.run(
        [sys.executable, str(BACKEND), *args],
        capture_output=True,
        text=True,
        timeout=45,
    )

    if result.returncode:
        raise RuntimeError(
            clean(result.stdout or result.stderr, 300)
        )

    data = json.loads(result.stdout)

    if data.get("error"):
        raise RuntimeError(data["error"])

    return data


def status(rom):
    return run_backend(
        "status",
        "--system", "switch",
        "--rom", rom,
        "--emulator", "ryujinx",
    )


def make_token(path):
    return str(path).encode("utf-8").hex()


def read_token(token):
    path = bytes.fromhex(token).decode("utf-8")

    if not path or "\x00" in path:
        raise ValueError("Invalid package identifier")

    return path


def matching_row(rows, content_type, path):
    for row in rows:
        if (
            row.get("content_type") == content_type
            and row.get("path") == path
        ):
            return row

    raise ValueError(
        "Selected package is not registered in this game's inventory"
    )



def is_registered_rom_container(row, rom):
    """True only when Ryujinx's update source is the game ROM itself."""
    if not row or not row.get("installed") or not row.get("path"):
        return False

    try:
        return (
            Path(row["path"]).resolve(strict=True)
            == Path(rom).resolve(strict=True)
        )
    except (OSError, RuntimeError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", required=True)
    ap.add_argument("--emulator", required=True)
    ap.add_argument(
        "--mode",
        choices=["summary", "list", "apply", "toggle", "bulk"],
        required=True,
    )
    ap.add_argument("--type", choices=["update", "dlc"])
    ap.add_argument("--token")
    ap.add_argument("--state", choices=["enable", "disable"])
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    if args.emulator not in SUPPORTED:
        if args.mode == "summary":
            print("SUMMARY\tUNSUPPORTED\tUNSUPPORTED")
            return 0

        raise ValueError(
            "Emulator not supported: " + args.emulator
        )

    if args.mode in ("apply", "toggle", "bulk") and not args.confirm:
        raise PermissionError(
            "Native confirmation is required before changing content"
        )

    if args.mode in ("apply", "toggle") and not args.token:
        raise ValueError("No package selected")

    if args.mode == "apply" and args.type != "update":
        raise ValueError("Apply is only valid for updates")

    if args.mode == "toggle" and (
        args.type != "dlc" or not args.state
    ):
        raise ValueError("DLC toggle requires a target state")

    if args.mode == "bulk" and (
        args.type != "dlc" or not args.state
    ):
        raise ValueError(
            "Bulk changes require DLC and an enable/disable state"
        )

    data = status(args.rom)
    rows = data.get("rows", [])

    if args.mode == "summary":
        active = next(
            (
                row for row in rows
                if row.get("content_type") == "update"
                and row.get("active")
            ),
            None,
        )

        version = (
            "v" + clean(active.get("version") or "UNKNOWN", 32)
            if active else "NONE"
        )

        if is_registered_rom_container(active, args.rom):
            version = 'REGISTERED CONTAINER - UNVERIFIED'

        enabled = sum(
            1 for row in rows
            if row.get("content_type") == "dlc"
            and row.get(
                "enabled",
                row.get("active", False),
            )
        )

        print(f"SUMMARY\t{version}\t{enabled} ENABLED")
        return 0

    if args.mode == "list":
        if not args.type:
            raise ValueError("Content type is required")

        for row in rows:
            if row.get("content_type") != args.type:
                continue

            section = (
                "AVAILABLE"
                if row.get("installed")
                else "DETECTED"
            )

            if args.type == "update":
                label = "v" + clean(
                    row.get("version") or "UNKNOWN", 40
                )
                if is_registered_rom_container(row, args.rom):
                    label = "REGISTERED GAME CONTAINER - UNVERIFIED"
                state = (
                    "ACTIVE" if row.get("active")
                    else "INSTALLED"
                )
            else:
                label = clean(
                    Path(row.get("path") or "UNKNOWN")
                    .stem.replace(".", " "),
                    95,
                )
                state = (
                    "ENABLED"
                    if row.get(
                        "enabled",
                        row.get("active", False),
                    )
                    else "DISABLED"
                )

            print(
                "ROW\t{}\t{}\t{}\t{}".format(
                    section,
                    state,
                    label,
                    make_token(row.get("path") or ""),
                )
            )

        return 0

    if args.mode == "bulk":
        installed = [
            row for row in rows
            if row.get("content_type") == "dlc"
            and row.get("installed")
        ]

        paths = []
        seen = set()

        for row in installed:
            path = str(row.get("path") or "")

            if not path:
                raise ValueError("Installed DLC has no file path")

            normalized = os.path.realpath(path)

            if normalized not in seen:
                seen.add(normalized)
                paths.append(path)

        if not paths:
            print("OK\\tNO INSTALLED DLC")
            return 0

        desired = args.state == "enable"

        run_backend(
            "dlc-state",
            *paths,
            "--rom", args.rom,
            "--enable" if desired else "--disable",
            "--confirm",
        )

        fresh_rows = status(args.rom).get("rows", [])

        for path in paths:
            row = matching_row(fresh_rows, "dlc", path)

            actual = bool(
                row.get(
                    "enabled",
                    row.get("active", False),
                )
            )

            if actual != desired:
                raise RuntimeError(
                    "Bulk DLC verification failed: " + path
                )

        print(
            "OK\\tVERIFIED {} DLC PACKAGES".format(
                len(paths)
            )
        )
        return 0

    path = read_token(args.token)
    row = matching_row(rows, args.type, path)

    if not row.get("installed"):
        raise ValueError(
            "External packages must be transferred and installed first"
        )

    if args.mode == "apply":
        if row.get("active"):
            print("OK\tALREADY ACTIVE")
            return 0

        if row.get("action") != "apply":
            raise ValueError(
                "This update is not ready for direct activation"
            )

        run_backend(
            "apply", path,
            "--emulator", "ryujinx",
            "--rom", args.rom,
            "--confirm",
        )

    elif args.mode == "toggle":
        run_backend(
            "dlc-state", path,
            "--rom", args.rom,
            "--" + args.state,
            "--confirm",
        )

    fresh_rows = status(args.rom).get("rows", [])
    fresh = matching_row(fresh_rows, args.type, path)

    if args.mode == "apply" and not fresh.get("active"):
        raise RuntimeError(
            "Update activation could not be verified"
        )

    if args.mode == "toggle":
        expected = args.state == "enable"
        actual = bool(
            fresh.get(
                "enabled",
                fresh.get("active", False),
            )
        )

        if actual != expected:
            raise RuntimeError(
                "DLC state change could not be verified"
            )

    print("OK\tCONTENT STATE VERIFIED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + clean(exc, 300))
        sys.exit(1)
