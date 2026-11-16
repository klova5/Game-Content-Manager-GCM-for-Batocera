"""GCM v3.6 shadPS4 DLC discovery and status evidence.

Read-only. Never installs, extracts, moves or deletes content.
"""

import json
import re
import tomllib
from collections import defaultdict
from pathlib import Path

from shadps4_metadata_matcher import parse_sfo, identity

BASE = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/shadps4/user/config.toml")
OUTPUT = BASE / "shadps4-dlc-status.json"

CUSA = re.compile(r"^CUSA[0-9]{5}$")
MAX_SFO = 2 * 1024 * 1024


def read_dlc_root():
    if CONFIG.is_symlink() or not CONFIG.is_file():
        raise RuntimeError("shadPS4 config unavailable")

    with CONFIG.open("rb") as stream:
        config = tomllib.load(stream)

    value = config.get("GUI", {}).get("addonInstallDir")

    if not isinstance(value, str):
        raise RuntimeError("DLC installation path not configured")

    path = Path(value)

    if not path.is_absolute():
        raise RuntimeError("DLC installation path is not absolute")

    return path


def inspect_dlc_folder(folder, title_hint=None):
    """Read metadata from an extracted DLC folder, if available."""

    result = {
        "directory": str(folder),
        "parent_title_hint": title_hint,
        "sfo_present": False,
        "sfo_parsed": False,
        "title_id": None,
        "content_id": None,
        "category": None,
        "metadata_consistent": False,
    }

    sce_sys = folder / "sce_sys"
    sfo = sce_sys / "param.sfo"

    if (
        folder.is_symlink()
        or sce_sys.is_symlink()
        or sfo.is_symlink()
        or not sfo.is_file()
    ):
        return result

    result["sfo_present"] = True

    try:
        size = sfo.stat().st_size

        if size < 20 or size > MAX_SFO:
            return result

        metadata = parse_sfo(sfo.read_bytes())
    except (OSError, ValueError):
        return result

    if metadata is None:
        return result

    details = identity(metadata)

    title_id = details["title_id"]
    category = details["category"]

    result.update({
        "sfo_parsed": True,
        "title_id": title_id,
        "content_id": str(
            metadata.get("CONTENT_ID") or ""
        ),
        "category": category,
        "metadata_consistent": bool(
            title_id
            and CUSA.fullmatch(title_id)
            and not details["identity_conflict"]
            and category == "ac"
            and (
                title_hint is None
                or title_hint == title_id
            )
        ),
    })

    return result


def discover_extracted(root):
    records = []
    root_entries = []

    if root.is_symlink() or not root.is_dir():
        return records, root_entries

    # Inspect root/CUSAxxxxx and root/CUSAxxxxx/DLC_FOLDER.
    # Also recognize a direct DLC folder carrying metadata.
    for folder in sorted(root.iterdir(), key=lambda p: p.name)[:300]:
        if folder.is_symlink():
            continue

        root_entries.append(folder.name)

        if not folder.is_dir():
            continue

        title_hint = (
            folder.name.upper()
            if CUSA.fullmatch(folder.name.upper())
            else None
        )

        records.append(
            inspect_dlc_folder(folder, title_hint)
        )

        if title_hint is None:
            continue

        try:
            children = sorted(
                folder.iterdir(),
                key=lambda p: p.name
            )[:200]
        except OSError:
            continue

        for child in children:
            if child.is_dir() and not child.is_symlink():
                records.append(
                    inspect_dlc_folder(child, title_hint)
                )

    return records, root_entries


def metadata_match(package, extracted):
    """Require exact title, content ID, category and consistent SFO."""

    return bool(
        extracted["sfo_parsed"]
        and extracted["metadata_consistent"]
        and extracted["category"] == "ac"
        and extracted["title_id"] == package["title_id"]
        and extracted["content_id"] == package["content_id"]
        and package["content_id"]
    )


def main():
    metadata = json.loads(
        (BASE / "shadps4-metadata-matches.json").read_text()
    )

    matrix = json.loads(
        (BASE / "shadps4-compatibility-matrix.json").read_text()
    )

    assert metadata["read_only"] is True
    assert metadata["installation_enabled"] is False
    assert matrix["read_only"] is True
    assert matrix["installation_enabled"] is False

    root = read_dlc_root()

    extracted, entries = discover_extracted(root)

    targets = {
        entry["title_id"]
        for entry in matrix["installed_title_evidence"]
        if entry["target_identity_verified"]
    }

    packages = [
        row for row in metadata["packages"]
        if row["metadata_content_kind"] == "dlc"
    ]

    assert len(packages) == 32, (
        "DLC package count changed; review inventory first"
    )

    # Group likely duplicate package sources using previously
    # parsed metadata and package size. No full hashing here.
    groups = defaultdict(list)

    for package in packages:
        title_id = package["resolved_title_id"]
        sfo = package.get("pkg_sfo") or {}
        content_id = str(sfo.get("CONTENT_ID") or "")

        key = (
            title_id,
            content_id,
            package.get("size_bytes"),
        )

        groups[key].append(package)

    results = []

    for (title_id, content_id, size), copies in sorted(groups.items()):
        sample = copies[0]

        package_identity_valid = all(
            row["resolved_title_id"] == title_id
            and row["header_title_id"] == title_id
            and row["pkg_sfo_status"] == "parsed"
            and row["package_category_verified"] is True
            and not row["metadata_conflict"]
            and str(
                (row.get("pkg_sfo") or {}).get("CONTENT_ID") or ""
            ) == content_id
            for row in copies
        )

        candidate = {
            "title_id": title_id,
            "content_id": content_id,
        }

        matching = [
            row for row in extracted
            if metadata_match(candidate, row)
        ]

        unverified_folders = [
            row for row in extracted
            if row["parent_title_hint"] == title_id
            and not row["metadata_consistent"]
        ]

        target_found = title_id in targets

        if not package_identity_valid:
            assessment = "PACKAGE_IDENTITY_REQUIRES_REVIEW"
        elif not target_found:
            assessment = "NO_MATCHING_VERIFIED_GAME_TARGET"
        elif matching:
            assessment = "MATCHING_DLC_METADATA_PRESENT"
        elif root.is_dir() and not root.is_symlink():
            assessment = "NO_MATCHING_DLC_METADATA_DETECTED"
        else:
            assessment = "DLC_ROOT_UNAVAILABLE"

        results.append({
            "emulator": "shadps4",
            "system": "ps4",
            "title_id": title_id,
            "content_id": content_id,
            "content_kind": "dlc",
            "package_size_bytes": size,
            "source_count": len(copies),
            "sources": [
                row["source"] for row in copies
            ],
            "possible_duplicate_sources": len(copies) > 1,
            "full_file_equality_verified": False,
            "package_identity_valid": package_identity_valid,
            "matching_game_target_identified": target_found,
            "matching_extracted_metadata_paths": [
                row["directory"] for row in matching
            ],
            "unverified_folder_count": len(unverified_folders),
            "assessment": assessment,
            "installed": None,
            "active": None,
            "installation_allowed": False,
        })

    report = {
        "schema_version": 1,
        "read_only": True,
        "installation_enabled": False,
        "configured_dlc_root": str(root),
        "dlc_root_available": (
            root.is_dir() and not root.is_symlink()
        ),
        "root_entry_names": entries[:100],
        "extracted_folder_evidence": extracted,
        "dlc_variants": results,
    }

    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    print("===== SHADPS4 DLC ROOT =====")
    print("Configured path:", root)
    print("Directory available:", report["dlc_root_available"])
    print("Top-level entries:", len(entries))
    print("Extracted folders examined:", len(extracted))

    for name in entries[:25]:
        print("  ", name)

    print()
    print("===== EXTRACTED DLC METADATA =====")

    print(
        "Readable SFO records:",
        sum(row["sfo_parsed"] for row in extracted)
    )

    print(
        "Consistent DLC metadata:",
        sum(row["metadata_consistent"] for row in extracted)
    )

    for row in extracted:
        if row["sfo_parsed"]:
            print(
                row["title_id"],
                row["category"],
                row["content_id"],
                "|",
                row["directory"]
            )

    print()
    print("===== DLC PACKAGE MATCHING =====")
    print("Original package records:", len(packages))
    print("Possible distinct DLC variants:", len(results))

    for title_id in sorted({r["title_id"] for r in results}):
        items = [
            row for row in results
            if row["title_id"] == title_id
        ]

        print()
        print("Title ID:", title_id)
        print("DLC variants:", len(items))
        print("Package sources:", sum(r["source_count"] for r in items))
        print("Matching extracted game:", title_id in targets)
        print(
            "DLC metadata matches:",
            sum(
                bool(r["matching_extracted_metadata_paths"])
                for r in items
            )
        )

        for item in items[:12]:
            print(
                " ",
                item["content_id"],
                "|",
                item["assessment"]
            )

    assert all(
        r["installed"] is None
        and r["active"] is None
        and r["installation_allowed"] is False
        for r in results
    )

    print()
    print("===== SAFETY =====")
    print("Package source hashes: NOT RECHECKED")
    print("Installed/active status: UNVERIFIED")
    print("Installation: DISABLED")
    print("Original DLC packages modified: NO")
    print("Report:", OUTPUT)


if __name__ == "__main__":
    main()
