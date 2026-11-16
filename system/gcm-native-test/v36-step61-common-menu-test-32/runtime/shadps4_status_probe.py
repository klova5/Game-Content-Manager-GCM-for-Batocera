"""GCM v3.6 shadPS4 merged-update status adapter.

Read-only. Does not install, activate, copy, or delete content.
"""

import json
import stat
import sys
import tomllib

from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = Path(
    "/userdata/system/configs/shadps4/user/config.toml"
)

OVERRIDES = Path(
    "/userdata/system/configs/shadps4/user/custom_configs"
)

OUTPUT = BASE / "shadps4-update-status.json"

sys.path.insert(0, str(BASE))

from shadps4_installed_update_resolver import (
    read_extracted_target,
    compare_update,
)


def read_config():
    if CONFIG.is_symlink() or not CONFIG.is_file():
        raise RuntimeError("shadPS4 config unavailable")

    with CONFIG.open("rb") as stream:
        data = tomllib.load(stream)

    general = data.get("General", {})
    gui = data.get("GUI", {})

    separate = general.get("separateUpdateEnabled")

    if type(separate) is not bool:
        raise RuntimeError(
            "Separate-update setting is missing or invalid"
        )

    roots = gui.get("installDirs", [])

    if not isinstance(roots, list):
        raise RuntimeError("Unexpected installDirs format")

    paths = []

    for root in roots:
        if not isinstance(root, str):
            continue

        path = Path(root)

        if path.is_absolute():
            paths.append(path)

    return separate, paths


def per_game_setting(title_id, global_value):
    path = OVERRIDES / (title_id + ".json")

    if not path.exists():
        return global_value, "global"

    if path.is_symlink() or not path.is_file():
        return None, "override_unreadable"

    try:
        if path.stat().st_size > 256000:
            return None, "override_too_large"

        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None, "override_unreadable"

    general = data.get("General", {})

    if not isinstance(general, dict):
        return None, "override_invalid"

    if "separateUpdateEnabled" not in general:
        return global_value, "global_with_override_file"

    value = general["separateUpdateEnabled"]

    if type(value) is not bool:
        return None, "override_invalid"

    return value, "game_override"


def evaluate(variant, target, separate, registered):
    """Return evidence status without claiming runtime activity."""

    if separate is None:
        return "CONFIGURATION_UNVERIFIED"

    if separate is True:
        return "SEPARATE_UPDATE_MODE_REQUIRES_ADAPTER"

    if target is None:
        return "NO_EXTRACTED_TARGET"

    if not registered:
        return "TARGET_OUTSIDE_CONFIGURED_INSTALL_ROOT"

    if not target["title_identity_verified"]:
        return "TARGET_IDENTITY_UNVERIFIED"

    if not target["extracted_patch_metadata_present"]:
        return "EXTRACTED_PATCH_METADATA_NOT_FOUND"

    if not target["eboot_present"]:
        return "PATCH_EXECUTABLE_MISSING"

    match = compare_update(variant, target)

    if not match["patch_version_match"]:
        return "PATCH_VERSION_NOT_MATCHED"

    if match["content_id_match"] is not True:
        return "PATCH_CONTENT_ID_NOT_MATCHED"

    return "MATCHING_PATCH_PRESENT"


def main():
    with (BASE / "shadps4-update-integrity.json").open() as f:
        integrity = json.load(f)

    with (BASE / "shadps4-compatibility-matrix.json").open() as f:
        matrix = json.load(f)

    assert integrity["read_only"] is True
    assert integrity["installation_enabled"] is False
    assert matrix["read_only"] is True
    assert matrix["installation_enabled"] is False

    separate, install_roots = read_config()

    targets = {}

    for entry in matrix["installed_title_evidence"]:
        title_id = entry["title_id"]

        if title_id in targets:
            raise RuntimeError("Duplicate extracted target")

        targets[title_id] = read_extracted_target(entry)

    records = []

    for variant in integrity["variants"]:
        title_id = variant["title_id"]

        setting, setting_source = per_game_setting(
            title_id, separate
        )

        target = targets.get(title_id)

        registered = (
            target is not None
            and Path(target["directory"]).parent in install_roots
        )

        status = evaluate(
            variant,
            target,
            setting,
            registered,
        )

        sources = variant["source_files"]

        # These hashes were established by Step 99.
        # They are historical evidence, not a new hash check.
        matching_hashes = (
            len(sources) == 2
            and len({s["sha256"] for s in sources}) == 1
            and variant["full_file_equality_verified"] is True
        )

        record = {
            "emulator": "shadps4",
            "system": "ps4",
            "title_id": title_id,
            "content_kind": "update",
            "package_version": variant["version"],
            "content_id": variant["content_id"],
            "effective_separate_update_enabled": setting,
            "setting_source": setting_source,
            "target_directory": (
                target["directory"] if target else None
            ),
            "extracted_version": (
                target["version"] if target else None
            ),
            "target_registered": registered,
            "status": status,
            "matching_patch_present": (
                status == "MATCHING_PATCH_PRESENT"
            ),
            "last_hash_sources_identical": matching_hashes,
            "installed": None,
            "active": None,
            "base_installation_verified": False,
            "automatic_reinstallation_allowed": False,
            "installation_enabled": False,
        }

        records.append(record)

    output = {
        "schema_version": 1,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "read_only": True,
        "installation_enabled": False,
        "global_separate_update_enabled": separate,
        "configured_install_roots": [
            str(root) for root in install_roots
        ],
        "updates": records,
    }

    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")

    print("===== SHADPS4 UPDATE STATUS =====")
    print("Separate update mode:", separate)
    print("Configured game roots:", len(install_roots))

    for record in records:
        print()
        print("Title ID:", record["title_id"])
        print("Update version:", record["package_version"])
        print("Configuration:", record["setting_source"])
        print("Assessment:", record["status"])
        print(
            "Matching extracted patch:",
            record["matching_patch_present"]
        )
        print(
            "Previously verified source pair:",
            record["last_hash_sources_identical"]
        )
        print("ACTIVE: NOT VERIFIED")
        print("Automatic reinstall: DISABLED")

    assert all(
        row["installed"] is None
        and row["active"] is None
        and row["automatic_reinstallation_allowed"] is False
        for row in records
    )

    print()
    print("===== SUMMARY =====")
    print("Update variants:", len(records))
    print(
        "Matching extracted patches:",
        sum(
            row["matching_patch_present"]
            for row in records
        )
    )
    print("Runtime-active confirmations: 0")
    print("Installation operations enabled: NO")
    print("Report:", OUTPUT)
    print("No emulator or game files modified")


if __name__ == "__main__":
    main()
