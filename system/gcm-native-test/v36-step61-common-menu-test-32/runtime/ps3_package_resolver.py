"""GCM v3.6: read-only PS3 package matching and evidence."""

from collections import defaultdict
from pathlib import Path
import hashlib
import json
import re
import struct

from adapters.rpcs3 import read_sfo

BASE = Path(__file__).resolve().parent
HDD = Path("/userdata/system/configs/rpcs3/dev_hdd0/game")
TITLE_RE = re.compile(r"^[A-Z0-9]{9}$")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_pkg(path):
    try:
        stat = path.stat()
        with path.open("rb") as stream:
            header = stream.read(4096)
            if len(header) < 128 or header[:4] != b"\x7fPKG":
                return {"valid_header": False, "error": "bad_magic"}

            revision, platform = struct.unpack_from(">HH", header, 4)
            meta_offset, meta_count, meta_size = struct.unpack_from(
                ">III", header, 8
            )
            declared_size, data_offset, data_size = struct.unpack_from(
                ">QQQ", header, 0x18
            )

            raw_id = header[0x30:0x60].split(b"\x00", 1)[0]
            content_id = raw_id.decode("ascii", "replace").strip()

            valid = (
                platform == 1
                and declared_size == stat.st_size
                and 128 <= data_offset <= stat.st_size
                and data_size <= stat.st_size - data_offset
            )

            metadata = {}
            if (
                128 <= meta_offset < data_offset
                and meta_count <= 128
                and meta_size <= 524288
                and meta_offset + meta_size <= data_offset
            ):
                stream.seek(meta_offset)
                blob = stream.read(meta_size)
                cursor = 0

                for _ in range(meta_count):
                    if cursor + 8 > len(blob):
                        break
                    kind, length = struct.unpack_from(
                        ">II", blob, cursor
                    )
                    cursor += 8
                    if length > len(blob) - cursor:
                        break

                    value = blob[cursor:cursor + length]
                    cursor += length

                    if kind == 2 and len(value) == 4:
                        metadata["content_type_code"] = int.from_bytes(
                            value, "big"
                        )
                    elif kind == 6:
                        metadata["title_id"] = value.split(
                            b"\x00", 1
                        )[0].decode("ascii", "replace")

            content_match = re.search(
                r"-([A-Z0-9]{9})_", content_id.upper()
            )
            content_title = (
                content_match.group(1) if content_match else ""
            )
            metadata_title = str(
                metadata.get("title_id", "")
            ).upper()

            if not TITLE_RE.fullmatch(metadata_title):
                metadata_title = ""

            conflict = bool(
                content_title and metadata_title
                and content_title != metadata_title
            )

            title_id = metadata_title or content_title

            return {
                "valid_header": valid,
                "source": str(path),
                "size_bytes": stat.st_size,
                "declared_size": declared_size,
                "header_revision": revision,
                "platform_type": platform,
                "content_id": content_id,
                "title_id": title_id,
                "metadata_title_id": metadata_title,
                "metadata_content_type_code": metadata.get(
                    "content_type_code"
                ),
                "identity_conflict": conflict,
                "header_sha256": sha(header),
                "filesystem_identity": [
                    stat.st_dev, stat.st_ino
                ],
                "full_sha256_verified": False,
            }
    except (OSError, struct.error) as exc:
        return {
            "valid_header": False,
            "source": str(path),
            "error": type(exc).__name__,
        }


def game_evidence(path, title_id):
    # A package under GAME/updates/ is not proof the base game exists.
    game_dir = path.parent.parent
    evidence = {
        "nearby_game_folder": str(game_dir),
        "base_identity_verified": False,
        "metadata_title_id": "",
    }

    candidates = (
        game_dir / "PS3_GAME/PARAM.SFO",
        game_dir / "PARAM.SFO",
    )

    for sfo in candidates:
        if not sfo.is_file():
            continue
        try:
            meta = read_sfo(sfo)
        except (OSError, ValueError):
            continue

        if not meta:
            continue

        found = str(meta.get("TITLE_ID", "")).upper()
        evidence["metadata_title_id"] = found

        if found == title_id:
            evidence["base_identity_verified"] = True
            evidence["metadata_path"] = str(sfo)
            evidence["game_name"] = meta.get("TITLE", "")
            break

    return evidence


def installed_evidence(title_id):
    if not TITLE_RE.fullmatch(title_id):
        return {"status": "unknown"}

    folder = HDD / title_id
    sfo = folder / "PARAM.SFO"
    eboot = folder / "USRDIR/EBOOT.BIN"

    meta = None
    if sfo.is_file():
        try:
            meta = read_sfo(sfo)
        except (OSError, ValueError):
            pass

    valid_update_files = bool(
        meta
        and str(meta.get("TITLE_ID", "")).upper() == title_id
        and meta.get("CATEGORY") == "GD"
        and eboot.is_file()
    )

    return {
        "directory_exists": folder.is_dir(),
        "param_sfo_present": sfo.is_file(),
        "eboot_present": eboot.is_file(),
        "metadata_category": (
            (meta or {}).get("CATEGORY", "")
        ),
        "update_file_evidence": valid_update_files,
        "installed": None,
        "active": None,
    }


def main():
    scan = json.loads(
        (BASE / "candidate-scan-fast.json").read_text()
    )
    inventory = json.loads(
        (BASE / "identity-inventory.json").read_text()
    )

    known = {
        game["title_id"]
        for game in inventory["rpcs3"]["games"]
    }

    groups = defaultdict(list)
    rejected = []

    for candidate in scan["candidates"]:
        if candidate.get("format") != "sony_pkg":
            continue

        path = Path(candidate["source"])
        info = parse_pkg(path)

        if (
            not info.get("valid_header")
            or info.get("identity_conflict")
            or not TITLE_RE.fullmatch(
                info.get("title_id", "")
            )
        ):
            rejected.append(info)
            continue

        title_id = info["title_id"]
        info["game_evidence"] = game_evidence(path, title_id)

        key = (
            title_id,
            info["content_id"],
            info["size_bytes"],
            info["header_sha256"],
        )
        groups[key].append(info)

    matches = []

    for (title_id, content_id, size, header_hash), copies in sorted(
        groups.items()
    ):
        verified_base = title_id in known or any(
            copy["game_evidence"]["base_identity_verified"]
            for copy in copies
        )

        physical_ids = {
            tuple(copy["filesystem_identity"])
            for copy in copies
        }

        matches.append({
            "title_id": title_id,
            "content_id": content_id,
            "size_bytes": size,
            "source_count": len(copies),
            "distinct_filesystem_objects": len(physical_ids),
            "header_matches": True,
            "full_files_equal": (
                True if len(physical_ids) == 1 else None
            ),
            "base_game_identity_verified": verified_base,
            "installed_evidence": installed_evidence(title_id),
            "content_kind": "unverified",
            "compatibility_verified": False,
            "installation_allowed": False,
            "sources": copies,
        })

    result = {
        "schema_version": 1,
        "read_only": True,
        "scan_incomplete": scan["scan_incomplete"],
        "matching_groups": matches,
        "rejected_candidates": rejected,
        "installation_enabled": False,
    }

    print("===== GCM V3.6 PS3 PACKAGE MATCHING =====")
    print("Package groups:", len(matches))
    print("Rejected candidates:", len(rejected))

    for match in matches:
        print()
        print("Title ID:", match["title_id"])
        print("Content ID:", match["content_id"])
        print("Sources:", match["source_count"])
        print("Filesystem objects:",
              match["distinct_filesystem_objects"])
        print("Base identity verified:",
              match["base_game_identity_verified"])
        print("Full-file equality:",
              match["full_files_equal"])

        installed = match["installed_evidence"]
        print("RPCS3 content directory:",
              installed["directory_exists"])
        print("Installed PARAM.SFO:",
              installed.get("param_sfo_present"))
        print("Installed EBOOT.BIN:",
              installed.get("eboot_present"))
        print("Update files verified:",
              installed.get("update_file_evidence"))

        for copy in match["sources"]:
            print("  Source:", copy["source"])
            print("  Header valid:", copy["valid_header"])
            print("  Metadata title:",
                  copy["metadata_title_id"] or "not available")
            print("  Nearby game identity:",
                  copy["game_evidence"]["base_identity_verified"])

        print("Compatibility: NOT VERIFIED")
        print("Installation: DISABLED")

    for item in rejected:
        print()
        print("REJECTED:", item.get("source"))
        print("Reason:", item.get("error", "identity/header conflict"))

    output = BASE / "ps3-package-matches.json"
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")

    print()
    print("Report:", output)
    print("No source files modified")
    print("No content installed")


if __name__ == "__main__":
    main()
