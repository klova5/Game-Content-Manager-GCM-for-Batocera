"""Read-only RPCS3 game and installed-content metadata discovery."""
from pathlib import Path
import struct

ROOTS = (
    ("installed", Path("/userdata/system/configs/rpcs3/dev_hdd0/game")),
    ("internal_rom", Path("/userdata/roms/ps3")),
    ("external_rom", Path("/media/easystore/emulators/roms/ps3")),
)

def read_sfo(path):
    if not path.is_file() or path.stat().st_size > 2_000_000:
        return None
    raw = path.read_bytes()
    if len(raw) < 20:
        return None
    magic, version, keys, values, count = struct.unpack_from(
        "<4sIIII", raw, 0
    )
    if magic != b"\x00PSF" or count > 512 or 20 + count * 16 > len(raw):
        return None
    result = {}
    for i in range(count):
        key_rel, fmt, size, maximum, data_rel = struct.unpack_from(
            "<HHIII", raw, 20 + i * 16
        )
        kpos = keys + key_rel
        dpos = values + data_rel
        if kpos >= len(raw) or dpos + size > len(raw):
            continue
        kend = raw.find(b"\x00", kpos, min(len(raw), kpos + 128))
        if kend < 0:
            continue
        key = raw[kpos:kend].decode("utf-8", "replace")
        value = raw[dpos:dpos + size]
        if fmt == 0x0404 and len(value) >= 4:
            result[key] = struct.unpack_from("<I", value)[0]
        else:
            result[key] = value.split(b"\x00", 1)[0].decode(
                "utf-8", "replace"
            )
    return result

def discover():
    results, seen = [], set()
    for location, root in ROOTS:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.name)[:300]:
            if child.is_symlink() or not child.is_dir():
                continue
            candidates = (
                child / "PARAM.SFO",
                child / "PS3_GAME/PARAM.SFO",
            )
            for sfo in candidates:
                if not sfo.is_file() or str(sfo) in seen:
                    continue
                seen.add(str(sfo))
                try:
                    meta = read_sfo(sfo)
                except (OSError, ValueError, struct.error):
                    meta = None
                if not meta:
                    continue
                category = str(meta.get("CATEGORY", ""))
                classification = {
                    "DG": "disc_game",
                    "HG": "installed_game",
                    "GD": "game_data_ambiguous",
                }.get(category, "unclassified")
                results.append({
                    "emulator": "rpcs3",
                    "location": location,
                    "title_id": str(meta.get("TITLE_ID", "")),
                    "title": str(meta.get("TITLE", "")),
                    "version": str(meta.get("APP_VER", "")),
                    "category": category,
                    "classification": classification,
                    "metadata_path": str(sfo),
                })
    return results
