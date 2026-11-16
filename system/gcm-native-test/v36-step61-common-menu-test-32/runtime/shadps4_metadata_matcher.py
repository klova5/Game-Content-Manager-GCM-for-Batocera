"""GCM v3.6: read-only shadPS4 package metadata matching."""

import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPORT = BASE / "shadps4-metadata-matches.json"

CUSA = re.compile(r"^CUSA[0-9]{5}$")

CATEGORIES = {
    "gd": "base_game",
    "gp": "update",
    "ac": "dlc",
}

MAX_SFO = 2 * 1024 * 1024
MAX_ENTRIES = 2048


def content_title_id(value):
    matches = re.findall(
        r"CUSA[0-9]{5}",
        str(value).upper()
    )
    return matches[0] if len(matches) == 1 else None


def parse_sfo(raw):
    if len(raw) < 20 or len(raw) > MAX_SFO:
        return None

    try:
        magic, version, keys, values, count = (
            struct.unpack_from("<4sIIII", raw, 0)
        )
    except struct.error:
        return None

    if (
        magic != b"\x00PSF"
        or count > 512
        or 20 + count * 16 > len(raw)
        or keys > len(raw)
        or values > len(raw)
    ):
        return None

    data = {}

    for index in range(count):
        try:
            key_offset, fmt, length, maximum, value_offset = (
                struct.unpack_from(
                    "<HHIII", raw, 20 + index * 16
                )
            )
        except struct.error:
            return None

        kpos = keys + key_offset
        dpos = values + value_offset

        if (
            kpos >= len(raw)
            or dpos > len(raw)
            or length > maximum
            or length > len(raw) - dpos
        ):
            return None

        kend = raw.find(
            b"\x00",
            kpos,
            min(len(raw), kpos + 128)
        )

        if kend < 0:
            return None

        key = raw[kpos:kend].decode(
            "utf-8", "replace"
        )

        value = raw[dpos:dpos + length]

        if fmt == 0x0404 and len(value) >= 4:
            decoded = int.from_bytes(
                value[:4], "little"
            )
        else:
            decoded = value.split(
                b"\x00", 1
            )[0].decode("utf-8", "replace")

        data[key] = decoded

    return data


def identity(meta):
    if not meta:
        return {
            "title_id": None,
            "category": None,
            "content_kind": "unknown",
            "version": None,
            "identity_conflict": False,
        }

    title_id = str(
        meta.get("TITLE_ID", "")
    ).upper()

    sfo_title = (
        title_id if CUSA.fullmatch(title_id)
        else None
    )

    content_title = content_title_id(
        meta.get("CONTENT_ID", "")
    )

    conflict = bool(
        sfo_title
        and content_title
        and sfo_title != content_title
    )

    category = str(
        meta.get("CATEGORY", "")
    ).lower()

    return {
        "title_id": sfo_title or content_title,
        "category": category,
        "content_kind": CATEGORIES.get(
            category, "unknown"
        ),
        "version": str(
            meta.get("APP_VER", "")
        ) or None,
        "title": str(
            meta.get("TITLE", "")
        ),
        "identity_conflict": conflict,
    }


def installed_games(inventory):
    results = []
    verified = {}

    for root in inventory["installed_folder_evidence"]:
        for entry in root["game_folders"]:
            path = Path(entry["directory"])
            sfo = path / "sce_sys/param.sfo"

            record = {
                "directory": str(path),
                "folder_title_id": entry["title_id_hint"],
                "sfo_present": False,
                "identity_verified": False,
                "installed_base_metadata_verified": False,
                "active": None,
            }

            if sfo.is_symlink() or not sfo.is_file():
                results.append(record)
                continue

            try:
                if sfo.stat().st_size > MAX_SFO:
                    raise ValueError("SFO too large")

                meta = parse_sfo(sfo.read_bytes())
            except (OSError, ValueError):
                meta = None

            record["sfo_present"] = True
            record["sfo_parsed"] = meta is not None

            details = identity(meta)
            record.update(details)

            confirmed = (
                meta is not None
                and not details["identity_conflict"]
                and details["title_id"]
                    == entry["title_id_hint"]
                and details["category"] == "gd"
            )

            record["identity_verified"] = confirmed
            record["installed_base_metadata_verified"] = confirmed

            if confirmed:
                verified[
                    entry["title_id_hint"]
                ] = record

            results.append(record)

    return results, verified


def package_sfo(path):
    result = {
        "read_status": "unavailable",
        "metadata": None,
    }

    if path.is_symlink():
        result["read_status"] = "symlink_rejected"
        return result

    try:
        size = path.stat().st_size

        with path.open("rb") as stream:
            header = stream.read(4096)

            if (
                len(header) != 4096
                or header[:4] != b"\x7fCNT"
            ):
                result["read_status"] = "invalid_pkg_header"
                return result

            count = struct.unpack_from(
                ">I", header, 0x10
            )[0]

            offset = struct.unpack_from(
                ">I", header, 0x18
            )[0]

            result["table_entry_count"] = count

            if (
                count == 0
                or count > MAX_ENTRIES
                or offset < 4096
                or offset + count * 32 > size
            ):
                result["read_status"] = "invalid_entry_table"
                return result

            stream.seek(offset)
            table = stream.read(count * 32)

            if len(table) != count * 32:
                result["read_status"] = "truncated_entry_table"
                return result

            entry = None

            for index in range(count):
                fields = struct.unpack_from(
                    ">IIIIIIQ",
                    table,
                    index * 32
                )

                if fields[0] == 0x1000:
                    entry = fields
                    break

            if entry is None:
                result["read_status"] = "param_sfo_entry_missing"
                return result

            file_offset = entry[4]
            file_size = entry[5]

            if (
                file_size < 20
                or file_size > MAX_SFO
                or file_offset > size
                or file_size > size - file_offset
            ):
                result["read_status"] = "invalid_sfo_entry_bounds"
                return result

            stream.seek(file_offset)
            raw = stream.read(file_size)

    except (OSError, ValueError, struct.error) as exc:
        result["read_status"] = type(exc).__name__
        return result

    meta = parse_sfo(raw)

    if meta is None:
        result["read_status"] = "sfo_not_plain_or_unreadable"
        return result

    result["read_status"] = "parsed"
    result["metadata"] = {
        key: meta.get(key)
        for key in (
            "TITLE_ID", "CONTENT_ID",
            "CATEGORY", "APP_VER", "TITLE"
        )
        if key in meta
    }

    return result


def main():
    original = json.loads(
        (BASE / "shadps4-package-inventory.json")
        .read_text()
    )

    assert original["read_only"] is True
    assert original["installation_enabled"] is False

    installed, verified = installed_games(original)

    packages = []
    grouped = defaultdict(list)

    for old in original["packages"]:
        path = Path(old["source"])

        read = package_sfo(path)

        details = identity(read["metadata"])

        header_id = old.get("embedded_title_id")
        metadata_id = details["title_id"]

        header_conflict = bool(
            metadata_id
            and header_id
            and metadata_id != header_id
        )

        resolved_id = (
            metadata_id or header_id
        )

        base_match = (
            resolved_id in verified
            if resolved_id
            else False
        )

        # Only parsed metadata can establish the
        # package's update/DLC category.
        category_verified = (
            read["read_status"] == "parsed"
            and details["content_kind"]
                in ("base_game", "update", "dlc")
            and not details["identity_conflict"]
            and not header_conflict
        )

        record = {
            "source": old["source"],
            "size_bytes": old.get("size_bytes"),
            "header_title_id": header_id,
            "metadata_title_id": metadata_id,
            "resolved_title_id": resolved_id,
            "pkg_sfo_status": read["read_status"],
            "pkg_sfo": read["metadata"],
            "metadata_content_kind":
                details["content_kind"],
            "metadata_category":
                details["category"],
            "metadata_version":
                details["version"],
            "directory_hint":
                old.get("directory_content_hint"),
            "metadata_conflict": bool(
                details["identity_conflict"]
                or header_conflict
                or old.get("filename_conflict")
            ),
            "base_game_identity_verified": base_match,
            "package_category_verified": category_verified,
            "full_sha256_verified": False,
            "compatibility_verified": False,
            "installed": None,
            "active": None,
            "installation_allowed": False,
        }

        packages.append(record)
        grouped[
            resolved_id or "UNIDENTIFIED"
        ].append(record)

    output = {
        "schema_version": 1,
        "mode": "development",
        "read_only": True,
        "installation_enabled": False,
        "installed_base_games": installed,
        "packages": packages,
    }

    with REPORT.open(
        "x", encoding="utf-8"
    ) as stream:
        json.dump(
            output, stream,
            indent=2,
            ensure_ascii=False
        )
        stream.write("\n")

    print("===== VERIFIED BASE-GAME METADATA =====")
    print("Game folders inspected:", len(installed))
    print("Base identities verified:", len(verified))

    for row in installed:
        print()
        print("Folder:", row["folder_title_id"])
        print("SFO parsed:", row.get("sfo_parsed", False))
        print("SFO title:", row.get("title_id"))
        print("Category:", row.get("category"))
        print("Version:", row.get("version"))
        print("Base identity verified:",
              row["identity_verified"])

    print()
    print("===== PKG METADATA DISCOVERY =====")

    print("Total packages:", len(packages))

    statuses = Counter(
        p["pkg_sfo_status"]
        for p in packages
    )

    print("PKG SFO read results:", dict(statuses))

    kinds = Counter(
        p["metadata_content_kind"]
        for p in packages
    )

    print("Metadata content types:", dict(kinds))

    print(
        "Package identity conflicts:",
        sum(p["metadata_conflict"] for p in packages)
    )

    print()
    print("===== GAME AND PACKAGE MATCHES =====")

    for title_id, rows in sorted(grouped.items()):
        print()
        print("Title ID:", title_id)
        print("Package records:", len(rows))

        print(
            "Base metadata match:",
            title_id in verified
        )

        print(
            "Confirmed package categories:",
            dict(Counter(
                p["metadata_content_kind"]
                for p in rows
                if p["package_category_verified"]
            ))
        )

        for row in rows[:6]:
            print(
                " ",
                Path(row["source"]).name[:95]
            )
            print(
                "   SFO:",
                row["pkg_sfo_status"],
                "| Type:",
                row["metadata_content_kind"],
                "| Conflict:",
                row["metadata_conflict"]
            )

        if len(rows) > 6:
            print(
                " Additional records:",
                len(rows) - 6
            )

    print()
    print("===== SAFETY =====")
    print("Complete package hashing: NOT PERFORMED")
    print("Package authenticity: NOT VERIFIED")
    print("Installed/active status: NOT VERIFIED")
    print("Installation: DISABLED")
    print("Production frontend modified: NO")
    print("Source packages modified: NO")
    print()
    print("Report:", REPORT)


if __name__ == "__main__":
    main()
