#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
V32 = ROOT / "gcm-native-v32-measured-v01.py"
CTL = ROOT / "gamecontentctl-measured-v01.py"
SNAPSHOTS = Path(
    "/userdata/system/gcm-native-test/"
    "v33-development/transactions"
)


def command(*args):
    p = subprocess.run(
        [sys.executable, *map(str, args)],
        text=True, capture_output=True,
    )
    if p.returncode:
        raise RuntimeError(
            (p.stderr or p.stdout).strip()[:1000]
            or "Backend operation failed"
        )
    return p.stdout


def inventory(rom, emulator):
    output = command(
        V32, "--rom", rom,
        "--emulator", emulator,
        "--mode", "list", "--type", "dlc",
    )
    result = []
    for line in output.splitlines():
        fields = line.split("\t")
        if fields[0] == "ERROR":
            raise RuntimeError(line)
        if fields[0] != "ROW":
            continue
        if len(fields) != 5:
            raise ValueError("Invalid inventory record")
        _, section, state, label, token = fields
        if section not in ("AVAILABLE", "DETECTED"):
            raise ValueError("Invalid inventory section")
        try:
            path = bytes.fromhex(token).decode("utf-8")
        except (ValueError, UnicodeError):
            raise ValueError("Invalid inventory package token")
        result.append(dict(
            section=section, state=state,
            label=label, token=token.lower(),
            path=path,
        ))
    tokens = [r["token"] for r in result]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate inventory tokens")
    return result


def selected(raw, allowed):
    if raw is None:
        raise ValueError("Explicit selections required")
    values = (
        set() if not raw
        else {x.strip().lower() for x in raw.split(",")}
    )
    if "" in values or not values.issubset(allowed):
        raise ValueError("Unknown or invalid selected package")
    return values


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(
            lambda: stream.read(4 * 1024 * 1024), b""
        ):
            h.update(block)
    return h.hexdigest()


def managed_copy(managed, expected_hash):
    folder = managed / "dlc"
    if not folder.is_dir():
        return None
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() in (
            ".nsp", ".nsz", ".xci", ".xcz"
        ):
            if digest(p) == expected_hash:
                return p
    return None


def restore_configuration(snapshot, config):
    temp = config.with_name(
        config.name + ".v33-restore-" + uuid.uuid4().hex
    )
    shutil.copy2(snapshot, temp)
    os.replace(temp, config)



def locate_game_directory(parent, title_id):
    """Find the existing directory without changing its case."""
    target = title_id.casefold()
    matches = []

    if parent.is_dir():
        matches = [
            item for item in parent.iterdir()
            if item.is_dir()
            and item.name.casefold() == target
        ]

    if len(matches) > 1:
        raise RuntimeError(
            "Ambiguous game directories for " + title_id
        )

    if matches:
        return matches[0]

    return parent / target


def apply(args):
    if not args.confirm:
        raise ValueError(
            "Native confirmation is required before changing content"
        )
    if args.type != "dlc":
        raise ValueError("Unified Apply currently supports DLC only")
    if args.emulator not in ("ryujinx", "ryujinx-emu"):
        raise ValueError("Unsupported emulator")

    initial = inventory(args.rom, args.emulator)
    available = {
        row["token"]: row for row in initial
        if row["section"] == "AVAILABLE"
    }
    detected = {
        row["token"]: row for row in initial
        if row["section"] == "DETECTED"
    }

    keep = selected(args.selected_available, set(available))
    import_tokens = selected(
        args.selected_detected, set(detected)
    )

    if not re.fullmatch(
        r"[0-9a-fA-F]{16}", args.rom_title_id
    ):
        raise ValueError("Valid game title ID required")

    # Guard against applying another game's files.
    rom_name = Path(args.rom).name.upper()
    title_id = args.rom_title_id.upper()
    if "[" + title_id + "]" not in rom_name:
        raise ValueError(
            "Game title ID does not match the selected ROM"
        )

    data_root = Path(os.environ.get(
        "GCM_DATA_ROOT",
        "/userdata/system/game-content"
    ))

    ryujinx_root = Path(os.environ.get(
        "GCM_RYUJINX_ROOT",
        "/userdata/system/configs/Ryujinx"
    ))

    managed = locate_game_directory(
        data_root / "switch", title_id
    )

    game_dir = locate_game_directory(
        ryujinx_root / "games", title_id
    )

    config = game_dir / "dlc.json"

    if config.exists() and not config.is_file():
        raise RuntimeError(
            "Ryujinx DLC configuration is not a regular file"
        )

    config_existed = config.is_file()

    if not config_existed and available:
        raise RuntimeError(
            "DLC inventory reports installed packages, "
            "but the actual Ryujinx dlc.json is missing"
        )

    if not config_existed and not import_tokens:
        print("OK\tNO DLC CHANGES")
        return 0

    # Validate all sources before performing a write.
    imported = []
    source_hashes = set()
    for token in sorted(import_tokens):
        source = Path(detected[token]["path"])
        if not source.is_file():
            raise FileNotFoundError(str(source))
        sha = digest(source)
        if sha in source_hashes:
            raise ValueError(
                "Duplicate detected package content selected"
            )
        source_hashes.add(sha)
        imported.append((source, sha))

    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOTS / (
        title_id + "-" + uuid.uuid4().hex + "-dlc.json"
    )
    if config_existed:
        shutil.copy2(config, snapshot)

    operation_started = False
    try:
        installed_paths = set()

        for source, source_hash in imported:
            copy = managed_copy(managed, source_hash)

            if copy is None:
                operation_started = True
                command(
                    CTL, "transfer", source,
                    "--system", "switch",
                    "--rom", args.rom,
                    "--type", "dlc",
                )
                copy = managed_copy(managed, source_hash)

            if copy is None:
                raise RuntimeError(
                    "No SHA256-verified managed copy for "
                    + source.name
                )

            if not source.is_file() or digest(source) != source_hash:
                raise RuntimeError(
                    "External source changed during import: "
                    + str(source)
                )

            operation_started = True
            command(
                CTL, "install", copy,
                "--emulator", "ryujinx",
                "--rom", args.rom, "--confirm",
            )
            installed_paths.add(str(copy.resolve()))

        fresh = inventory(args.rom, args.emulator)
        registered = [
            row for row in fresh
            if row["section"] == "AVAILABLE"
        ]
        registered_paths = {
            str(Path(row["path"]).resolve())
            for row in registered
        }

        if not installed_paths.issubset(registered_paths):
            raise RuntimeError(
                "Imported DLC was not registered by Ryujinx"
            )

        wanted_paths = {
            str(Path(available[token]["path"]).resolve())
            for token in keep
        } | installed_paths

        all_paths = {
            str(Path(row["path"]).resolve())
            for row in registered
        }

        if not wanted_paths.issubset(all_paths):
            raise RuntimeError(
                "An available DLC package disappeared"
            )

        enable = sorted(wanted_paths)
        disable = sorted(all_paths - wanted_paths)

        if enable:
            operation_started = True
            command(
                CTL, "dlc-state", *enable,
                "--rom", args.rom,
                "--enable", "--confirm",
            )

        if disable:
            operation_started = True
            command(
                CTL, "dlc-state", *disable,
                "--rom", args.rom,
                "--disable", "--confirm",
            )

        final = inventory(args.rom, args.emulator)
        final_available = [
            row for row in final
            if row["section"] == "AVAILABLE"
        ]

        final_states = {
            str(Path(row["path"]).resolve()):
                row["state"] == "ENABLED"
            for row in final_available
        }

        if set(final_states) != all_paths:
            raise RuntimeError(
                "Final DLC inventory changed unexpectedly"
            )

        for path, state in final_states.items():
            if state != (path in wanted_paths):
                raise RuntimeError(
                    "Final DLC enabled-state verification failed"
                )

        print(
            "OK\tVERIFIED ENABLE={} DISABLE={} IMPORT={}".format(
                len(wanted_paths),
                len(all_paths - wanted_paths),
                len(imported),
            )
        )
        return 0

    except Exception as exc:
        if operation_started:
            try:
                if config_existed:
                    restore_configuration(snapshot, config)
                elif config.exists():
                    config.unlink()

                raise RuntimeError(
                    str(exc)
                    + (
                        "; DLC configuration restored from snapshot "
                        + str(snapshot)
                        if config_existed else
                        "; initial DLC configuration state restored"
                    )
                    + ". Managed copies or index entries may remain."
                ) from exc
            except RuntimeError:
                raise
            except Exception as restore_error:
                raise RuntimeError(
                    str(exc)
                    + "; CONFIGURATION RESTORE FAILED: "
                    + str(restore_error)
                    + "; snapshot: "
                    + (
                        str(snapshot)
                        if config_existed else
                        "none (initial configuration absent)"
                    )
                ) from exc
        raise


def main():
    # Preserve every working v3.2 operation, including Updates.
    if "--mode" not in sys.argv:
        raise ValueError("Missing operation mode")

    index = sys.argv.index("--mode")
    if index + 1 >= len(sys.argv):
        raise ValueError("Missing operation mode value")

    if sys.argv[index + 1] != "commit":
        result = subprocess.run(
            [sys.executable, str(V32), *sys.argv[1:]],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(
                (result.stderr or result.stdout).strip()
                or "Existing operation failed"
            )
        sys.stdout.write(result.stdout)
        return 0

    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--mode", choices=["commit"], required=True)
    parser.add_argument("--type", required=True)
    parser.add_argument("--rom-title-id", required=True)
    parser.add_argument("--selected-available", required=True)
    parser.add_argument("--selected-detected", required=True)
    parser.add_argument("--confirm", action="store_true")
    return apply(parser.parse_args())


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print("ERROR\t" + str(error).replace("\n", " "))
        sys.exit(1)
