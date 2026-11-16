"""GCM v3.6: read-only, cross-emulator evidence ledger."""

import json
import re
import stat
from pathlib import Path

BASE = Path(__file__).resolve().parent

PS3_ID = re.compile(r"^[A-Z0-9]{9}$")
WIIU_ID = re.compile(r"^[0-9a-f]{16}$")
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")

WIIU_KINDS = {
    "0005000e": "update",
    "0005000c": "dlc",
    "00050000": "base_game",
}


def source_exists_unchanged_size(source):
    """
    Check current file existence and size.

    This is NOT a renewed SHA256 verification.
    """
    path = Path(source["source"])

    try:
        info = path.lstat()

        return (
            stat.S_ISREG(info.st_mode)
            and info.st_size == source["size_bytes"]
        )
    except OSError:
        return False


def reconcile_ps3(integrity, inventory):
    known_titles = {
        game["title_id"]
        for game in inventory["rpcs3"]["games"]
    }

    records = []

    for package in integrity["results"]:
        title_id = str(package["title_id"]).upper()

        if not PS3_ID.fullmatch(title_id):
            raise ValueError(
                "Invalid PS3 title ID in integrity report"
            )

        variants = {}

        for source in package["sources"]:
            digest = source.get("sha256", "")
            size = source.get("size_bytes", -1)

            if (
                not HEX_SHA.fullmatch(digest)
                or not isinstance(size, int)
                or size <= 0
            ):
                raise ValueError(
                    "Source was not fully hashed"
                )

            if source.get("stable_during_read") is not True:
                raise ValueError(
                    "Source changed during original hashing"
                )

            key = (digest, size)

            variants.setdefault(key, []).append({
                "path": source["source"],
                "size_bytes": size,
                "present_at_reconciliation":
                    source_exists_unchanged_size(source),
                "last_verified_sha256": digest,
            })

        base_resolved = (
            title_id in known_titles
            or package.get(
                "matching_base_metadata_found"
            ) is True
        )

        installed = package.get(
            "installed_directory_evidence", {}
        )

        for (digest, size), sources in sorted(
            variants.items()
        ):
            blockers = [
                "CONTENT_TYPE_NOT_VERIFIED",
                "PKG_COMPATIBILITY_NOT_VERIFIED",
                "EMULATOR_INSTALL_STATUS_NOT_VERIFIED",
            ]

            if not base_resolved:
                blockers.append(
                    "BASE_GAME_IDENTITY_NOT_VERIFIED"
                )

            if not all(
                source["present_at_reconciliation"]
                for source in sources
            ):
                blockers.append(
                    "SOURCE_MISSING_OR_SIZE_CHANGED"
                )

            records.append({
                "emulator": "rpcs3",
                "system": "ps3",
                "title_id": title_id,
                "content_id": package["content_id"],
                "content_kind": "unknown",
                "base_game_identity_verified": base_resolved,

                "source_group": {
                    "sha256_at_last_full_read": digest,
                    "size_bytes": size,
                    "source_count": len(sources),
                    "sources": sources,
                    "hash_confirmed_identical_at_last_read":
                        len(sources) > 1,
                },

                "other_hash_variants_exist":
                    len(variants) > 1,

                "installed_directory_present": bool(
                    installed.get("directory_exists")
                ),

                "installed": None,
                "active": None,
                "compatibility_verified": False,
                "installation_allowed": False,
                "blockers": blockers,
            })

    return records


def reconcile_cemu(scan, inventory):
    bases = {
        record["title_id"].lower()
        for record in inventory["cemu"]["content_directories"]
        if (
            record.get("kind") == "base_game"
            and record.get("meta_xml_present")
            and not record.get("metadata_conflict")
        )
    }

    records = []

    for candidate in scan["candidates"]:
        if candidate["format"] not in {
            "wiiu_meta",
            "wiiu_container"
        }:
            continue

        title_id = str(
            candidate.get("title_id", "")
        ).lower()

        valid_id = bool(WIIU_ID.fullmatch(title_id))

        kind = (
            WIIU_KINDS.get(title_id[:8], "unknown")
            if valid_id
            else "unknown"
        )

        base_id = (
            "00050000" + title_id[8:]
            if valid_id
            else None
        )

        base_verified = (
            base_id in bases
            if base_id
            else False
        )

        blockers = [
            "SOURCE_NOT_FULLY_HASHED",
            "CONTENT_COMPATIBILITY_NOT_VERIFIED",
            "EMULATOR_INSTALL_STATUS_NOT_VERIFIED",
        ]

        if not base_verified:
            blockers.append(
                "BASE_GAME_IDENTITY_NOT_VERIFIED"
            )

        records.append({
            "emulator": "cemu",
            "system": "wiiu",
            "title_id": title_id or None,
            "base_title_id": base_id,
            "content_kind": kind,
            "source": candidate["source"],
            "base_game_identity_verified": base_verified,
            "source_sha256_verified": False,
            "installed": None,
            "active": None,
            "compatibility_verified": False,
            "installation_allowed": False,
            "blockers": blockers,
        })

    return records


def main():
    def read(filename):
        return json.loads(
            (BASE / filename).read_text(
                encoding="utf-8"
            )
        )

    integrity = read("ps3-package-integrity.json")
    inventory = read("identity-inventory.json")
    scan = read("candidate-scan-fast.json")
    registry = read("adapter-registry.json")

    if not (
        integrity.get("read_only") is True
        and integrity.get("installation_enabled") is False
        and scan.get("read_only") is True
        and registry.get("adapter_status") == "discovery_only"
    ):
        raise ValueError(
            "Unexpected installation-enabled input"
        )

    records = (
        reconcile_ps3(integrity, inventory)
        + reconcile_cemu(scan, inventory)
    )

    output = {
        "schema_version": 1,
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "records": records,
    }

    destination = BASE / "unified-evidence.json"

    with destination.open(
        "x", encoding="utf-8"
    ) as handle:
        json.dump(
            output,
            handle,
            indent=2,
            ensure_ascii=False
        )
        handle.write("\n")

    print("===== GCM V3.6 SHARED EVIDENCE LEDGER =====")
    print("Candidate variants:", len(records))

    for record in records:
        print()
        print("Emulator:", record["emulator"])
        print("Title ID:", record.get("title_id"))
        print("Content type:", record["content_kind"])

        group = record.get("source_group")

        if group:
            print(
                "Copies with matching SHA256:",
                group["source_count"]
            )
            print(
                "Last full-read SHA256:",
                group["sha256_at_last_full_read"]
            )
            print(
                "All source paths present at expected sizes:",
                all(
                    source["present_at_reconciliation"]
                    for source in group["sources"]
                )
            )

        print(
            "Base identity confirmed:",
            record["base_game_identity_verified"]
        )
        print("Installed:", record["installed"])
        print("Active:", record["active"])
        print(
            "Installation allowed:",
            record["installation_allowed"]
        )
        print(
            "Outstanding evidence:",
            ", ".join(record["blockers"])
        )

    print()
    print("Report:", destination)
    print(
        "PASS: No source files copied, "
        "modified, installed, or deleted"
    )


if __name__ == "__main__":
    main()
