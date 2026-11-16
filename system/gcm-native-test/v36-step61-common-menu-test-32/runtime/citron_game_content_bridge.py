#!/usr/bin/env python3
"""Citron Step 56: fail-closed game-specific content status."""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from citron_game_resolver import resolve_game
from citron_metadata_cache_v02 import DEFAULT_CACHE

contract_dir = (
    Path(__file__).resolve().parent /
    "citron-provider-contract-v01"
)

sys.path.insert(0, str(contract_dir))

from citron_provider_contract import (
    LiveCitronProvider,
    read_game_inventory,
)

def format_status(result):
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
            raise ValueError("Unsafe Citron result: " + key)

    match = result.get("match_status")
    title_id = result.get("normalized_program_id")

    if match == "CACHE_MATCH_UNVERIFIED":
        if not isinstance(title_id, str):
            raise ValueError("Missing cached title ID")

        if not re.fullmatch(r"[0-9A-F]{16}", title_id):
            raise ValueError("Invalid cached title ID")

        try:
            read_game_inventory(LiveCitronProvider(), title_id)
        except PermissionError:
            provider = "LIVE_PROVIDER_BLOCKED"
        else:
            raise RuntimeError("Unsafe live provider authorization")

    elif match in {
        "ROM_UNAVAILABLE",
        "NO_CACHE_MATCH",
        "UNVERIFIED_PROGRAM_ID",
        "AMBIGUOUS_PROGRAM_ID",
    }:
        if title_id is not None:
            raise ValueError("Unverified identity exposes title ID")
        provider = "UNVERIFIED_IDENTITY"

    else:
        raise ValueError("Unrecognized cache match status")

    return "\t".join((
        "CITRON_CONTENT",
        provider,
        "UNKNOWN",
        "UNKNOWN",
        "UNKNOWN",
        "UNKNOWN",
        "DISABLED",
    ))

def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        rom = folder / "synthetic.nsp"
        cache = folder / "cache.json"

        rom.touch()
        cache.write_text(json.dumps({
            "entries": [{
                "file_path": str(rom),
                "program_id": "100000000001000",
                "key": "DO_NOT_PRINT_PRIVATE_DATA",
            }]
        }))

        row = format_status(resolve_game(cache, rom))

        assert row == (
            "CITRON_CONTENT\tLIVE_PROVIDER_BLOCKED\t"
            "UNKNOWN\tUNKNOWN\tUNKNOWN\tUNKNOWN\tDISABLED"
        )

        assert "DO_NOT_PRINT_PRIVATE_DATA" not in row
        print("PASS: Cached identity never promoted")

        missing = format_status(
            resolve_game(cache, folder / "missing.nsp")
        )

        assert "\tUNVERIFIED_IDENTITY\t" in missing
        print("PASS: Missing ROM handled safely")

        unsafe = resolve_game(cache, rom)
        unsafe["installation_enabled"] = True

        try:
            format_status(unsafe)
        except ValueError:
            print("PASS: Unsafe installation capability rejected")
        else:
            raise AssertionError("Unsafe capability accepted")

    print("PASS: Live metadata provider remains blocked")
    print("PASS: Update/DLC/active statuses remain UNKNOWN")

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

    print(format_status(resolve_game(args.cache, args.rom)))

if __name__ == "__main__":
    main()
