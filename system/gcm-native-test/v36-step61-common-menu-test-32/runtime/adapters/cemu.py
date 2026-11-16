"""Read-only Wii U installed-title and ROM metadata discovery."""
from pathlib import Path
from collections import deque
import re
import xml.etree.ElementTree as ET

ROOTS = (
    ("installed", Path("/userdata/saves/wiiu/usr/title")),
    ("internal_rom", Path("/userdata/roms/wiiu")),
    ("external_rom", Path("/media/easystore/emulators/roms/wiiu")),
)

TYPES = {
    "00050000": "base_game",
    "0005000e": "update",
    "0005000c": "dlc",
}

def read_meta(path):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return None
    root = ET.fromstring(path.read_bytes())
    wanted = {
        "title_id", "title_version", "longname_en", "product_code"
    }
    meta = {}
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in wanted and node.text:
            meta[tag] = node.text.strip()
    return meta

def mlc_path_id(path):
    parts = [part.lower() for part in path.parts]
    for i in range(len(parts) - 3):
        if parts[i:i+2] == ["usr", "title"]:
            upper, lower = parts[i+2:i+4]
            if (
                re.fullmatch(r"[0-9a-f]{8}", upper)
                and re.fullmatch(r"[0-9a-f]{8}", lower)
            ):
                return upper + lower
    return ""

def discover():
    results, seen = [], set()
    for location, root in ROOTS:
        if not root.is_dir():
            continue
        queue = deque([(root, 0)])
        visited = 0
        while queue and visited < 1500:
            directory, depth = queue.popleft()
            visited += 1
            if directory.is_symlink():
                continue
            if directory.name.lower() == "meta":
                xml = directory / "meta.xml"
                if xml.is_file() and str(xml) not in seen:
                    seen.add(str(xml))
                    try:
                        meta = read_meta(xml)
                    except (OSError, ValueError, ET.ParseError):
                        meta = None
                    if meta is not None:
                        title_id = meta.get("title_id", "").lower()
                        title_id = title_id.removeprefix("0x")
                        path_id = mlc_path_id(xml)
                        if not re.fullmatch(r"[0-9a-f]{16}", title_id):
                            title_id = path_id
                        kind = TYPES.get(title_id[:8], "unclassified")
                        results.append({
                            "emulator": "cemu",
                            "location": location,
                            "title_id": title_id,
                            "base_title_id": (
                                "00050000" + title_id[8:]
                                if len(title_id) == 16 else ""
                            ),
                            "title": meta.get("longname_en", ""),
                            "version": meta.get("title_version", ""),
                            "classification": kind,
                            "metadata_conflict": bool(
                                path_id and title_id != path_id
                            ),
                            "metadata_path": str(xml),
                        })
            if depth >= 6:
                continue
            try:
                children = [
                    p for p in directory.iterdir()
                    if p.is_dir() and not p.is_symlink()
                ]
            except OSError:
                continue
            for child in sorted(children, key=lambda p: p.name)[:400]:
                queue.append((child, depth + 1))
    return results
