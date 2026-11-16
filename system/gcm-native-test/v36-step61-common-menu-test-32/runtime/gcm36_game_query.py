"""GCM v3.6 read-only game-to-content query bridge.

Development API for native EmulationStation integration.
No installation, activation, copying, or deletion operations.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from adapters.rpcs3 import read_sfo
from shadps4_metadata_matcher import parse_sfo, identity

BASE = Path(__file__).resolve().parent
INVENTORY = BASE / "gcm36-content-inventory.json"
REGISTRY = BASE / "adapter-registry.json"

PS3_ID = re.compile(r"^[A-Z0-9]{9}$")
PS4_ID = re.compile(r"^CUSA[0-9]{5}$")


class QueryError(Exception):
    pass


def load_inputs():
    inventory = json.loads(INVENTORY.read_text())
    registry = json.loads(REGISTRY.read_text())

    if not (
        inventory.get("read_only") is True
        and inventory.get("installation_enabled") is False
        and registry.get("adapter_status") == "discovery_only"
    ):
        raise QueryError("Unsafe or unsupported inventory state")

    registered = {
        item["id"]: item["system"]
        for item in registry["emulators"]
    }

    for row in inventory["records"]:
        if (
            row.get("installation_allowed") is not False
            or row.get("installed") is not None
            or row.get("active") is not None
        ):
            raise QueryError("Unexpected installation status")

    return inventory, registered


def resolve_emulator(emulator, system, registered):
    if emulator:
        if emulator not in registered:
            raise QueryError("Emulator is not registered")

        if system and registered[emulator] != system:
            raise QueryError("System/emulator mismatch")

        return emulator, registered[emulator]

    if not system:
        raise QueryError("Specify --emulator or --system")

    matches = [
        name for name, platform in registered.items()
        if platform == system
    ]

    if len(matches) != 1:
        raise QueryError(
            "System requires an explicit emulator selection"
        )

    return matches[0], system


def read_ps4_identity(folder):
    if not folder.is_dir():
        raise QueryError("PS4 game path is not a directory")

    sfo = folder / "sce_sys/param.sfo"

    if sfo.is_symlink() or not sfo.is_file():
        raise QueryError("PS4 game metadata unavailable")

    try:
        if sfo.stat().st_size > 2_000_000:
            raise QueryError("PS4 metadata exceeds size limit")

        metadata = parse_sfo(sfo.read_bytes())
    except OSError:
        raise QueryError("Cannot read PS4 game metadata")

    details = identity(metadata)

    title_id = details["title_id"]

    if not (
        title_id
        and PS4_ID.fullmatch(title_id)
        and not details["identity_conflict"]
        and details["category"] in ("gd", "gp")
    ):
        raise QueryError("PS4 game identity not verified")

    # Reject a misleading CUSA-named folder.
    if (
        PS4_ID.fullmatch(folder.name.upper())
        and folder.name.upper() != title_id
    ):
        raise QueryError("Folder and PS4 metadata disagree")

    return {
        "title_id": title_id,
        "identity_source": "ps4_param_sfo",
        "metadata_category": details["category"],
        "metadata_version": details["version"],
        "base_installation_verified": False,
    }


def read_ps3_identity(folder):
    if not folder.is_dir():
        raise QueryError("PS3 game path is not a directory")

    for sfo in (
        folder / "PS3_GAME/PARAM.SFO",
        folder / "PARAM.SFO",
    ):
        if sfo.is_symlink() or not sfo.is_file():
            continue

        try:
            metadata = read_sfo(sfo)
        except (OSError, ValueError):
            continue

        if not metadata:
            continue

        title_id = str(
            metadata.get("TITLE_ID", "")
        ).upper()

        if not PS3_ID.fullmatch(title_id):
            continue

        return {
            "title_id": title_id,
            "identity_source": "ps3_param_sfo",
            "metadata_category": str(
                metadata.get("CATEGORY", "")
            ),
            "metadata_version": str(
                metadata.get("APP_VER", "")
            ),
            "base_installation_verified": False,
        }

    raise QueryError("PS3 game identity not verified")


def game_identity(emulator, game_path):
    folder = Path(game_path)

    if not folder.exists():
        raise QueryError("Game path does not exist")

    if emulator == "shadps4":
        return read_ps4_identity(folder)

    if emulator == "rpcs3":
        return read_ps3_identity(folder)

    raise QueryError(
        "Game-path identity adapter not yet implemented: "
        + emulator
    )


def query(emulator=None, system=None, game_path=None, title_id=None):
    inventory, registered = load_inputs()

    emulator, system = resolve_emulator(
        emulator, system, registered
    )

    if game_path:
        identified = game_identity(emulator, game_path)

        if (
            title_id is not None
            and title_id.upper() != identified["title_id"]
        ):
            raise QueryError(
                "Requested title ID conflicts with game metadata"
            )

        title_id = identified["title_id"]
        identity_source = identified["identity_source"]
        identity_verified = True
    else:
        if not title_id:
            raise QueryError(
                "Specify --game-path or --title-id"
            )

        title_id = title_id.upper()

        if emulator == "shadps4":
            valid = bool(PS4_ID.fullmatch(title_id))
        elif emulator == "rpcs3":
            valid = bool(PS3_ID.fullmatch(title_id))
        else:
            valid = bool(re.fullmatch(
                r"[A-Z0-9]{4,32}", title_id
            ))

        if not valid:
            raise QueryError("Invalid title ID")

        identified = {}
        identity_source = "caller_supplied"
        identity_verified = False

    records = [
        row
        for row in inventory["records"]
        if (
            row["emulator"] == emulator
            and row["system"] == system
            and row["title_id"] == title_id
        )
    ]

    snapshot_modified = datetime.fromtimestamp(
        INVENTORY.stat().st_mtime,
        timezone.utc
    ).isoformat()

    return {
        "api_version": 1,
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "inventory_source": "saved_development_snapshot",
        "snapshot_modified_utc": snapshot_modified,
        "emulator": emulator,
        "system": system,
        "title_id": title_id,
        "game_path": game_path,
        "identity_source": identity_source,
        "game_identity_verified": identity_verified,
        "game_metadata": identified,
        "content_count": len(records),
        "updates": [
            row for row in records
            if row["content_kind"] == "update"
        ],
        "dlc": [
            row for row in records
            if row["content_kind"] == "dlc"
        ],
        "other_content": [
            row for row in records
            if row["content_kind"] not in ("update", "dlc")
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator")
    parser.add_argument("--system")
    parser.add_argument("--game-path")
    parser.add_argument("--title-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = query(
            emulator=args.emulator,
            system=args.system,
            game_path=args.game_path,
            title_id=args.title_id,
        )
    except QueryError as exc:
        print("GCM QUERY ERROR:", exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("===== GCM V3.6 GAME CONTENT =====")
    print("Emulator:", result["emulator"])
    print("Game ID:", result["title_id"])
    print(
        "Game identity verified:",
        result["game_identity_verified"]
    )
    print("Inventory:", result["inventory_source"])
    print("Updates:", len(result["updates"]))
    print("DLC:", len(result["dlc"]))
    print("Other content:", len(result["other_content"]))

    for kind in ("updates", "dlc", "other_content"):
        for row in result[kind]:
            print()
            print(row["content_kind"].upper())
            print(" Content:", row["content_id"])
            print(" Version:", row["version"])
            print(" Status:", row["status"])
            print(" Sources:", row["source_count"])
            print(" Installed:", row["installed"])
            print(" Active:", row["active"])

    print()
    print("Installation operations: DISABLED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
