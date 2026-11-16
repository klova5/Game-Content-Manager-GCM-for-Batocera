"""GCM v3.6 read-only shadPS4 package and installation probe."""

import json
import re
import stat
from collections import Counter, defaultdict
from pathlib import Path

try:
    import tomllib
except ImportError:
    tomllib = None

BASE = Path(__file__).resolve().parent
REPORT = BASE / "shadps4-package-inventory.json"

CUSA = re.compile(r"CUSA[0-9]{5}", re.I)
PKG_MAGIC = b"\x7fCNT"

CONFIGS = (
    Path("/userdata/system/configs/shadps4/user/config.toml"),
    Path("/userdata/system/configs/shadps4/config.toml"),
)

PATH_KEYS = {
    "installdirs",
    "gameinstalldirs",
    "gameinstalldir",
    "addoninstalldir",
    "gamesdir",
    "addonsdir",
    "gameinstalldirectory",
}


def inspect_header(path):
    result = {
        "source": str(path),
        "format": "unknown",
        "header_valid": False,
        "embedded_title_id": None,
        "content_id": None,
        "package_type_verified": False,
        "compatibility_verified": False,
        "installation_allowed": False,
    }

    try:
        info = path.lstat()

        if not stat.S_ISREG(info.st_mode):
            result["error"] = "not_regular_file"
            return result

        result["size_bytes"] = info.st_size

        with path.open("rb") as stream:
            raw = stream.read(4096)

    except OSError as exc:
        result["error"] = type(exc).__name__
        return result

    if len(raw) < 4096:
        result["error"] = "short_header"
        return result

    if raw[:4] != PKG_MAGIC:
        result["error"] = "not_ps4_pkg_magic"
        return result

    result["format"] = "ps4_pkg_header"
    result["header_valid"] = True

    result["pkg_flags_hex"] = raw[4:8].hex()

    content_bytes = raw[0x40:0x64].split(
        b"\x00", 1
    )[0]

    try:
        content_id = content_bytes.decode("ascii")
    except UnicodeDecodeError:
        content_id = ""

    if content_id and all(
        32 <= ord(c) <= 126 for c in content_id
    ):
        result["content_id"] = content_id

        found = CUSA.findall(content_id)

        if len(found) == 1:
            result["embedded_title_id"] = found[0].upper()

    name_ids = sorted({
        match.upper()
        for match in CUSA.findall(path.name)
    })

    result["filename_title_ids"] = name_ids
    result["filename_conflict"] = bool(
        result["embedded_title_id"]
        and name_ids
        and result["embedded_title_id"] not in name_ids
    )

    directory_names = [
        part.lower() for part in path.parent.parts
    ]

    if any(
        part in ("updates", "update", "patches")
        for part in directory_names
    ):
        hint = "update"
    elif any(
        "dlc" in part or "add-on" in part
        for part in directory_names
    ):
        hint = "dlc"
    else:
        hint = "unknown"

    result["directory_content_hint"] = hint

    # Hints and PKG magic cannot establish installability.
    result["content_kind"] = "unverified"
    result["installed"] = None
    result["active"] = None

    return result


def configured_paths():
    reports = []
    paths = set()

    for path in CONFIGS:
        entry = {
            "config": str(path),
            "exists": path.is_file(),
            "path_keys": [],
        }

        if not path.is_file():
            reports.append(entry)
            continue

        if tomllib is None:
            entry["parse_status"] = "tomllib_unavailable"
            reports.append(entry)
            continue

        try:
            with path.open("rb") as stream:
                config = tomllib.load(stream)
        except (OSError, ValueError) as exc:
            entry["parse_status"] = type(exc).__name__
            reports.append(entry)
            continue

        def walk(obj):
            if not isinstance(obj, dict):
                return

            for key, value in obj.items():
                normalized = (
                    key.lower().replace("_", "")
                )

                if isinstance(value, dict):
                    walk(value)
                    continue

                if normalized not in PATH_KEYS:
                    continue

                entry["path_keys"].append(key)

                values = (
                    value if isinstance(value, list)
                    else [value]
                )

                for candidate in values:
                    if not isinstance(candidate, str):
                        continue

                    candidate = candidate.strip()

                    if candidate.startswith("/"):
                        paths.add(candidate)

        walk(config)
        entry["parse_status"] = "parsed"
        reports.append(entry)

    return reports, sorted(paths)


def installed_folder_probe(paths):
    records = []

    for name in paths:
        root = Path(name)

        record = {
            "configured_path": name,
            "directory_exists": root.is_dir(),
            "game_folders": [],
        }

        if not root.is_dir():
            records.append(record)
            continue

        try:
            children = sorted(
                root.iterdir(),
                key=lambda p: p.name.lower()
            )
        except OSError:
            records.append(record)
            continue

        for child in children[:1000]:
            if child.is_symlink() or not child.is_dir():
                continue

            matched = CUSA.search(child.name)

            if not matched:
                continue

            sfo = child / "sce_sys/param.sfo"

            record["game_folders"].append({
                "directory": str(child),
                "title_id_hint": matched.group().upper(),
                "param_sfo_present": sfo.is_file(),
                "installed": None,
                "active": None,
            })

        records.append(record)

    return records


def main():
    data = json.loads(
        (BASE / "multi-emulator-discovery.json")
        .read_text()
    )

    assert data["read_only"] is True
    assert data["installation_enabled"] is False

    source_records = data["platforms"][
        "shadps4"
    ]["candidates"]

    packages = [
        inspect_header(Path(row["source"]))
        for row in source_records
    ]

    configs, install_paths = configured_paths()

    installed = installed_folder_probe(
        install_paths
    )

    grouped = defaultdict(list)

    for package in packages:
        key = (
            package["embedded_title_id"]
            or "UNIDENTIFIED"
        )
        grouped[key].append(package)

    result = {
        "schema_version": 1,
        "read_only": True,
        "installation_enabled": False,
        "package_count": len(packages),
        "packages": packages,
        "configuration_files": configs,
        "configured_install_paths": install_paths,
        "installed_folder_evidence": installed,
    }

    with REPORT.open(
        "x", encoding="utf-8"
    ) as stream:
        json.dump(
            result, stream,
            indent=2,
            ensure_ascii=False
        )
        stream.write("\n")

    print("===== PS4 PACKAGE HEADER RESULTS =====")

    print("Candidate files:", len(packages))
    print(
        "Recognized PS4 magic:",
        sum(p["header_valid"] for p in packages)
    )
    print(
        "Embedded CUSA IDs:",
        sum(
            bool(p["embedded_title_id"])
            for p in packages
        )
    )

    print(
        "Filename/header conflicts:",
        sum(
            bool(p.get("filename_conflict"))
            for p in packages
        )
    )

    hints = Counter(
        p.get("directory_content_hint", "unknown")
        for p in packages
    )

    print("Directory-based hints:", dict(hints))

    print()
    print("===== GROUPED TITLE IDENTITIES =====")

    for title_id, items in sorted(grouped.items()):
        print()
        print(
            title_id,
            "| package records:",
            len(items)
        )

        counts = Counter(
            item.get(
                "directory_content_hint", "unknown"
            )
            for item in items
        )

        print("  Path hints:", dict(counts))

        for item in items[:5]:
            print(
                "  ",
                Path(item["source"]).name[:105]
            )
            print(
                "   Header:",
                item["header_valid"],
                "| filename conflict:",
                item.get("filename_conflict", False)
            )

        if len(items) > 5:
            print(
                "   Additional records:",
                len(items) - 5
            )

    print()
    print("===== SHADPS4 CONFIGURATION =====")

    for entry in configs:
        print()
        print(entry["config"])
        print("  Found:", entry["exists"])
        print(
            "  Parse:",
            entry.get("parse_status", "not_present")
        )
        print("  Path keys:", entry["path_keys"])

    print()
    print("Configured install paths:", len(install_paths))

    for record in installed:
        print()
        print("  Path:", record["configured_path"])
        print("  Exists:", record["directory_exists"])
        print(
            "  CUSA-named folders:",
            len(record["game_folders"])
        )

        for folder in record["game_folders"][:15]:
            print(
                "   ",
                folder["title_id_hint"],
                "| param.sfo:",
                folder["param_sfo_present"]
            )

    print()
    print("===== SAFETY STATUS =====")
    print("Content type verified: NO")
    print("Package compatibility verified: NO")
    print("Installed/active status verified: NO")
    print("Installation operations: DISABLED")
    print()
    print("Report:", REPORT)
    print("No source packages modified")


if __name__ == "__main__":
    main()
