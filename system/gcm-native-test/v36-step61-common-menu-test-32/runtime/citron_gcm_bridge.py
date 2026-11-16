#!/usr/bin/env python3
"""Citron NAND inventory protocol for GCM v3.6.

Read-only. Counts global NAND registration entries only.
No game identity, installation, or active-status verification.
"""

import argparse
import tempfile
from pathlib import Path

from citron_inventory import DEFAULT_ROOT, inventory


def format_record(result):
    if result["emulator"] != "citron-emu":
        raise ValueError("Unexpected emulator")

    if result["mode"] != "read_only":
        raise ValueError("Read-only requirement violated")

    if result["installation_enabled"] is not False:
        raise ValueError("Installation must remain disabled")

    fields = (
        "registration_directories",
        "nca_files",
        "skipped_symlinks",
    )

    counts = []

    for field in fields:
        value = result[field]

        if type(value) is not int or value < 0:
            raise ValueError("Invalid inventory count")

        counts.append(str(value))

    return "CITRON_NAND\t" + "\t".join(counts)


def self_test():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "registered"

        first = root / "00000001"
        second = root / "00000002"

        first.mkdir(parents=True)
        second.mkdir()

        (first / "one.nca").touch()
        (second / "two.NCA").touch()

        (first / "linked.nca").symlink_to(
            second / "two.NCA"
        )

        result = inventory(root)
        record = format_record(result)

        assert record == "CITRON_NAND\t2\t2\t1"

    print("PASS: Citron GCM bridge self-test")
    print("PASS: Native inventory protocol verified")
    print("PASS: Symbolic links excluded")
    print("PASS: Installation remains disabled")
    print("PASS: No game-specific identity claims")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT
    )

    parser.add_argument(
        "--self-test",
        action="store_true"
    )

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    result = inventory(args.root)
    print(format_record(result))


if __name__ == "__main__":
    main()
