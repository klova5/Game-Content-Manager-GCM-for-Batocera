"""GCM v3.6 read-only source-package discovery.

No package installation, extraction, copying, or deletion.
"""

import argparse
import json
import os
import re
import struct
from collections import deque
from pathlib import Path

from adapters.cemu import read_meta

BASE = Path(__file__).resolve().parent

WIIU_KINDS = {
    "00050000": "base_game",
    "0005000e": "update",
    "0005000c": "dlc",
}

WIIU_CONTAINERS = {".wua", ".wux", ".wud"}

FAST_PATHS = [
    "/userdata/roms/ps3",
    "/userdata/roms/wiiu",
    "/userdata/downloads",
    "/userdata/Downloads",
    "/userdata/updates",
    "/userdata/dlc",
    "/userdata/system/game-content",
    "/media/easystore/emulators/roms/ps3",
    "/media/easystore/emulators/roms/wiiu",
    "/media/easystore/downloads",
    "/media/easystore/updates",
    "/media/easystore/DLC",
]

MAX_RECORDS = 1500


def load_identities():
    report = json.loads(
        (BASE / "identity-inventory.json").read_text()
    )
    ps3 = {
        row["title_id"].upper()
        for row in report["rpcs3"]["games"]
    }
    wiiu = {
        row["base_title_id"].lower()
        for row in report["cemu"]["content_directories"]
        if row["kind"] == "base_game"
        and row["meta_xml_present"]
        and not row["metadata_conflict"]
    }
    return ps3, wiiu


def inspect_pkg(path, known_ps3):
    try:
        with path.open("rb") as stream:
            header = stream.read(128)
        size = path.stat().st_size
    except OSError:
        return None

    if len(header) < 84 or header[:4] != b"\x7fPKG":
        return None

    declared_size = struct.unpack_from(">Q", header, 0x18)[0]
    raw_id = header[0x30:0x54].split(b"\x00", 1)[0]

    try:
        content_id = raw_id.decode("ascii")
    except UnicodeDecodeError:
        content_id = ""

    if not all(32 <= ord(c) < 127 for c in content_id):
        content_id = ""

    match = re.search(
        r"-([A-Z0-9]{9})_", content_id.upper()
    )
    title_id = match.group(1) if match else ""

    valid_size = declared_size == size and size >= 128
    known_match = valid_size and title_id in known_ps3

    return {
        "format": "sony_pkg",
        "source": str(path),
        "size_bytes": size,
        "header_size_consistent": valid_size,
        "content_id": content_id,
        "title_id": title_id,
        "matching_ps3_game": title_id if known_match else None,
        "content_kind": "unknown",
        "compatibility_verified": False,
        "status": (
            "title_id_match_requires_package_validation"
            if known_match
            else "unverified_package"
        ),
    }


def inspect_wiiu_meta(path, known_wiiu):
    if path.parent.name.lower() != "meta":
        return None

    try:
        meta = read_meta(path)
    except (OSError, ValueError, Exception) as exc:
        return {
            "format": "wiiu_meta",
            "source": str(path),
            "status": "metadata_unreadable",
            "error_type": type(exc).__name__,
            "compatibility_verified": False,
        }

    if not meta:
        return None

    title_id = (
        str(meta.get("title_id", ""))
        .lower()
        .removeprefix("0x")
    )

    if not re.fullmatch(r"[0-9a-f]{16}", title_id):
        return None

    kind = WIIU_KINDS.get(
        title_id[:8], "unknown"
    )

    base_id = "00050000" + title_id[8:]

    package = path.parent.parent
    has_content = (package / "content").is_dir()
    has_code = (package / "code").is_dir()

    return {
        "format": "wiiu_meta",
        "source": str(path),
        "package_root": str(package),
        "title_id": title_id,
        "base_title_id": base_id,
        "content_kind": kind,
        "version": meta.get("title_version", ""),
        "name": meta.get("longname_en", ""),
        "content_directory_present": has_content,
        "code_directory_present": has_code,
        "known_base_game": base_id in known_wiiu,
        "compatibility_verified": False,
        "status": "metadata_identified_not_verified",
    }


def fast_roots():
    paths = [Path(p) for p in FAST_PATHS]

    for parent in [Path("/userdata"), Path("/media")]:
        if not parent.is_dir():
            continue
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue

            name = child.name.lower()

            if parent == Path("/userdata"):
                if any(
                    term in name
                    for term in ("update", "dlc", "download", "ps3", "wiiu")
                ):
                    paths.append(child)
            else:
                for term in (
                    "updates", "update", "dlc",
                    "downloads", "ps3", "wiiu"
                ):
                    paths.append(child / term)

    return paths


def deep_roots():
    result = [Path("/userdata")]
    media = Path("/media")
    if media.is_dir():
        for item in media.iterdir():
            if item.is_dir() and not item.is_symlink():
                result.append(item)
    return result


def scan(mode, known_ps3, known_wiiu):
    roots = fast_roots() if mode == "fast" else deep_roots()

    max_dirs = 20000 if mode == "fast" else 100000
    max_files = 250000 if mode == "fast" else 1000000

    queue = deque(
        root for root in roots
        if root.is_dir() and not root.is_symlink()
    )

    visited = set()
    results = []
    directories = 0
    files = 0
    limited = False
    errors = 0

    while queue:
        if directories >= max_dirs or files >= max_files:
            limited = True
            break

        directory = queue.popleft()

        try:
            stat = directory.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in visited:
                continue
            visited.add(identity)
            entries = os.scandir(directory)
        except OSError:
            errors += 1
            continue

        directories += 1

        try:
            with entries as iterator:
                for entry in iterator:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            queue.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        errors += 1
                        continue

                    files += 1
                    if files >= max_files:
                        limited = True
                        break

                    path = Path(entry.path)
                    lower = path.name.lower()

                    record = None
                    if lower.endswith(".pkg"):
                        record = inspect_pkg(path, known_ps3)
                    elif lower == "meta.xml":
                        record = inspect_wiiu_meta(
                            path, known_wiiu
                        )
                    elif path.suffix.lower() in WIIU_CONTAINERS:
                        record = {
                            "format": "wiiu_container",
                            "source": str(path),
                            "content_kind": "unknown",
                            "compatibility_verified": False,
                            "status": "container_identity_not_verified",
                        }

                    if record is not None:
                        if len(results) < MAX_RECORDS:
                            results.append(record)
                        else:
                            limited = True

                if files >= max_files:
                    break
        except OSError:
            errors += 1

    results.sort(key=lambda r: r["source"])

    return {
        "schema_version": 1,
        "mode": mode,
        "read_only": True,
        "directories_examined": directories,
        "files_examined": files,
        "directory_limit": max_dirs,
        "file_limit": max_files,
        "records_returned": len(results),
        "scan_incomplete": limited or bool(queue) or errors > 0,
        "filesystem_errors": errors,
        "candidates": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=("fast", "deep"),
        default="fast"
    )
    args = parser.parse_args()

    registry = json.loads(
        (BASE / "adapter-registry.json").read_text()
    )
    assert registry["adapter_status"] == "discovery_only"

    identities = load_identities()
    report = scan(args.mode, *identities)

    output = BASE / f"candidate-scan-{args.mode}.json"
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    print("===== GCM V3.6 PACKAGE DISCOVERY =====")
    print("Mode:", args.mode.upper())
    print("Directories examined:", report["directories_examined"])
    print("Files examined:", report["files_examined"])
    print("Candidate records:", report["records_returned"])
    print("Filesystem errors:", report["filesystem_errors"])
    print("Scan incomplete:", report["scan_incomplete"])

    for row in report["candidates"][:45]:
        print()
        print("Format:", row["format"])
        print("Source:", row["source"])
        print("Status:", row["status"])
        if row.get("title_id"):
            print("Title ID:", row["title_id"])
        if row.get("matching_ps3_game"):
            print("PS3 game match:", row["matching_ps3_game"])
        if row.get("content_kind"):
            print("Content type:", row["content_kind"])

    if len(report["candidates"]) > 45:
        print(
            "Additional records:",
            len(report["candidates"]) - 45
        )

    print()
    print("Report:", output)
    print("Content installation enabled: NO")
    print("Existing files modified: NO")


if __name__ == "__main__":
    main()
