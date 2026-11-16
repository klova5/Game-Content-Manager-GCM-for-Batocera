"""GCM v3.6: read-only shadPS4 compatibility evidence."""

import hashlib
import json
import re
import stat
from collections import Counter, defaultdict
from pathlib import Path

from shadps4_metadata_matcher import parse_sfo, identity

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "shadps4-compatibility-matrix.json"

CUSA = re.compile(r"^CUSA[0-9]{5}$")
KINDS = {"gd": "base_game", "gp": "update", "ac": "dlc"}


def installed_target(row):
    """Verify the identity of an extracted title directory."""
    path = Path(row["directory"])
    title_id = row["folder_title_id"]
    sfo = path / "sce_sys/param.sfo"

    result = {
        "title_id": title_id,
        "directory": str(path),
        "metadata_category": None,
        "version": None,
        "target_identity_verified": False,
        "base_installation_verified": False,
        "installed_update_verified": False,
        "active": None,
        "eboot_present": False,
    }

    if (
        path.is_symlink()
        or sfo.is_symlink()
        or not sfo.is_file()
    ):
        return result

    try:
        if sfo.stat().st_size > 2_000_000:
            return result

        meta = parse_sfo(sfo.read_bytes())
    except (OSError, ValueError):
        return result

    if meta is None:
        return result

    details = identity(meta)
    category = details["category"]

    confirmed = (
        bool(CUSA.fullmatch(title_id))
        and details["title_id"] == title_id
        and not details["identity_conflict"]
        and category in ("gd", "gp")
    )

    result.update({
        "metadata_category": category,
        "version": details["version"],
        "target_identity_verified": confirmed,
        "eboot_present": (path / "eboot.bin").is_file(),
    })

    # A matching SFO identifies the extracted title.
    # It does not independently prove a complete base
    # installation or which update is currently active.
    return result


def inspect_source(row, header_record):
    path = Path(row["source"])

    result = {
        "path": str(path),
        "present": False,
        "expected_size": row["size_bytes"],
        "current_size": None,
        "header_sha256": None,
        "current_header_valid": False,
        "header_content_id_matches_metadata": False,
    }

    try:
        info = path.lstat()

        if not stat.S_ISREG(info.st_mode):
            return result

        result["present"] = True
        result["current_size"] = info.st_size

        if info.st_size != row["size_bytes"]:
            return result

        with path.open("rb") as stream:
            header = stream.read(4096)

        if len(header) != 4096 or header[:4] != b"\x7fCNT":
            return result

        current_content_id = header[0x40:0x64].split(
            b"\x00", 1
        )[0].decode("ascii", "replace")

        expected_content_id = (
            (row.get("pkg_sfo") or {}).get("CONTENT_ID")
            or header_record.get("content_id")
            or ""
        )

        result["header_sha256"] = hashlib.sha256(
            header
        ).hexdigest()

        result["current_header_valid"] = True

        result["header_content_id_matches_metadata"] = (
            bool(expected_content_id)
            and current_content_id == expected_content_id
        )

        return result

    except OSError:
        return result


def evaluate_match(title_id, kind, targets):
    if kind == "base_game":
        return "BASE_PACKAGE_SOURCE_ONLY", False

    target = targets.get(title_id)

    if target and target["target_identity_verified"]:
        return "TARGET_TITLE_ID_MATCH", True

    return "NO_VERIFIED_TARGET_TITLE", False


def main():
    old = json.loads(
        (BASE / "shadps4-metadata-matches.json").read_text()
    )
    headers = json.loads(
        (BASE / "shadps4-package-inventory.json").read_text()
    )

    assert old["read_only"] is True
    assert old["installation_enabled"] is False
    assert headers["read_only"] is True
    assert headers["installation_enabled"] is False

    original_headers = {
        item["source"]: item
        for item in headers["packages"]
    }

    targets = {}

    for row in old["installed_base_games"]:
        target = installed_target(row)
        targets[target["title_id"]] = target

    packages = []
    groups = defaultdict(list)

    for row in old["packages"]:
        header_record = original_headers.get(
            row["source"]
        )

        if header_record is None:
            raise ValueError("Missing header inventory record")

        meta = row.get("pkg_sfo") or {}
        title_id = row.get("resolved_title_id")
        category = str(meta.get("CATEGORY", "")).lower()
        kind = KINDS.get(category, "unknown")
        content_id = str(meta.get("CONTENT_ID") or "")
        version = str(meta.get("APP_VER") or "")

        source = inspect_source(row, header_record)

        sfo_title = str(
            meta.get("TITLE_ID") or ""
        ).upper()

        sfo_identity_valid = (
            not sfo_title
            or sfo_title == title_id
        )

        identity_valid = (
            bool(title_id)
            and bool(CUSA.fullmatch(title_id))
            and row["pkg_sfo_status"] == "parsed"
            and row["package_category_verified"]
            and row["header_title_id"] == title_id
            and not row["metadata_conflict"]
            and sfo_identity_valid
            and source["current_header_valid"]
            and source["header_content_id_matches_metadata"]
        )

        match_status, target_found = evaluate_match(
            title_id, kind, targets
        )

        blockers = [
            "FULL_PACKAGE_SHA256_NOT_VERIFIED",
            "PACKAGE_AUTHENTICITY_NOT_VERIFIED",
            "EMULATOR_INSTALLATION_NOT_VALIDATED",
            "INSTALLED_AND_ACTIVE_STATUS_UNVERIFIED",
        ]

        if not identity_valid:
            blockers.append(
                "PACKAGE_IDENTITY_REQUIRES_REVIEW"
            )

        if kind == "unknown":
            blockers.append(
                "PACKAGE_TYPE_UNKNOWN"
            )

        if kind in ("update", "dlc") and not target_found:
            blockers.append(
                "MATCHING_EXTRACTED_TARGET_NOT_VERIFIED"
            )

        if kind in ("update", "dlc"):
            blockers.append(
                "BASE_INSTALLATION_NOT_VERIFIED"
            )

        record = {
            "source": row["source"],
            "title_id": title_id,
            "content_id": content_id,
            "version": version,
            "content_kind": kind,
            "identity_valid": identity_valid,
            "match_status": match_status,
            "target_title_identity_verified": target_found,
            "source_evidence": source,
            "full_sha256_verified": False,
            "compatibility_verified": False,
            "installed": None,
            "active": None,
            "installation_allowed": False,
            "blockers": blockers,
        }

        packages.append(record)

        # Same metadata and 4 KB fingerprint indicate
        # possible duplicates, NOT full-file equality.
        if (
            identity_valid
            and source["header_sha256"]
            and content_id
        ):
            key = (
                title_id,
                kind,
                content_id,
                version,
                source["current_size"],
                source["header_sha256"],
            )
        else:
            key = ("UNRESOLVED", row["source"])

        groups[key].append(record)

    grouped = []

    for key, rows in groups.items():
        grouped.append({
            "title_id": rows[0]["title_id"],
            "content_kind": rows[0]["content_kind"],
            "content_id": rows[0]["content_id"],
            "version": rows[0]["version"],
            "source_count": len(rows),
            "possible_duplicate_sources": len(rows) > 1,
            "full_file_equality_verified": False,
            "sources": [r["source"] for r in rows],
        })

    result = {
        "schema_version": 1,
        "read_only": True,
        "installation_enabled": False,
        "installed_title_evidence": list(targets.values()),
        "packages": packages,
        "possible_duplicate_groups": grouped,
    }

    with OUTPUT.open(
        "x", encoding="utf-8"
    ) as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")

    print("===== EXTRACTED TITLE IDENTITIES =====")

    for title_id, target in sorted(targets.items()):
        print()
        print("Title:", title_id)
        print("Metadata category:", target["metadata_category"])
        print("Version:", target["version"])
        print(
            "Extracted target identity verified:",
            target["target_identity_verified"]
        )
        print("Executable present:", target["eboot_present"])
        print(
            "Base installation independently verified:",
            target["base_installation_verified"]
        )

    print()
    print("===== PACKAGE COMPATIBILITY MATRIX =====")

    for title_id in sorted({
        p["title_id"] for p in packages
    }):
        rows = [
            p for p in packages
            if p["title_id"] == title_id
        ]

        print()
        print("Title:", title_id)
        print("PKG records:", len(rows))
        print(
            "Content categories:",
            dict(Counter(p["content_kind"] for p in rows))
        )
        print(
            "Identity checks passed:",
            sum(p["identity_valid"] for p in rows)
        )
        print(
            "Matching extracted target:",
            sum(
                p["target_title_identity_verified"]
                for p in rows
            )
        )
        print(
            "Unresolved package identities:",
            sum(not p["identity_valid"] for p in rows)
        )

    print()
    print("===== SOURCE GROUPING =====")
    print("Individual source records:", len(packages))
    print("Candidate content variants:", len(grouped))
    print(
        "Groups with possible duplicate sources:",
        sum(
            g["possible_duplicate_sources"]
            for g in grouped
        )
    )
    print("Full-file equality proven by this step: NO")

    print()
    print("===== SAFETY ASSERTIONS =====")

    assert all(
        p["installation_allowed"] is False
        and p["installed"] is None
        and p["active"] is None
        for p in packages
    )

    # Even when an installed target has patch metadata,
    # no base installation or active version is inferred.
    assert all(
        t["base_installation_verified"] is False
        for t in targets.values()
    )

    print("PASS: Installation remains disabled")
    print("PASS: No active status inferred")
    print("PASS: Cross-title content kept separate")
    print("PASS: Source files preserved")
    print()
    print("Report:", OUTPUT)


if __name__ == "__main__":
    main()
