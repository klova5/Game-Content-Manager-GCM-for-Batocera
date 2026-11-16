"""Read-only RPCS3 and Cemu identity and content inventory."""

import json
import re
import xml.etree.ElementTree as ET

from collections import defaultdict
from pathlib import Path

from adapters.rpcs3 import read_sfo
from adapters.cemu import read_meta

BASE = Path(__file__).resolve().parent

PS3_GAME = Path(
    "/userdata/system/configs/rpcs3/dev_hdd0/game"
)

WIIU_TITLE = Path(
    "/userdata/saves/wiiu/usr/title"
)

WIIU_ROMS = [
    Path("/userdata/roms/wiiu"),
    Path("/media/easystore/emulators/roms/wiiu"),
]

WIIU_TYPES = {
    "00050000": "base_game",
    "0005000e": "update",
    "0005000c": "dlc",
}


def ps3_inventory(report):
    grouped = defaultdict(list)

    for record in report["adapters"]["rpcs3"]:
        title_id = str(
            record.get("title_id", "")
        ).upper()

        if not re.fullmatch(
            r"[A-Z0-9]{9}", title_id
        ):
            continue

        grouped[title_id].append(record)

    titles = []

    for title_id, items in sorted(grouped.items()):
        versions = sorted({
            str(r.get("version", ""))
            for r in items if r.get("version")
        })

        names = sorted({
            str(r.get("title", ""))
            for r in items if r.get("title")
        })

        sources = sorted({
            (
                r["location"],
                r["metadata_path"]
            )
            for r in items
        })

        titles.append({
            "title_id": title_id,
            "names": names,
            "versions": versions,
            "source_locations": [
                {
                    "location": location,
                    "metadata_path": path,
                }
                for location, path in sources
            ],
            "requires_identity_review": (
                len(names) > 1
                or len(versions) > 1
            ),
        })

    installed = []

    if PS3_GAME.is_dir():
        for folder in sorted(
            PS3_GAME.iterdir(),
            key=lambda p: p.name
        )[:300]:

            if (
                not folder.is_dir()
                or folder.is_symlink()
                or folder.name.startswith("＄")
            ):
                continue

            sfo = folder / "PARAM.SFO"
            metadata = None

            if sfo.is_file():
                try:
                    metadata = read_sfo(sfo)
                except (OSError, ValueError):
                    pass

            category = str(
                (metadata or {}).get("CATEGORY", "")
            )

            sfotitle = str(
                (metadata or {}).get(
                    "TITLE_ID", ""
                )
            ).upper()

            eboot = (
                folder / "USRDIR/EBOOT.BIN"
            ).is_file()

            matching_base = next(
                (
                    title["title_id"]
                    for title in titles
                    if folder.name == title["title_id"]
                ),
                None
            )

            installed.append({
                "directory": str(folder),
                "directory_name": folder.name,
                "matching_disc_title_id": matching_base,
                "param_sfo_present": sfo.is_file(),
                "param_sfo_category": category,
                "param_sfo_title_id": sfotitle,
                "eboot_present": eboot,
                "evidence_status": (
                    "metadata_present_unverified"
                    if metadata
                    else "unclassified_directory"
                ),
                "installed": None,
                "active": None,
            })

    return {
        "games": titles,
        "content_directories": installed,
    }


def wiiu_installed_inventory():
    records = []

    if not WIIU_TITLE.is_dir():
        return records

    for kind_root in sorted(
        WIIU_TITLE.iterdir(),
        key=lambda p: p.name
    ):
        if (
            kind_root.is_symlink()
            or not kind_root.is_dir()
        ):
            continue

        prefix = kind_root.name.lower()

        if prefix not in WIIU_TYPES:
            continue

        for folder in sorted(
            kind_root.iterdir(),
            key=lambda p: p.name
        )[:300]:

            if (
                folder.is_symlink()
                or not folder.is_dir()
                or not re.fullmatch(
                    r"[0-9a-fA-F]{8}",
                    folder.name
                )
            ):
                continue

            title_id = (
                prefix + folder.name.lower()
            )

            metafile = (
                folder / "meta/meta.xml"
            )

            meta = None

            if metafile.is_file():
                try:
                    meta = read_meta(metafile)
                except (
                    OSError,
                    ValueError,
                    ET.ParseError
                ):
                    pass

            meta_id = (
                (meta or {})
                .get("title_id", "")
                .lower()
                .removeprefix("0x")
            )

            records.append({
                "title_id": title_id,
                "base_title_id": (
                    "00050000" + title_id[8:]
                ),
                "kind": WIIU_TYPES[prefix],
                "directory": str(folder),
                "meta_xml_present": metafile.is_file(),
                "meta_title_id": meta_id,
                "metadata_conflict": bool(
                    meta_id and meta_id != title_id
                ),
                "code_directory": (
                    folder / "code"
                ).is_dir(),
                "content_directory": (
                    folder / "content"
                ).is_dir(),
                "evidence_status": (
                    "metadata_present_unverified"
                    if meta
                    else "unclassified_directory"
                ),
                "installed": None,
                "active": None,
            })

    return records


def wiiu_gamelist_inventory():
    records = []

    for root in WIIU_ROMS:
        gamelist = root / "gamelist.xml"

        if (
            not gamelist.is_file()
            or gamelist.stat().st_size > 10_000_000
        ):
            continue

        try:
            xml = ET.parse(gamelist).getroot()
        except (OSError, ET.ParseError):
            continue

        for game in xml.findall(".//game")[:300]:
            path_text = (
                game.findtext("path") or ""
            ).strip()

            if not path_text:
                continue

            path = Path(path_text)

            if not path.is_absolute():
                path = root / path

            records.append({
                "name": (
                    game.findtext("name") or ""
                ).strip(),
                "gamelist": str(gamelist),
                "referenced_path": path_text,
                "resolved_path": str(path),
                "referenced_path_exists": path.exists(),
                "identity_verified": False,
            })

    return records


def main():
    registry = json.loads(
        (BASE / "adapter-registry.json").read_text()
    )

    assert registry["adapter_status"] == "discovery_only"

    discovery = json.loads(
        (BASE / "discovery-report.json").read_text()
    )

    ps3 = ps3_inventory(discovery)
    cemu_titles = wiiu_installed_inventory()
    cemu_list = wiiu_gamelist_inventory()

    output = {
        "schema_version": 1,
        "read_only": True,
        "installed_status_confirmed": False,
        "rpcs3": ps3,
        "cemu": {
            "content_directories": cemu_titles,
            "gamelist_references": cemu_list,
        },
    }

    print("===== RPCS3 IDENTITIES =====")
    print("Distinct games:", len(ps3["games"]))

    for game in ps3["games"]:
        print(
            game["title_id"],
            "|",
            ", ".join(game["names"]),
            "| source paths:",
            len(game["source_locations"])
        )

    print()
    print("===== RPCS3 CONTENT EVIDENCE =====")

    for row in ps3["content_directories"]:
        print(
            row["directory_name"],
            "| SFO:", row["param_sfo_present"],
            "| EBOOT:", row["eboot_present"],
            "| matching game:",
            row["matching_disc_title_id"] or "none",
            "| status: UNVERIFIED"
        )

    print()
    print("===== CEMU CONTENT EVIDENCE =====")
    print(
        "Individual title directories:",
        len(cemu_titles)
    )

    for row in cemu_titles[:30]:
        print(
            row["title_id"],
            row["kind"],
            "| meta.xml:",
            row["meta_xml_present"],
            "| status: UNVERIFIED"
        )

    print()
    print("===== CEMU GAMELIST REFERENCES =====")
    print("References:", len(cemu_list))

    for row in cemu_list[:30]:
        print(
            row["name"] or "(unnamed)",
            "| target exists:",
            row["referenced_path_exists"]
        )
        print(" ", row["resolved_path"])

    report = BASE / "identity-inventory.json"

    with report.open("x", encoding="utf-8") as handle:
        json.dump(
            output,
            handle,
            indent=2,
            ensure_ascii=False
        )
        handle.write("\n")

    print()
    print("===== REPORT =====")
    print(report)
    print(
        "PASS: No emulator installations "
        "or registrations modified"
    )


if __name__ == "__main__":
    main()
