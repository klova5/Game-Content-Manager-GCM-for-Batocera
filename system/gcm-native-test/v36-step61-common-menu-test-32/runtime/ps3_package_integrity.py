"""GCM v3.6: read-only PS3 package integrity and game evidence."""

import hashlib
import json
import os
import stat
from collections import deque
from pathlib import Path

from adapters.rpcs3 import read_sfo

BASE = Path(__file__).resolve().parent
REPORT = BASE / "ps3-package-integrity.json"

CHUNK = 8 * 1024 * 1024
PROGRESS = 256 * 1024 * 1024


def file_hash(source, expected_size, expected_header):
    path = Path(source)

    if path.is_symlink():
        raise RuntimeError(f"Symlink source rejected: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)

    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())

        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Source not regular file: {path}")

        if before.st_size != expected_size:
            raise RuntimeError(f"Source size changed: {path}")

        first = stream.read(4096)
        header_hash = hashlib.sha256(first).hexdigest()

        if header_hash != expected_header:
            raise RuntimeError(f"Package header changed: {path}")

        stream.seek(0)

        digest = hashlib.sha256()
        processed = 0
        next_progress = PROGRESS

        while True:
            block = stream.read(CHUNK)

            if not block:
                break

            digest.update(block)
            processed += len(block)

            if processed >= next_progress:
                print(
                    f"  Hashed {processed:,} / "
                    f"{before.st_size:,} bytes",
                    flush=True
                )
                next_progress = processed + PROGRESS

        after = os.fstat(stream.fileno())

    attributes = (
        "st_dev", "st_ino", "st_size",
        "st_mtime_ns", "st_ctime_ns"
    )

    if any(
        getattr(before, name) != getattr(after, name)
        for name in attributes
    ):
        raise RuntimeError(
            f"Source changed while hashing: {path}"
        )

    if processed != expected_size:
        raise RuntimeError(f"Incomplete read: {path}")

    return {
        "source": str(path),
        "size_bytes": processed,
        "sha256": digest.hexdigest(),
        "header_sha256": header_hash,
        "stable_during_read": True,
    }


def nearby_game_evidence(package_source, title_id):
    path = Path(package_source)
    root = path.parent.parent

    found = []
    containers = []
    examined = 0

    queue = deque([(root, 0)])
    skip = {
        "updates", "update", "dlc",
        "usrdir", "tropdir", "savedata",
    }

    while queue and examined < 150:
        directory, depth = queue.popleft()

        if directory.is_symlink() or not directory.is_dir():
            continue

        examined += 1

        try:
            children = sorted(
                directory.iterdir(),
                key=lambda p: p.name.lower()
            )[:150]
        except OSError:
            continue

        for child in children:
            if child.is_symlink():
                continue

            if child.is_file():
                if child.name.lower() == "param.sfo":
                    try:
                        metadata = read_sfo(child)
                    except (OSError, ValueError):
                        metadata = None

                    if metadata:
                        identified = str(
                            metadata.get("TITLE_ID", "")
                        ).upper()

                        found.append({
                            "path": str(child),
                            "title_id": identified,
                            "title": metadata.get("TITLE", ""),
                            "category": metadata.get("CATEGORY", ""),
                            "matches_package": identified == title_id,
                        })

                elif child.suffix.lower() in {
                    ".iso", ".chd", ".zip", ".7z",
                    ".rar", ".cso", ".bin"
                }:
                    if len(containers) < 20:
                        containers.append(str(child))

            elif (
                child.is_dir()
                and depth < 3
                and child.name.lower() not in skip
            ):
                queue.append((child, depth + 1))

    return {
        "game_root": str(root),
        "directories_examined": examined,
        "matching_base_metadata": any(
            entry["matches_package"] for entry in found
        ),
        "sfo_records": found,
        "possible_containers": containers,
    }


def installed_directory_evidence(title_id):
    root = Path(
        "/userdata/system/configs/rpcs3/"
        "dev_hdd0/game"
    ) / title_id

    if not root.is_dir():
        return {
            "directory_exists": False,
            "entries": [],
        }

    entries = []

    for child in sorted(
        root.iterdir(),
        key=lambda p: p.name
    )[:25]:
        if child.is_symlink():
            continue

        record = {
            "name": child.name,
            "is_directory": child.is_dir(),
        }

        if child.name == "USRDIR" and child.is_dir():
            record["sample_children"] = [
                p.name
                for p in sorted(
                    child.iterdir(),
                    key=lambda p: p.name
                )[:20]
            ]

        entries.append(record)

    return {
        "directory_exists": True,
        "entries": entries,
        "param_sfo_present": (root / "PARAM.SFO").is_file(),
        "eboot_present": (root / "USRDIR/EBOOT.BIN").is_file(),
    }


def main():
    data = json.loads(
        (BASE / "ps3-package-matches.json").read_text()
    )

    assert data["read_only"] is True
    assert data["installation_enabled"] is False

    results = []

    print("===== GCM V3.6 PS3 INTEGRITY TEST =====")

    for group in data["matching_groups"]:
        assert group["installation_allowed"] is False

        title_id = group["title_id"]
        print()
        print("TITLE ID:", title_id)
        print("Content ID:", group["content_id"])

        hashes = []
        game_evidence = []

        for source in group["sources"]:
            path = source["source"]
            print()
            print("HASHING:", path, flush=True)

            checked = file_hash(
                path,
                source["size_bytes"],
                source["header_sha256"]
            )

            print("SHA256:", checked["sha256"])
            print("PASS: Full file read completed")

            hashes.append(checked)

            game_evidence.append(
                nearby_game_evidence(path, title_id)
            )

        equal = (
            len({item["sha256"] for item in hashes}) == 1
        )

        base_found = any(
            item["matching_base_metadata"]
            for item in game_evidence
        )

        installed = installed_directory_evidence(title_id)

        print()
        print("===== SOURCE COMPARISON =====")
        print("Full-file equality:", equal)
        print("Matching base metadata:", base_found)
        print("Installed directory:",
              installed["directory_exists"])
        print("Installed PARAM.SFO:",
              installed.get("param_sfo_present"))
        print("Installed EBOOT.BIN:",
              installed.get("eboot_present"))

        for evidence in game_evidence:
            print()
            print("Game root:", evidence["game_root"])
            print(
                "Directories examined:",
                evidence["directories_examined"]
            )

            for entry in evidence["sfo_records"]:
                print(
                    "SFO:", entry["title_id"],
                    "| match:", entry["matches_package"],
                    "|", entry["path"]
                )

            for container in evidence["possible_containers"]:
                print("Possible game container:", container)

        print()
        print("RPCS3 directory entries:")

        for entry in installed.get("entries", []):
            print(
                " ", entry["name"],
                "(directory)" if entry["is_directory"] else "(file)"
            )
            for child in entry.get("sample_children", []):
                print("    ", child)

        results.append({
            "title_id": title_id,
            "content_id": group["content_id"],
            "sources": hashes,
            "full_file_equality": equal,
            "matching_base_metadata_found": base_found,
            "game_evidence": game_evidence,
            "installed_directory_evidence": installed,
            "package_authenticity_verified": False,
            "compatibility_verified": False,
            "installed_status_verified": False,
            "active_status_verified": False,
            "installation_allowed": False,
        })

    output = {
        "schema_version": 1,
        "read_only": True,
        "results": results,
        "installation_enabled": False,
    }

    with REPORT.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")

    print()
    print("===== STEP 93 REPORT =====")
    print(REPORT)
    print("Package authenticity: NOT VERIFIED")
    print("Installation: DISABLED")
    print("Source files modified: NO")


if __name__ == "__main__":
    main()
