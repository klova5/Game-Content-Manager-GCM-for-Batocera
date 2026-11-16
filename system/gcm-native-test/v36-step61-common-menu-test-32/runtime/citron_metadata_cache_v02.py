#!/usr/bin/env python3
"""GCM v3.6 Step 55 - Citron cached game identity reader.

Read-only. Cache entries do not establish installed or active
update/DLC status. Never prints paths, keys, or icon contents.
"""

import argparse
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CACHE = Path(
    "/userdata/system/.cache/citron/game_list/"
    "game_metadata_cache.json"
)

MAX_BYTES = 50 * 1024 * 1024


def normalize_id(value):
    if not isinstance(value, str):
        return None

    value = value.strip().upper()

    if value.startswith("0X"):
        value = value[2:]

    if re.fullmatch(r"[0-9A-F]{16}", value):
        return value

    # Citron cache may omit the leading zero.
    # This is a format inference, not identity verification.
    if re.fullmatch(r"[0-9A-F]{15}", value):
        return "0" + value

    return None


def inspect(cache, title_id=None):
    if not cache.is_file() or cache.is_symlink():
        raise ValueError("Missing or unsafe Citron cache")

    if cache.stat().st_size > MAX_BYTES:
        raise ValueError("Metadata cache exceeds size limit")

    data = json.loads(cache.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError("Invalid cache root")

    entries = data.get("entries")

    if not isinstance(entries, list):
        raise ValueError("Invalid cache entry collection")

    valid_ids = []
    invalid = 0
    leading_zero_inferred = 0

    for entry in entries:
        if not isinstance(entry, dict):
            invalid += 1
            continue

        program_id = normalize_id(entry.get("program_id"))

        if program_id is None:
            invalid += 1
            continue

        raw = entry.get("program_id")
        if isinstance(raw, str):
            raw_hex = raw.strip().upper()
            if raw_hex.startswith("0X"):
                raw_hex = raw_hex[2:]
            if re.fullmatch(r"[0-9A-F]{15}", raw_hex):
                leading_zero_inferred += 1

        valid_ids.append(program_id)

    unique_ids = set(valid_ids)

    report = {
        "emulator": "citron-emu",
        "source": "cached_game_list",
        "mode": "read_only",
        "cache_modified_utc": datetime.fromtimestamp(
            cache.stat().st_mtime,
            timezone.utc
        ).isoformat(),
        "cache_freshness": "UNVERIFIED",
        "cached_entries": len(entries),
        "valid_program_id_entries": len(valid_ids),
        "unique_program_ids": len(unique_ids),
        "invalid_program_id_entries": invalid,
        "leading_zero_inferred_entries": leading_zero_inferred,
        "duplicate_program_id_entries": (
            len(valid_ids) - len(unique_ids)
        ),
        "game_identity_source": "CACHE_ONLY",
        "installed_update_status": "UNKNOWN",
        "installed_dlc_status": "UNKNOWN",
        "active_status": "UNKNOWN",
        "installation_enabled": False,
    }

    if title_id is not None:
        requested = normalize_id(title_id)

        if requested is None:
            raise ValueError("Invalid requested title ID")

        report["requested_title_id"] = requested
        report["matching_cached_entries"] = (
            valid_ids.count(requested)
        )

    return report


def self_test():
    with tempfile.TemporaryDirectory() as temporary:
        cache = Path(temporary) / "metadata.json"

        data = {
            "entries": [
                {
                    "program_id": "0100000000001000",
                    "title": "Example",
                    "file_path": "/private/example",
                    "key": "DO_NOT_PRINT"
                },
                {
                    "program_id": "0x0100000000001000",
                    "title": "Duplicate"
                },
                {
                    "program_id": "invalid"
                }
            ]
        }

        cache.write_text(json.dumps(data))

        result = inspect(
            cache,
            title_id="0100000000001000"
        )

        assert result["cached_entries"] == 3
        assert result["valid_program_id_entries"] == 2
        assert result["unique_program_ids"] == 1
        assert result["duplicate_program_id_entries"] == 1
        assert result["invalid_program_id_entries"] == 1
        assert result["matching_cached_entries"] == 2
        assert result["installation_enabled"] is False
        assert result["active_status"] == "UNKNOWN"

        output = json.dumps(result)

        assert "/private/example" not in output
        assert "DO_NOT_PRINT" not in output

        assert normalize_id("100000000001000") == (
            "0100000000001000"
        )

        assert normalize_id("123456789012345") == (
            "0123456789012345"
        )

        assert normalize_id("12345678901234G") is None

        data["entries"].append({
            "program_id": "100000000001000"
        })
        cache.write_text(json.dumps(data))

        inferred = inspect(
            cache,
            title_id="0100000000001000"
        )

        assert inferred["cached_entries"] == 4
        assert inferred["valid_program_id_entries"] == 3
        assert inferred["unique_program_ids"] == 1
        assert inferred["matching_cached_entries"] == 3
        assert inferred["leading_zero_inferred_entries"] == 1
        assert inferred["installed_update_status"] == "UNKNOWN"
        assert inferred["installed_dlc_status"] == "UNKNOWN"
        assert inferred["installation_enabled"] is False

    print("PASS: Citron metadata reader self-test")
    print("PASS: Title ID validation")
    print("PASS: Duplicate identity detection")
    print("PASS: Game-specific cache matching")
    print("PASS: Private cache fields excluded")
    print("PASS: Installation remains disabled")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE
    )
    parser.add_argument("--title-id")
    parser.add_argument("--self-test", action="store_true")

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    report = inspect(args.cache, args.title_id)

    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
