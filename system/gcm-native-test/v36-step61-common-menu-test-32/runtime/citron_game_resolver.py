#!/usr/bin/env python3
"""GCM Step 55: read-only Citron ROM-to-cache resolver."""

import argparse
import json
import tempfile
from pathlib import Path

from citron_metadata_cache_v02 import (
    DEFAULT_CACHE, MAX_BYTES, normalize_id
)


def resolve_game(cache, rom):
    if not cache.is_file() or cache.is_symlink():
        raise ValueError("Unsafe or missing cache")

    if cache.stat().st_size > MAX_BYTES:
        raise ValueError("Cache exceeds size limit")

    data = json.loads(cache.read_text(encoding="utf-8"))

    if not isinstance(data, dict) or not isinstance(
        data.get("entries"), list
    ):
        raise ValueError("Invalid cache schema")

    if not rom.is_absolute():
        raise ValueError("Absolute ROM path required")

    result = {
        "emulator": "citron-emu",
        "mode": "read_only",
        "identity_source": "CACHE_ONLY",
        "identity_verified": False,
        "cache_freshness": "UNVERIFIED",
        "match_status": "ROM_UNAVAILABLE",
        "matching_cached_entries": 0,
        "normalized_program_id": None,
        "leading_zero_inferred": False,
        "installed_update_status": "UNKNOWN",
        "installed_dlc_status": "UNKNOWN",
        "active_status": "UNKNOWN",
        "installation_enabled": False,
    }

    if not rom.is_file():
        return result

    target = rom.resolve(strict=True)
    matched = []

    for entry in data["entries"]:
        if not isinstance(entry, dict):
            continue

        raw = entry.get("file_path")

        if not isinstance(raw, str):
            continue

        path = Path(raw)

        if not path.is_absolute() or not path.is_file():
            continue

        try:
            if path.resolve(strict=True) == target:
                matched.append(entry.get("program_id"))
        except OSError:
            continue

    result["matching_cached_entries"] = len(matched)

    if not matched:
        result["match_status"] = "NO_CACHE_MATCH"
        return result

    ids = [normalize_id(value) for value in matched]

    if any(value is None for value in ids):
        result["match_status"] = "UNVERIFIED_PROGRAM_ID"
        return result

    if len(set(ids)) != 1:
        result["match_status"] = "AMBIGUOUS_PROGRAM_ID"
        return result

    result["match_status"] = "CACHE_MATCH_UNVERIFIED"
    result["normalized_program_id"] = ids[0]

    result["leading_zero_inferred"] = any(
        len(value.strip().removeprefix("0x").removeprefix("0X")) == 15
        for value in matched
    )

    return result


def self_test():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "a" / "game.nsp"
        b = root / "b" / "game.nsp"

        a.parent.mkdir()
        b.parent.mkdir()
        a.touch()
        b.touch()

        cache = root / "cache.json"

        def save(rows):
            cache.write_text(json.dumps({"entries": rows}))

        row = {
            "file_path": str(a),
            "program_id": "100000000001000",
            "key": "PRIVATE_TEST_VALUE",
        }

        save([row])

        found = resolve_game(cache, a)

        assert found["match_status"] == "CACHE_MATCH_UNVERIFIED"
        assert found["normalized_program_id"] == "0100000000001000"
        assert found["leading_zero_inferred"] is True
        assert found["installation_enabled"] is False
        assert found["installed_update_status"] == "UNKNOWN"
        assert "PRIVATE_TEST_VALUE" not in json.dumps(found)

        assert resolve_game(cache, b)["match_status"] == "NO_CACHE_MATCH"

        save([row, dict(row)])
        assert resolve_game(cache, a)["matching_cached_entries"] == 2

        save([
            row,
            {"file_path": str(a), "program_id": "100000000001001"},
        ])
        conflict = resolve_game(cache, a)

        assert conflict["match_status"] == "AMBIGUOUS_PROGRAM_ID"
        assert conflict["normalized_program_id"] is None

        save([
            row,
            {"file_path": str(a), "program_id": "invalid"},
        ])
        assert resolve_game(cache, a)["match_status"] == "UNVERIFIED_PROGRAM_ID"

        assert resolve_game(
            cache, root / "missing.nsp"
        )["match_status"] == "ROM_UNAVAILABLE"

    print("PASS: Exact ROM path matching")
    print("PASS: Same-name ROM collision rejected")
    print("PASS: Cached title ID normalization")
    print("PASS: Ambiguous and invalid identities rejected")
    print("PASS: Missing ROM handling")
    print("PASS: Installation disabled and privacy preserved")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--rom", type=Path)
    parser.add_argument("--self-test", action="store_true")

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if args.rom is None:
        parser.error("--rom is required")

    print(json.dumps(
        resolve_game(args.cache, args.rom),
        sort_keys=True
    ))


if __name__ == "__main__":
    main()
