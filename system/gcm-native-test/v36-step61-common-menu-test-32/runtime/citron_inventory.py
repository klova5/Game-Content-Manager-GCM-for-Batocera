#!/usr/bin/env python3
"""GCM v3.6 Citron NAND inventory: read-only, identity unverified."""

import argparse
import json
import os
import tempfile
from pathlib import Path

DEFAULT_ROOT = Path(
    "/userdata/system/configs/citron/"
    "nand/user/Contents/registered"
)


def inventory(root):
    if not root.is_dir():
        raise FileNotFoundError(f"Citron registration directory missing: {root}")

    top_entries = 0
    registration_dirs = 0
    nca_files = 0
    skipped_symlinks = 0

    with os.scandir(root) as entries:
        for entry in entries:
            top_entries += 1

            if entry.is_symlink():
                skipped_symlinks += 1
                continue

            if not entry.is_dir(follow_symlinks=False):
                continue

            registration_dirs += 1

            for directory, subdirs, files in os.walk(
                entry.path, followlinks=False
            ):
                subdirs[:] = [
                    name for name in subdirs
                    if not (Path(directory) / name).is_symlink()
                ]

                for name in files:
                    candidate = Path(directory) / name

                    if candidate.is_symlink():
                        skipped_symlinks += 1
                    elif name.lower().endswith(".nca"):
                        nca_files += 1

    return {
        "emulator": "citron-emu",
        "mode": "read_only",
        "identity": "UNVERIFIED",
        "top_level_entries": top_entries,
        "registration_directories": registration_dirs,
        "nca_files": nca_files,
        "skipped_symlinks": skipped_symlinks,
        "update_status": "UNKNOWN",
        "dlc_status": "UNKNOWN",
        "active_status": "UNKNOWN",
        "installation_enabled": False,
    }


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "registered"
        first = root / "00000001"
        second = root / "00000002"

        first.mkdir(parents=True)
        second.mkdir()

        (first / "one.nca").touch()
        (second / "two.NCA").touch()
        (first / "notes.txt").touch()
        (root / "directory-link").symlink_to(
            first, target_is_directory=True
        )

        result = inventory(root)

        assert result["top_level_entries"] == 3
        assert result["registration_directories"] == 2
        assert result["nca_files"] == 2
        assert result["skipped_symlinks"] == 1
        assert result["installation_enabled"] is False
        assert result["update_status"] == "UNKNOWN"
        assert result["dlc_status"] == "UNKNOWN"

    print("PASS: Citron inventory self-test")
    print("PASS: Registration directories counted")
    print("PASS: NCA filenames counted without reading content")
    print("PASS: Symbolic links excluded")
    print("PASS: Installation disabled and status unverified")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    print(json.dumps(inventory(args.root), sort_keys=True))


if __name__ == "__main__":
    main()
