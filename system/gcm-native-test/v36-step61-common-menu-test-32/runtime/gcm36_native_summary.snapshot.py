"""Read-only summary bridge for GCM v3.6 native menus."""

import argparse
import sys

from gcm36_game_query import query, QueryError
from gcm36_ps4_launcher_resolver import resolve_ps4_game_path


def summarize(emulator, rom):
    if emulator not in ("shadps4", "rpcs3"):
        raise QueryError("Unsupported summary adapter")

    if emulator == "shadps4":
        rom = str(resolve_ps4_game_path(rom))

    result = query(
        emulator=emulator,
        game_path=rom,
    )

    updates = result["updates"]
    dlc = result["dlc"]

    matching_patches = sum(
        row["status"] == "MATCHING_PATCH_PRESENT"
        for row in updates
    )

    if matching_patches:
        update_text = f"{matching_patches} PATCH PRESENT"
    else:
        update_text = f"{len(updates)} CANDIDATES"

    dlc_text = f"{len(dlc)} DLC CANDIDATES"

    return update_text, dlc_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--rom", required=True)
    args = parser.parse_args()

    try:
        updates, dlc = summarize(
            args.emulator,
            args.rom,
        )
    except (QueryError, OSError, ValueError, KeyError) as exc:
        message = str(exc).replace("\n", " ").replace("\t", " ")
        print("ERROR\t" + message[:200])
        return 2

    print(f"SUMMARY\t{updates}\t{dlc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
