#!/usr/bin/env python3
"""Step 55 Citron cached game identity bridge. Read-only."""

import argparse
import json
import re
import tempfile
from pathlib import Path

from citron_game_resolver import resolve_game
from citron_metadata_cache_v02 import DEFAULT_CACHE

STATUSES = {
    "ROM_UNAVAILABLE",
    "NO_CACHE_MATCH",
    "UNVERIFIED_PROGRAM_ID",
    "AMBIGUOUS_PROGRAM_ID",
    "CACHE_MATCH_UNVERIFIED",
}

def format_record(result):
    required = {
        "emulator": "citron-emu",
        "mode": "read_only",
        "identity_source": "CACHE_ONLY",
        "identity_verified": False,
        "installation_enabled": False,
        "installed_update_status": "UNKNOWN",
        "installed_dlc_status": "UNKNOWN",
        "active_status": "UNKNOWN",
    }

    for key, value in required.items():
        if result.get(key) != value:
            raise ValueError("Unsafe identity result: " + key)

    status = result.get("match_status")
    if status not in STATUSES:
        raise ValueError("Unknown match status")

    count = result.get("matching_cached_entries")
    if type(count) is not int or not 0 <= count <= 99999999:
        raise ValueError("Invalid match count")

    program_id = "UNAVAILABLE"
    inferred = "NO"

    if status == "CACHE_MATCH_UNVERIFIED":
        program_id = result.get("normalized_program_id")
        if not isinstance(program_id, str) or not re.fullmatch(
            r"[0-9A-F]{16}", program_id
        ):
            raise ValueError("Invalid program ID")
        flag = result.get("leading_zero_inferred")
        if type(flag) is not bool:
            raise ValueError("Invalid normalization flag")
        inferred = "YES" if flag else "NO"
    elif result.get("normalized_program_id") is not None:
        raise ValueError("Unverified identity contains a program ID")

    return "\t".join(
        ("CITRON_GAME", status, program_id, inferred, str(count))
    )

def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        rom = root / "game.nsp"
        cache = root / "cache.json"
        rom.touch()

        row = {
            "file_path": str(rom),
            "program_id": "100000000001000",
            "key": "PRIVATE_TEST_VALUE",
        }

        cache.write_text(json.dumps({"entries": [row]}))
        output = format_record(resolve_game(cache, rom))

        assert output == (
            "CITRON_GAME\tCACHE_MATCH_UNVERIFIED\t"
            "0100000000001000\tYES\t1"
        )
        assert "PRIVATE_TEST_VALUE" not in output

        missing = format_record(
            resolve_game(cache, root / "missing.nsp")
        )
        assert "\tROM_UNAVAILABLE\t" in missing

        cache.write_text(json.dumps({
            "entries": [
                row,
                {
                    "file_path": str(rom),
                    "program_id": "100000000001001",
                },
            ]
        }))
        conflict = format_record(resolve_game(cache, rom))
        assert "\tAMBIGUOUS_PROGRAM_ID\tUNAVAILABLE\t" in conflict

    print("PASS: Citron game bridge self-test")
    print("PASS: Cached game identity protocol")
    print("PASS: Ambiguous identities rejected")
    print("PASS: Private data excluded")
    print("PASS: Installation disabled")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if args.rom is None:
        parser.error("--rom is required")

    print(format_record(resolve_game(args.cache, args.rom)))

if __name__ == "__main__":
    main()
