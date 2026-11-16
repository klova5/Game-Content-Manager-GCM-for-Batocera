"""GCM v3.6: full-file integrity verification for PS4 updates.

Reads existing update packages only.
Never modifies original packages.
"""

import hashlib
import json
import os
import stat
import time

from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent

MATRIX = BASE / "shadps4-compatibility-matrix.json"
REPORT = BASE / "shadps4-update-integrity.json"

CHUNK = 8 * 1024 * 1024
PROGRESS = 256 * 1024 * 1024

ALLOWED_ROOTS = (
    Path("/userdata/roms/ps4"),
    Path("/media/easystore/emulators/roms/ps4"),
)


def verify_source(record):
    source = Path(record["source"])

    if not any(
        source == root or root in source.parents
        for root in ALLOWED_ROOTS
    ):
        raise ValueError(
            f"Unexpected package source: {source}"
        )

    expected = record["source_evidence"]

    size = expected["expected_size"]
    header_sha = expected["header_sha256"]

    if not isinstance(size, int) or size <= 4096:
        raise ValueError("Invalid expected package size")

    if not isinstance(header_sha, str) or len(header_sha) != 64:
        raise ValueError("Invalid expected header hash")

    if source.is_symlink():
        raise ValueError("Package symlink rejected")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )

    descriptor = os.open(source, flags)

    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())

        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Source is not a regular file")

        if before.st_size != size:
            raise ValueError(
                f"Package size changed: {source}"
            )

        header = stream.read(4096)

        if header[:4] != b"\x7fCNT":
            raise ValueError("PS4 package header missing")

        actual_header_sha = hashlib.sha256(
            header
        ).hexdigest()

        if actual_header_sha != header_sha:
            raise ValueError(
                f"Package header changed: {source}"
            )

        stream.seek(0)

        digest = hashlib.sha256()

        processed = 0
        next_progress = PROGRESS

        started = time.monotonic()

        while True:
            block = stream.read(CHUNK)

            if not block:
                break

            digest.update(block)
            processed += len(block)

            if processed >= next_progress:
                print(
                    f"  Read {processed:,} / {size:,} bytes",
                    flush=True
                )
                next_progress = processed + PROGRESS

        after = os.fstat(stream.fileno())

    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )

    if any(
        getattr(before, field)
        != getattr(after, field)
        for field in stable_fields
    ):
        raise RuntimeError(
            f"Source changed during verification: {source}"
        )

    if processed != size:
        raise RuntimeError(
            f"Incomplete file read: {source}"
        )

    return {
        "source": str(source),
        "size_bytes": processed,
        "sha256": digest.hexdigest(),
        "stable_during_read": True,
        "filesystem_identity": [
            before.st_dev,
            before.st_ino,
        ],
        "seconds_to_hash": round(
            time.monotonic() - started, 2
        ),
    }


def main():
    matrix = json.loads(
        MATRIX.read_text(encoding="utf-8")
    )

    assert matrix["read_only"] is True
    assert matrix["installation_enabled"] is False

    updates = [
        item
        for item in matrix["packages"]
        if item["content_kind"] == "update"
    ]

    assert len(updates) == 6, (
        "Expected six PS4 update sources; "
        "review inventory changes before proceeding"
    )

    assert all(
        item["identity_valid"] is True
        and item["target_title_identity_verified"] is True
        and item["installation_allowed"] is False
        and item["installed"] is None
        and item["active"] is None
        for item in updates
    )

    groups = defaultdict(list)

    for item in updates:
        source = item["source_evidence"]

        key = (
            item["title_id"],
            item["content_id"],
            item["version"],
            source["expected_size"],
            source["header_sha256"],
        )

        groups[key].append(item)

    print("===== PS4 UPDATE INTEGRITY =====")
    print("Update source files:", len(updates))
    print("Candidate update variants:", len(groups))

    results = []

    for key, items in sorted(groups.items()):
        title_id, content_id, version, size, header = key

        print()
        print("===== VERIFY UPDATE =====")
        print("Title ID:", title_id)
        print("Content ID:", content_id)
        print("Version:", version)
        print("Sources:", len(items))

        hashes = []

        for index, item in enumerate(items, 1):
            print()
            print(
                f"VERIFYING SOURCE {index}/{len(items)}:",
                item["source"],
                flush=True
            )

            verified = verify_source(item)

            hashes.append(verified)

            print(
                "PASS: Full-file SHA256:",
                verified["sha256"],
                flush=True
            )
            print(
                "Elapsed:",
                verified["seconds_to_hash"],
                "seconds",
                flush=True
            )

        identical = (
            len({row["sha256"] for row in hashes}) == 1
        )

        print()
        print("===== COMPARISON RESULT =====")
        print("Title ID:", title_id)
        print("Source count:", len(hashes))
        print("Full-file hashes identical:", identical)

        if len(hashes) > 1 and not identical:
            print(
                "WARNING: Sources differ despite "
                "matching package metadata"
            )

        results.append({
            "emulator": "shadps4",
            "system": "ps4",
            "title_id": title_id,
            "content_id": content_id,
            "version": version,
            "content_kind": "update",
            "source_count": len(hashes),
            "source_files": hashes,
            "full_file_equality_verified": (
                len(hashes) > 1 and identical
            ),
            "source_hashes_verified": True,
            "base_game_target_identified": True,
            "compatibility_verified": False,
            "installed": None,
            "active": None,
            "installation_allowed": False,
        })

    report = {
        "schema_version": 1,
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "variants": results,
    }

    with REPORT.open(
        "x", encoding="utf-8"
    ) as stream:
        json.dump(
            report,
            stream,
            indent=2,
            ensure_ascii=False
        )
        stream.write("\n")

    print()
    print("===== FINAL RESULTS =====")
    print("Verified update variants:", len(results))
    print(
        "Byte-identical source pairs:",
        sum(
            r["full_file_equality_verified"]
            for r in results
        )
    )
    print(
        "Different-source variants:",
        sum(
            r["source_count"] > 1
            and not r["full_file_equality_verified"]
            for r in results
        )
    )

    print("Content installation: DISABLED")
    print("Installed/active status: UNVERIFIED")
    print("Original source files modified: NO")
    print()
    print("Report:", REPORT)


if __name__ == "__main__":
    main()
