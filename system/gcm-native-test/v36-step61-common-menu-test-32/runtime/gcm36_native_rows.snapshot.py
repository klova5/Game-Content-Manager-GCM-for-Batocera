"""GCM v3.6 native frontend text interface.

Returns small, tab-delimited read-only inventory results.
No installation, copy, delete, or activation operations.
"""

import argparse
import sys

from gcm36_game_query import query, QueryError
from gcm36_ps4_launcher_resolver import resolve_ps4_game_path


def clean(value):
    if value is None:
        return ""

    text = str(value)

    return (
        text.replace("\t", " ")
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("\x00", "")
    )[:240]


def native_rows(emulator, rom, content_type):
    if emulator not in ("shadps4", "rpcs3"):
        raise QueryError(
            "Unsupported native development adapter"
        )

    if content_type not in ("update", "dlc"):
        raise QueryError("Unsupported content type")

    if emulator == "shadps4":
        rom = str(resolve_ps4_game_path(rom))

    result = query(
        emulator=emulator,
        game_path=rom,
    )

    selected = (
        result["updates"]
        if content_type == "update"
        else result["dlc"]
    )

    output = [
        "\t".join([
            "META",
            clean(result["title_id"]),
            clean(result["emulator"]),
            "VERIFIED" if result["game_identity_verified"]
            else "UNVERIFIED",
        ])
    ]

    for item in selected:
        status = item["status"]

        if status == "MATCHING_PATCH_PRESENT":
            label = "MATCHED PATCH - INSTALL UNVERIFIED"
        elif status == "DLC_CANDIDATE_FOUND_UNVERIFIED":
            label = "DLC CANDIDATE - INSTALL UNVERIFIED"
        elif status == "BLOCKED_NO_MATCHING_TITLE_ID":
            label = "BLOCKED - NO BASE MATCH IN SNAPSHOT"
        else:
            label = status.replace("_", " ")

        title = item["content_id"]

        if item["content_kind"] == "update" and item["version"]:
            title = "VERSION " + item["version"]

        output.append("\t".join([
            "ROW",
            clean(item["content_kind"]),
            clean(label),
            clean(title),
            str(item["source_count"]),
        ]))

    output.append(
        "COUNT\t" + str(len(selected))
    )

    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator", required=True)
    parser.add_argument("--rom", required=True)
    parser.add_argument(
        "--type",
        required=True,
        choices=("update", "dlc"),
    )

    args = parser.parse_args()

    try:
        lines = native_rows(
            args.emulator,
            args.rom,
            args.type,
        )
    except (QueryError, OSError, ValueError, KeyError) as exc:
        print("ERROR\t" + clean(exc))
        return 2

    for line in lines:
        print(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
