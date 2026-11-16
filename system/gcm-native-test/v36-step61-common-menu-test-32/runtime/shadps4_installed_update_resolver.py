"""GCM v3.6: read-only shadPS4 extracted-update evidence."""

import json
import re
import stat
from pathlib import Path

from shadps4_metadata_matcher import parse_sfo, identity

BASE = Path(__file__).resolve().parent
OUTPUT = BASE / "shadps4-installed-update-evidence.json"
GAME_ROOT = Path("/userdata/roms/ps4")
CUSA = re.compile(r"^CUSA[0-9]{5}$")


def read_extracted_target(target):
    title_id = target["title_id"]
    folder = Path(target["directory"])

    result = {
        "title_id": title_id,
        "directory": str(folder),
        "sfo_readable": False,
        "sfo_title_id": None,
        "category": None,
        "version": None,
        "content_id": None,
        "eboot_present": False,
        "title_identity_verified": False,
        "extracted_patch_metadata_present": False,
        "base_installation_verified": False,
        "installed": None,
        "active": None,
    }

    if not CUSA.fullmatch(title_id):
        return result

    if GAME_ROOT not in folder.parents:
        return result

    sfo = folder / "sce_sys/param.sfo"
    eboot = folder / "eboot.bin"

    if (
        folder.is_symlink()
        or sfo.is_symlink()
        or not sfo.is_file()
    ):
        return result

    try:
        info = sfo.stat()

        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size < 20
            or info.st_size > 2_000_000
        ):
            return result

        metadata = parse_sfo(sfo.read_bytes())
    except (OSError, ValueError):
        return result

    if not metadata:
        return result

    details = identity(metadata)

    valid_title = (
        details["title_id"] == title_id
        and not details["identity_conflict"]
    )

    category = str(
        metadata.get("CATEGORY") or ""
    ).strip().lower()

    version = str(
        metadata.get("APP_VER") or ""
    ).strip()

    content_id = str(
        metadata.get("CONTENT_ID") or ""
    ).strip()

    result.update({
        "sfo_readable": True,
        "sfo_title_id": details["title_id"],
        "category": category,
        "version": version,
        "content_id": content_id,
        "eboot_present": (
            eboot.is_file()
            and not eboot.is_symlink()
        ),
        "title_identity_verified": valid_title,
        "extracted_patch_metadata_present": (
            valid_title and category == "gp"
        ),
    })

    return result


def compare_update(package, target):
    title_id = package["title_id"]
    version = str(package["version"]).strip()
    content_id = str(package["content_id"]).strip()

    result = {
        "title_id": title_id,
        "package_version": version,
        "extracted_version": (
            target.get("version") if target else None
        ),
        "title_identity_match": False,
        "patch_version_match": False,
        "content_id_match": None,
        "status": "NO_EXTRACTED_TARGET",
    }

    if target is None:
        return result

    valid_identity = (
        target["title_identity_verified"]
        and target["title_id"] == title_id
    )

    result["title_identity_match"] = valid_identity

    if not valid_identity:
        result["status"] = "TARGET_IDENTITY_UNVERIFIED"
        return result

    if not target["extracted_patch_metadata_present"]:
        result["status"] = "EXTRACTED_TARGET_NOT_PATCH_METADATA"
        return result

    if not version or target["version"] != version:
        result["status"] = "EXTRACTED_PATCH_VERSION_DIFFERS"
        return result

    result["patch_version_match"] = True

    extracted_content_id = target.get("content_id") or ""

    if content_id and extracted_content_id:
        equal = content_id == extracted_content_id
        result["content_id_match"] = equal
        result["status"] = (
            "TITLE_VERSION_CONTENT_ID_MATCH"
            if equal
            else "TITLE_VERSION_MATCH_CONTENT_ID_DIFFERS"
        )
    else:
        result["status"] = (
            "TITLE_VERSION_MATCH_CONTENT_ID_UNAVAILABLE"
        )

    return result


def main():
    integrity = json.loads(
        (BASE / "shadps4-update-integrity.json").read_text()
    )
    matrix = json.loads(
        (BASE / "shadps4-compatibility-matrix.json").read_text()
    )

    assert integrity["read_only"] is True
    assert integrity["installation_enabled"] is False
    assert matrix["read_only"] is True
    assert matrix["installation_enabled"] is False

    targets = {}

    for record in matrix["installed_title_evidence"]:
        target = read_extracted_target(record)
        title_id = target["title_id"]

        if title_id in targets:
            raise ValueError(
                "Multiple extracted directories for " + title_id
            )

        targets[title_id] = target

    matches = []

    for variant in integrity["variants"]:
        assert variant["content_kind"] == "update"
        assert variant["installation_allowed"] is False
        assert variant["source_hashes_verified"] is True

        sources = variant["source_files"]
        hashes = {row["sha256"] for row in sources}

        if len(hashes) != 1:
            raise ValueError(
                "Update sources have different hashes"
            )

        comparison = compare_update(
            variant, targets.get(variant["title_id"])
        )

        matches.append({
            **comparison,
            "content_id": variant["content_id"],
            "source_count": len(sources),
            "last_verified_sha256": next(iter(hashes)),
            "sources_identical_at_last_hash": (
                len(sources) > 1
                and variant["full_file_equality_verified"]
            ),
            "extracted_patch_metadata_matches": (
                comparison["patch_version_match"]
                and comparison["content_id_match"] is True
            ),
            "package_contents_installed_verified": False,
            "base_installation_verified": False,
            "installed": None,
            "active": None,
            "installation_allowed": False,
        })

    report = {
        "schema_version": 1,
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "extracted_targets": list(targets.values()),
        "update_matches": matches,
    }

    with OUTPUT.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    print("===== EXTRACTED PS4 PATCH EVIDENCE =====")

    for title_id, target in sorted(targets.items()):
        print()
        print("Title:", title_id)
        print("SFO readable:", target["sfo_readable"])
        print("Category:", target["category"])
        print("Extracted version:", target["version"])
        print("Content ID:", target["content_id"] or "(absent)")
        print("EBOOT present:", target["eboot_present"])
        print(
            "Patch metadata identified:",
            target["extracted_patch_metadata_present"]
        )

    print()
    print("===== VERIFIED UPDATE SOURCE COMPARISONS =====")

    for match in matches:
        print()
        print("Title:", match["title_id"])
        print("Package version:", match["package_version"])
        print("Extracted version:", match["extracted_version"])
        print("Version match:", match["patch_version_match"])
        print("Content ID match:", match["content_id_match"])
        print("Assessment:", match["status"])
        print(
            "Original source copies identical:",
            match["sources_identical_at_last_hash"]
        )
        print("Installed status: UNVERIFIED")
        print("Active status: UNVERIFIED")

    assert all(
        match["installation_allowed"] is False
        and match["installed"] is None
        and match["active"] is None
        for match in matches
    )

    print()
    print("===== SUMMARY =====")
    print("Update variants:", len(matches))
    print(
        "Extracted title/version matches:",
        sum(m["patch_version_match"] for m in matches)
    )
    print(
        "Exact content ID matches:",
        sum(m["content_id_match"] is True for m in matches)
    )
    print(
        "Content ID differences:",
        sum(m["content_id_match"] is False for m in matches)
    )
    print("Installed/active status: NOT ESTABLISHED")
    print("Installation: DISABLED")
    print("Original packages modified: NO")
    print("Report:", OUTPUT)


if __name__ == "__main__":
    main()
