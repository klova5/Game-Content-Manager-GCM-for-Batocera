"""GCM v3.6: read-only multi-emulator content discovery."""

import json
import os
from collections import deque
from pathlib import Path

BASE = Path(__file__).resolve().parent

PLATFORMS = {
    "vita3k": {
        "system": "psvita",
        "extensions": {".vpk", ".pkg"},
        "storage": [
            "/userdata/saves/psvita",
            "/userdata/saves/psvita/ux0/app",
            "/userdata/saves/psvita/ux0/patch",
            "/userdata/saves/psvita/ux0/addcont",
            "/userdata/system/configs/vita3k",
            "/userdata/system/configs/Vita3K",
        ],
    },
    "azahar": {
        "system": "3ds",
        "extensions": {".cia"},
        "storage": [
            "/userdata/saves/3ds/Citra",
            "/userdata/saves/3ds/citra-emu",
            "/userdata/system/configs/azahar",
        ],
    },
    "xenia-edge": {
        "system": "xbox360",
        "extensions": {".tu", ".xex"},
        "storage": [
            "/userdata/system/configs/Xenia",
            "/userdata/saves/xbox360",
        ],
    },
    "ppsspp": {
        "system": "psp",
        "extensions": {".pbp", ".edat"},
        "storage": [
            "/userdata/saves/psp/PSP/GAME",
            "/userdata/saves/psp/PSP/SAVEDATA",
            "/userdata/saves/psp/PSP/TEXTURES",
        ],
    },
    "shadps4": {
        "system": "ps4",
        "extensions": {".pkg"},
        "storage": [
            "/userdata/system/configs/shadps4/user",
            "/userdata/saves/ps4",
        ],
    },
}

ROM_ROOTS = (
    Path("/userdata/roms"),
    Path("/media/easystore/emulators/roms"),
)

MAX_DIRS = 5000
MAX_FILES = 50000
MAX_CANDIDATES = 100


def sample_storage(path):
    result = {
        "path": str(path),
        "exists": path.is_dir() and not path.is_symlink(),
        "sample_entries": [],
    }

    if not result["exists"]:
        return result

    try:
        with os.scandir(path) as entries:
            names = []
            for entry in entries:
                if entry.is_symlink():
                    continue
                names.append(entry.name)
                if len(names) >= 25:
                    break
            result["sample_entries"] = sorted(names)
    except OSError:
        result["read_error"] = True

    return result


def scan_roms(system, extensions):
    records = []
    statistics = []

    for parent in ROM_ROOTS:
        root = parent / system
        if not root.is_dir() or root.is_symlink():
            continue

        directories = 0
        files = 0
        errors = 0
        truncated = False

        queue = deque([(root, 0)])
        visited = set()

        while queue:
            if directories >= MAX_DIRS or files >= MAX_FILES:
                truncated = True
                break

            folder, depth = queue.popleft()

            try:
                if folder.is_symlink():
                    continue

                info = folder.stat()
                key = (info.st_dev, info.st_ino)

                if key in visited:
                    continue

                visited.add(key)
                directories += 1

                with os.scandir(folder) as entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink():
                                continue

                            if entry.is_dir(follow_symlinks=False):
                                if depth < 5:
                                    queue.append(
                                        (Path(entry.path), depth + 1)
                                    )
                                continue

                            if not entry.is_file(follow_symlinks=False):
                                continue

                            files += 1

                            if files >= MAX_FILES:
                                truncated = True
                                break

                            suffix = Path(entry.name).suffix.lower()

                            if suffix not in extensions:
                                continue

                            if len(records) >= MAX_CANDIDATES:
                                truncated = True
                                continue

                            records.append({
                                "source": entry.path,
                                "extension": suffix,
                                "format_hint_only": True,
                                "title_id": None,
                                "content_kind": "unknown",
                                "compatibility_verified": False,
                                "installed": None,
                                "active": None,
                                "installation_allowed": False,
                            })

                        except OSError:
                            errors += 1

            except OSError:
                errors += 1

        statistics.append({
            "root": str(root),
            "directories_examined": directories,
            "files_examined": files,
            "errors": errors,
            "scan_incomplete": truncated or errors > 0,
        })

    return records, statistics


def main():
    registry = json.loads(
        (BASE / "adapter-registry.json").read_text()
    )

    assert registry["adapter_status"] == "discovery_only"

    registered = {
        row["id"] for row in registry["emulators"]
    }

    result = {
        "schema_version": 1,
        "read_only": True,
        "installation_enabled": False,
        "platforms": {},
    }

    print("===== GCM V3.6 MULTI-EMULATOR DISCOVERY =====")

    for emulator, spec in PLATFORMS.items():
        assert emulator in registered

        system = spec["system"]

        roots = [
            sample_storage(Path(p))
            for p in spec["storage"]
        ]

        candidates, statistics = scan_roms(
            system, spec["extensions"]
        )

        for candidate in candidates:
            assert candidate["installation_allowed"] is False
            assert candidate["installed"] is None
            assert candidate["active"] is None
            assert candidate["compatibility_verified"] is False

        result["platforms"][emulator] = {
            "system": system,
            "storage_roots": roots,
            "rom_scan": statistics,
            "candidates": candidates,
            "classification": "discovery_only",
        }

        print()
        print("EMULATOR:", emulator)
        print("System:", system)

        found = [r for r in roots if r["exists"]]

        print("Storage roots found:", len(found))

        for root in found:
            print("  ", root["path"])
            for name in root["sample_entries"][:12]:
                print("     ", name)

        print("Content-format candidates:", len(candidates))

        for item in candidates[:15]:
            print("  ", item["source"])

        if len(candidates) > 15:
            print(
                "  Additional candidates:",
                len(candidates) - 15
            )

        for scan in statistics:
            print(
                "  Scan:",
                scan["directories_examined"], "directories,",
                scan["files_examined"], "files,",
                scan["errors"], "errors,",
                "incomplete =", scan["scan_incomplete"]
            )

        print("Installed status: NOT VERIFIED")
        print("Installation operations: DISABLED")

    output = BASE / "multi-emulator-discovery.json"

    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    print()
    print("===== DISCOVERY SUMMARY =====")
    print("Additional emulator families:", len(PLATFORMS))
    print(
        "Combined adapter registry:",
        len(registry["emulators"])
    )
    print("Report:", output)
    print("No emulator settings changed")
    print("No game content copied or deleted")
    print("No installations performed")


if __name__ == "__main__":
    main()
