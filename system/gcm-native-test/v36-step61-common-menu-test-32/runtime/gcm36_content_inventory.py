"""GCM v3.6 unified read-only game-content inventory."""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "gcm36-content-inventory.json"


def load(name):
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def make_inventory():
    registry = load("adapter-registry.json")
    evidence = load("unified-evidence.json")
    updates = load("shadps4-update-status.json")
    integrity = load("shadps4-update-integrity.json")
    dlc = load("shadps4-dlc-status.json")

    assert registry["adapter_status"] == "discovery_only"

    for report in (evidence, updates, integrity, dlc):
        assert report["read_only"] is True
        assert report["installation_enabled"] is False

    adapters = {
        row["id"]: {
            "emulator": row["id"],
            "system": row["system"],
            "development_status": "DISCOVERY_PENDING",
            "content_records": 0,
        }
        for row in registry["emulators"]
    }

    assert len(adapters) == 9

    adapters["ryujinx"]["development_status"] = (
        "V35_PRODUCTION_FOUNDATION_NOT_YET_MIGRATED"
    )
    adapters["citron"]["development_status"] = (
        "ADAPTER_PENDING"
    )
    adapters["rpcs3"]["development_status"] = (
        "PACKAGE_EVIDENCE_IMPLEMENTED"
    )
    adapters["cemu"]["development_status"] = (
        "DISCOVERY_IMPLEMENTED_NO_VERIFIED_CONTENT"
    )
    adapters["shadps4"]["development_status"] = (
        "UPDATE_AND_DLC_EVIDENCE_IMPLEMENTED"
    )

    records = []

    # PS3: preserve unknown package type and unknown status.
    for row in evidence["records"]:
        assert row["installation_allowed"] is False

        group = row["source_group"]

        records.append({
            "emulator": row["emulator"],
            "system": row["system"],
            "title_id": row["title_id"],
            "content_id": row["content_id"],
            "content_kind": row["content_kind"],
            "version": None,
            "status": "PACKAGE_IDENTIFIED_UNVERIFIED",
            "source_count": group["source_count"],
            "sources": [
                source["path"]
                for source in group["sources"]
            ],
            "source_equality_verified_at_last_hash": (
                group["hash_confirmed_identical_at_last_read"]
            ),
            "last_verified_sha256": (
                group["sha256_at_last_full_read"]
            ),
            "target_identity_verified": (
                row["base_game_identity_verified"]
            ),
            "installed": None,
            "active": None,
            "installation_allowed": False,
            "blockers": list(row["blockers"]),
        })

    # PS4: match live metadata assessments to historical
    # full-file SHA256 reports.
    verified_updates = {}

    for variant in integrity["variants"]:
        key = (
            variant["title_id"],
            variant["content_id"],
            variant["version"],
        )
        if key in verified_updates:
            raise RuntimeError("Duplicate update variant")
        verified_updates[key] = variant

    for row in updates["updates"]:
        key = (
            row["title_id"],
            row["content_id"],
            row["package_version"],
        )
        variant = verified_updates[key]

        sources = variant["source_files"]
        hashes = {source["sha256"] for source in sources}

        assert len(sources) == 2
        assert len(hashes) == 1
        assert variant["full_file_equality_verified"] is True

        status = row["status"]

        blockers = [
            "BASE_INSTALLATION_NOT_INDEPENDENTLY_VERIFIED",
            "RUNTIME_ACTIVE_STATUS_NOT_VERIFIED",
            "EMULATOR_NATIVE_INSTALLATION_NOT_VALIDATED",
        ]

        if status != "MATCHING_PATCH_PRESENT":
            blockers.append("MATCHING_PATCH_NOT_CONFIRMED")

        records.append({
            "emulator": "shadps4",
            "system": "ps4",
            "title_id": row["title_id"],
            "content_id": row["content_id"],
            "content_kind": "update",
            "version": row["package_version"],
            "status": status,
            "source_count": len(sources),
            "sources": [
                source["source"]
                for source in sources
            ],
            "source_equality_verified_at_last_hash": True,
            "last_verified_sha256": next(iter(hashes)),
            "target_identity_verified": (
                status == "MATCHING_PATCH_PRESENT"
            ),
            "installed": None,
            "active": None,
            "installation_allowed": False,
            "blockers": blockers,
        })

    # PS4 DLC: metadata grouping only. No source hash claim.
    for row in dlc["dlc_variants"]:
        assert row["installation_allowed"] is False

        title_id = row["title_id"]
        target_found = row["matching_game_target_identified"]

        if target_found:
            status = (
                "MATCHING_DLC_METADATA_PRESENT"
                if row["matching_extracted_metadata_paths"]
                else "DLC_CANDIDATE_FOUND_UNVERIFIED"
            )
        else:
            status = "BLOCKED_NO_MATCHING_TITLE_ID"

        blockers = [
            "FULL_SOURCE_SHA256_NOT_VERIFIED",
            "DLC_INSTALLATION_NOT_VALIDATED",
            "RUNTIME_DLC_STATUS_NOT_VERIFIED",
            "BASE_INSTALLATION_NOT_INDEPENDENTLY_VERIFIED",
        ]

        if not target_found:
            blockers.append("NO_MATCHING_EXTRACTED_GAME_TITLE")

        records.append({
            "emulator": "shadps4",
            "system": "ps4",
            "title_id": title_id,
            "content_id": row["content_id"],
            "content_kind": "dlc",
            "version": None,
            "status": status,
            "source_count": row["source_count"],
            "sources": list(row["sources"]),
            "source_equality_verified_at_last_hash": False,
            "last_verified_sha256": None,
            "target_identity_verified": target_found,
            "installed": None,
            "active": None,
            "installation_allowed": False,
            "blockers": blockers,
        })

    records.sort(key=lambda r: (
        r["emulator"],
        r["title_id"],
        r["content_kind"],
        r["content_id"],
    ))

    # Detect accidental cross-title or duplicate records.
    seen = set()

    for row in records:
        key = (
            row["emulator"],
            row["title_id"],
            row["content_id"],
            row["content_kind"],
            row["version"],
        )

        if key in seen:
            raise RuntimeError("Duplicate inventory identity")

        seen.add(key)

        if row["emulator"] == "shadps4":
            if not re.fullmatch(
                r"CUSA[0-9]{5}",
                row["title_id"]
            ):
                raise RuntimeError("Invalid PS4 title ID")

        assert row["installed"] is None
        assert row["active"] is None
        assert row["installation_allowed"] is False

        adapters[row["emulator"]]["content_records"] += 1

    ps4_updates = [
        r for r in records
        if r["emulator"] == "shadps4"
        and r["content_kind"] == "update"
    ]
    ps4_dlc = [
        r for r in records
        if r["emulator"] == "shadps4"
        and r["content_kind"] == "dlc"
    ]
    ps3 = [
        r for r in records
        if r["emulator"] == "rpcs3"
    ]

    assert len(ps4_updates) == 3
    assert len(ps4_dlc) == 16
    assert len(ps3) == 1

    assert all(
        r["status"] == "MATCHING_PATCH_PRESENT"
        for r in ps4_updates
    )

    assert all(
        r["status"] == "BLOCKED_NO_MATCHING_TITLE_ID"
        for r in ps4_dlc
        if r["title_id"] == "CUSA08034"
    )

    assert len(records) == 20

    return {
        "schema_version": 1,
        "project": "GCM v3.6 Multi-Emulator",
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "status_semantics": {
            "MATCHING_PATCH_PRESENT":
                "Matching extracted patch metadata found; activity unverified",
            "DLC_CANDIDATE_FOUND_UNVERIFIED":
                "DLC package identified for an extracted title; installation unverified",
            "BLOCKED_NO_MATCHING_TITLE_ID":
                "No matching extracted game title ID",
            "PACKAGE_IDENTIFIED_UNVERIFIED":
                "Package identified; compatibility and installation unverified",
        },
        "adapters": list(adapters.values()),
        "records": records,
    }


def show(inventory, emulator=None, title=None):
    rows = inventory["records"]

    if emulator:
        rows = [
            r for r in rows if r["emulator"] == emulator
        ]

    if title:
        rows = [
            r for r in rows if r["title_id"] == title
        ]

    print("===== GCM V3.6 UNIFIED INVENTORY =====")
    print("Registered adapters:", len(inventory["adapters"]))
    print("Content records:", len(rows))
    print("Installation enabled: NO")

    grouped = defaultdict(list)

    for row in rows:
        grouped[
            (row["emulator"], row["title_id"])
        ].append(row)

    for (emulator_name, title_id), items in sorted(grouped.items()):
        print()
        print("EMULATOR:", emulator_name)
        print("GAME ID:", title_id)
        print("Content records:", len(items))
        print(
            "Types:",
            dict(Counter(r["content_kind"] for r in items))
        )

        for item in items:
            print(
                " ",
                item["content_kind"].upper(),
                "|",
                item["version"] or item["content_id"],
                "|",
                item["status"]
            )
            print(
                "   Sources:",
                item["source_count"],
                "| Full equality verified:",
                item["source_equality_verified_at_last_hash"]
            )

    print()
    print("===== ADAPTER COVERAGE =====")

    for adapter in inventory["adapters"]:
        print(
            adapter["emulator"],
            "| records:",
            adapter["content_records"],
            "|",
            adapter["development_status"]
        )

    print()
    print("No native emulator installation performed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emulator")
    parser.add_argument("--title")
    parser.add_argument(
        "--query-only", action="store_true"
    )
    args = parser.parse_args()

    inventory = make_inventory()

    if not args.query_only:
        with OUTPUT.open("x", encoding="utf-8") as stream:
            json.dump(
                inventory,
                stream,
                indent=2,
                ensure_ascii=False
            )
            stream.write("\n")

        print("Saved development report:", OUTPUT)

    show(inventory, args.emulator, args.title)


if __name__ == "__main__":
    main()
