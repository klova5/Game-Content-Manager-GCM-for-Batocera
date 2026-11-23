#!/usr/bin/env python3
"""GCM content inspector: identifies update and DLC files by their contents (not their names), assigns each
to its console and game, and reports it. Read-only: it reads file headers only and changes nothing.

  python3 gcm-content-inspect.py             inspect, write the inspector report, print a summary
  python3 gcm-content-inspect.py --dry-run   inspect and print; write nothing

Recognised formats (from the file's own header, whatever its name or extension):
  Lichii NUP (Switch 2)  every package inside: base / update / DLC, title ID, version, title, display version
  Switch NSP / NSZ       PFS0 package; title ID from its ticket, else from the file name
  Switch XCI / XCZ       cartridge image
  PS4 PKG                content type and patch flags from the header, title and version from its param.sfo
  PS3 PKG                content type from the header metadata, content ID (includes the game ID)
  Wii WAD                title type from its TMD (00010005 = DLC), title ID and version
  Xbox 360 STFS          CON / LIVE / PIRS package: title ID and content type
Unpacked folders are read as one item each, not file by file:
  PS4 folder with sce_sys/param.sfo, PS3 folder with PARAM.SFO (CATEGORY gives game / update / DLC)
  Switch / Switch 2 loose dump (main.npdm, exefs, romfs): base/, updates/<version>/ and dlc/<title ID>/<n>/
Anything else is listed as "format not recognised", with the evidence that suggests it is an update or DLC.
"""
import hashlib, json, os, re, struct, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(os.environ.get("GCM_SCAN_ROOT", "/"))
U = ROOT / "userdata"
GCM = U / "system/add-ons/game-content-manager"
REGISTRY = GCM / "emulator-registry.json"
OUTPUT = GCM / "content-inspection.json"
REPORT = U / "system/logs/gcm-content-inspection.txt"
SYSTEM_LISTS = [ROOT / "usr/share/emulationstation/es_systems.cfg"]
ADDON_LISTS = U / "system/configs/emulationstation"
GCM_SYSTEMS = ("switch", "ps4", "ps3", "wiiu", "psvita", "xbox360", "3ds", "wii", "psp")
SKIP_DIRS = {"romfs", "exefs", "images", "videos", "manuals", "media", "downloaded_images", "downloaded_videos", ".gcm-uninstalled"}
MAX_FILES = 20000
NUP_KINDS = {0: "base", 1: "update", 2: "dlc"}
TAG_UPDATE = re.compile(r"\[(?:upd|update|patch)\]|\bupdate\b|\bpatch\b|\[v[1-9]\d*\]|-patch\b|-update\b", re.I)
NOT_CONTENT = (".keys", ".key", ".lua", ".ini", ".log", ".sfo", ".dat", ".db", ".nfo", ".md", ".html", ".url", ".bat", ".py",
               ".srm", ".sav", ".state", ".cht", ".bps", ".ips", ".ups", ".ttarch2", ".m3u")
MIN_GUESS_SIZE = 64 << 10  # a file under 64 KiB is not offered as possible content
UNIT_FILE_LIMIT = 200000
TAG_DLC = re.compile(r"\[dlc\]|\bdlc\b|\badd-?on\b|\baoc\b", re.I)
SWITCH_ID = re.compile(r"\[?\b(0[14]00[0-9A-Fa-f]{12})\b\]?")
VERSION_TAG = re.compile(r"\[v(\d+)\]", re.I)


def read_at(f, offset, size):
    f.seek(offset)
    return f.read(size)


# ---------------------------------------------------------------- formats
def switch_kind(tid):
    low = tid & 0xFFF
    if low == 0:
        return "base", tid
    if low == 0x800:
        return "update", tid - 0x800
    return "dlc", (tid & ~0xFFF) - 0x1000


def console_for_switch_id(tid):
    top = tid >> 56
    return {0x01: "switch", 0x04: "switch2"}.get(top)


def parse_nup(f, size):
    sb = read_at(f, 0, 0x80)
    if len(sb) < 0x80 or sb[:4] != b"NUP\x01":
        return None
    header_size, _ver, toc_off, toc_size = struct.unpack_from("<HHQQ", sb, 4)
    toc_sha = sb[0x18:0x38]
    blob_off, file_size, pkg_count, flags = struct.unpack_from("<QQII", sb, 0x38)
    if header_size < 0x80 or flags or file_size != size or not (0x80 <= toc_off <= blob_off <= size) or toc_size > blob_off - toc_off or toc_size > 64 << 20:
        return {"error": "NUP header is not valid"}
    toc = read_at(f, toc_off, toc_size)
    if len(toc) != toc_size or hashlib.sha256(toc).digest() != toc_sha:
        return {"error": "NUP table of contents does not match its checksum (damaged or modified file)"}
    magic, count = struct.unpack_from("<II", toc, 0)
    if magic != 0x01434F54 or count == 0 or count != pkg_count or 0x30 + count * 0x40 > len(toc):
        return {"error": "NUP table of contents is not valid"}
    packages = []
    for i in range(count):
        rec = toc[0x30 + i * 0x40: 0x70 + i * 0x40]
        tid, = struct.unpack_from("<Q", rec, 0)
        version, = struct.unpack_from("<I", rec, 0x14)
        kind, pflags = rec[0x18], rec[0x19]
        meta_off, meta_size = struct.unpack_from("<QI", rec, 0x20)
        pkg = {"kind": NUP_KINDS.get(kind, "unknown"), "title_id": "%016X" % tid, "version": version,
               "standalone_update": bool(pflags & 1)}
        if meta_off and meta_size and meta_off + meta_size <= size and meta_size <= 16 << 20:
            block, pos = read_at(f, meta_off, meta_size), 0
            while pos + 8 <= len(block):
                typ, _r, ln = struct.unpack_from("<HHI", block, pos)
                pos += 8
                if pos + ln > len(block):
                    break
                if typ in (0x4, 0x5):
                    pkg["display_version" if typ == 0x4 else "title"] = block[pos:pos + ln].decode("utf-8", "replace")
                pos += ln
        packages.append(pkg)
    return {"packages": packages}


def parse_pfs0(f, name):
    head = read_at(f, 0, 16)
    files, strsize = struct.unpack_from("<II", head, 4)
    if files == 0 or files > 1000 or strsize > 1 << 20:
        return None
    table = read_at(f, 16, files * 0x18 + strsize)
    pool = table[files * 0x18:]
    names = []
    for i in range(files):
        off, = struct.unpack_from("<I", table, i * 0x18 + 16)
        end = pool.find(b"\0", off)
        names.append(pool[off:end if end >= 0 else None].decode("utf-8", "replace"))
    tids = set()
    for n in names:
        m = re.fullmatch(r"([0-9a-fA-F]{32})\.(tik|cert)", n)
        if m and n.endswith(".tik"):
            tids.add(int(m.group(1)[:16], 16))
    source = "its ticket"
    if not tids:
        m = SWITCH_ID.search(name)
        if m:
            tids.add(int(m.group(1), 16))
            source = "its file name"
    return {"inner_files": len(names), "title_ids": sorted(tids), "id_source": source if tids else None}


def parse_sfo(data):
    """PARAM.SFO (PS3, PS4, PS Vita): {key: value}."""
    if len(data) < 20 or data[:4] != b"\0PSF":
        return None
    key_start, data_start, count = struct.unpack_from("<III", data, 8)
    out = {}
    for i in range(min(count, 512)):
        if 20 + i * 16 + 16 > len(data):
            break
        koff, fmt, ln, _mx, doff = struct.unpack_from("<HHIII", data, 20 + i * 16)
        k0 = key_start + koff
        key = data[k0:data.find(b"\0", k0)].decode("ascii", "replace")
        raw = data[data_start + doff: data_start + doff + ln]
        out[key] = struct.unpack_from("<I", raw)[0] if fmt == 0x0404 and len(raw) >= 4 else raw.rstrip(b"\0").decode("utf-8", "replace")
    return out


PS4_PATCH_FLAGS = 0x00100000 | 0x01000000 | 0x20000000 | 0x40000000  # first, delta, cumulative, subsequent patch
PS4_TYPES = {0x1A: "game", 0x1B: "dlc", 0x1C: "dlc", 0x1E: "game"}  # GD, AC, AL (licence only), DP


def parse_ps4_pkg(f, head, size):
    entries, = struct.unpack_from(">I", head, 0x10)
    table, = struct.unpack_from(">I", head, 0x18)
    ctype, cflags = struct.unpack_from(">II", head, 0x74)
    info = {"content_id": head[0x40:0x64].decode("ascii", "replace").strip("\0"), "content_type": ctype, "content_flags": cflags}
    kind = PS4_TYPES.get(ctype)
    if kind == "game":
        kind = "update" if cflags & PS4_PATCH_FLAGS else "base"
    info["kind"], info["kind_source"] = kind, "its header (content type %02X, flags %08X)" % (ctype, cflags)
    if 0 < entries <= 10000 and table + entries * 32 <= size:
        tab = read_at(f, table, entries * 32)
        for i in range(entries):
            eid, _n, _f1, _f2, off, ln = struct.unpack_from(">IIIIII", tab, i * 32)
            if eid == 0x1000 and 0 < ln <= 1 << 20 and off + ln <= size:
                sfo = parse_sfo(read_at(f, off, ln))
                if sfo:
                    info["title"] = sfo.get("TITLE")
                    info["version"] = sfo.get("APP_VER") or sfo.get("VERSION")
                    cat = {"gd": "base", "gp": "update", "ac": "dlc"}.get(str(sfo.get("CATEGORY", "")).lower())
                    if cat:
                        info["kind"], info["kind_source"] = cat, "its param.sfo (CATEGORY %s)" % sfo["CATEGORY"]
                break
    return info


PS3_TYPES = {0x4: "gamedata", 0x5: "base", 0xB: "dlc", 0x6: "base", 0x7: "base", 0xE: "base", 0xF: "base", 0x10: "base",
             0x12: "base", 0x14: "base", 0x9: "other", 0xA: "other", 0xD: "other"}
PS3_PATCH_NAME = re.compile(r"-A(\d{2})(\d{2})-V\d{4}", re.I)


def parse_ps3_pkg(f, head, size):
    meta_off, meta_count = struct.unpack_from(">II", head, 8)
    info = {"content_id": head[0x30:0x54].decode("ascii", "replace").strip("\0")}
    ctype = None
    if meta_off and meta_count <= 64 and meta_off < size:
        block, pos = read_at(f, meta_off, 0x1000), 0
        for _ in range(meta_count):
            if pos + 8 > len(block):
                break
            mid, ln = struct.unpack_from(">II", block, pos)
            if mid == 2 and ln >= 4:
                ctype, = struct.unpack_from(">I", block, pos + 8)
            pos += 8 + ln
    info["content_type"] = ctype
    info["kind"] = PS3_TYPES.get(ctype)
    return info


def parse_wad(f, head, size):
    hdr, _typ, cert, _r, tik, tmd = struct.unpack_from(">IIIIII", head, 0)
    align = lambda n: (n + 63) & ~63
    tmd_off = align(hdr) + align(cert) + align(tik)
    t = read_at(f, tmd_off, 0x1E0) if tmd >= 0x1E0 else b""
    if len(t) < 0x1E0:
        return None
    tid, = struct.unpack_from(">Q", t, 0x18C)
    ver, = struct.unpack_from(">H", t, 0x1DC)
    high, low = tid >> 32, tid & 0xFFFFFFFF
    code = low.to_bytes(4, "big").decode("ascii", "replace")
    kind = {0x00010005: "dlc", 0x00010001: "base", 0x00010002: "base", 0x00010004: "base", 0x00010008: "other"}.get(high, "other")
    return {"title_id": "%016X" % tid, "game_code": code, "version": ver, "kind": kind, "title_type": "%08X" % high}


def identify(path, size):
    """Returns (format, details) from the file's own bytes, or (None, None)."""
    try:
        with open(path, "rb") as f:
            head = f.read(0x400)
            if head[:4] == b"NUP\x01":
                return "nup", parse_nup(f, size)
            if head[:4] == b"PFS0":
                return "switch-package", parse_pfs0(f, path.name)
            if len(head) >= 0x104 and head[0x100:0x104] == b"HEAD":
                m = SWITCH_ID.search(path.name)
                return "switch-cartridge", {"title_ids": [int(m.group(1), 16)] if m else [], "id_source": "its file name" if m else None}
            if head[:4] == b"\x7fCNT":
                return "ps4-package", parse_ps4_pkg(f, head, size)
            if head[:4] == b"\x7fPKG":
                return "ps3-package", parse_ps3_pkg(f, head, size)
            if head[:8] in (b"\0\0\0\x20Is\0\0", b"\0\0\0\x20ib\0\0"):
                return "wii-wad", parse_wad(f, head, size)
            if head[0x18:0x1C] == b"\x5D\x1C\x9E\xA3":
                return "wii-disc", {"game_id": head[:6].decode("ascii", "replace")}
            if head[:4] == b"WBFS" and len(head) >= 0x206:
                return "wii-disc", {"game_id": read_at(f, 0x200, 6).decode("ascii", "replace")}
            if head[:4] in (b"RVZ\x01", b"WIA\x01") and len(head) >= 0x5E:
                return "wii-disc", {"game_id": head[0x58:0x5E].decode("ascii", "replace")}
            if head[:4] in (b"CON ", b"LIVE", b"PIRS"):
                meta = read_at(f, 0x344, 0x20)
                ctype = struct.unpack_from(">I", meta, 0)[0] if len(meta) >= 4 else 0
                tid = struct.unpack_from(">I", meta, 0x1C)[0] if len(meta) >= 0x20 else 0
                kind = {0x2: "dlc", 0xB0000: "update"}.get(ctype, "other")
                return "xbox360-package", {"title_id": "%08X" % tid, "kind": kind, "content_type": "%08X" % ctype}
    except OSError:
        return None, None
    return None, None


# ---------------------------------------------------------------- where to look
def consoles():
    """{console: {"roms": [folders], "emulators": [...]}} for GCM's consoles and every add-on console."""
    out = {}
    stock = [p for p in SYSTEM_LISTS if p.is_file()]
    addon = sorted(ADDON_LISTS.glob("es_systems*.cfg"))
    for f in stock + addon:
        try:
            root = ET.fromstring(f.read_text(errors="replace"))
        except (ET.ParseError, OSError):
            continue
        for s in root.iter("system"):
            name = (s.findtext("name") or "").strip()
            if not name or (name not in GCM_SYSTEMS and f not in addon):
                continue
            entry = out.setdefault(name, {"roms": [], "emulators": []})
            path = (s.findtext("path") or "").strip()
            if path:
                p = ROOT / path.lstrip("/")
                if p.is_dir() and p not in entry["roms"]:
                    entry["roms"].append(p)
            entry["emulators"] += [e.get("name") for e in s.iter("emulator") if e.get("name")]
    return out


def drive_dirs(console):
    """ROM folders for this console on other connected drives (e.g. a backup HDD at /media/GAMES):
    <drive>/roms/<console>, and for Switch also <drive>/roms/switch_update."""
    media = ROOT / "media"
    try:
        mounts = {line.split()[1] for line in open(os.environ.get("GCM_MOUNTS", "/proc/mounts")) if len(line.split()) > 1}
    except OSError:
        mounts = set()
    out = []
    try:
        drives = sorted(d for d in media.iterdir() if d.is_dir() and not d.is_symlink()
                        and ("/" + str(d.relative_to(ROOT)) in mounts or str(d) in mounts))
    except OSError:
        return out
    for d in drives:
        for name in ([console, "switch_update"] if console == "switch" else [console]):
            for base in ("roms", "batocera-roms"):
                p = d / base / name
                if p.is_dir() and not p.is_symlink():
                    out.append(p)
    return out


def content_dirs(console):
    """Extra folders the console's emulators load content from (yuzu-family external content folders)."""
    dirs = []
    try:
        reg = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        reg = {}
    for row in reg.get("emulators", []):
        if row.get("system") != console or row.get("family") != "yuzu" or not row.get("config"):
            continue
        cfg = ROOT / row["config"]
        try:
            text = cfg.read_text(errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"external_content_dirs\\\d+\\path\s*=\s*(.+)", text):
            p = ROOT / m.group(1).strip().strip('"').lstrip("/")
            if p.is_dir() and p not in dirs:
                dirs.append(p)
    return dirs


def child(d, name):
    """Case-insensitive child of folder d, or None."""
    try:
        for e in os.scandir(d):
            if e.name.lower() == name.lower():
                return Path(e.path)
    except OSError:
        pass
    return None


def npdm_dir(d):
    """d itself if it is one loose program dump (main.npdm, directly or in exefs/), else None."""
    for where in (d, child(d, "exefs")):
        if where and child(where, "main.npdm"):
            return where
    return None


def unit_kind(d, files):
    """What an unpacked folder is, so it is read as one item and not walked file by file."""
    lower = {n.lower() for n in files}
    if child(d, "sce_sys") and child(child(d, "sce_sys"), "param.sfo"):
        return "ps4-folder"
    if "param.sfo" in lower or (child(d, "ps3_game") and child(child(d, "ps3_game"), "param.sfo")):
        return "ps3-folder"
    updates = child(d, "updates")
    try:
        update_dumps = updates and any(e.is_dir() and npdm_dir(Path(e.path)) for e in os.scandir(updates))
    except OSError:
        update_dumps = False
    if npdm_dir(d) or (child(d, "base") and npdm_dir(child(d, "base"))) or update_dumps or child(d, "romfs"):
        return "loose-dump"
    return None


def walk(folders, stats):
    """Yields ("file", path) and ("folder", path, kind); stops at MAX_FILES files."""
    seen = set()  # folders by (device, inode), so a bind-mounted folder is read once
    for top in folders:
        for d, subdirs, files in os.walk(top, followlinks=True):
            subdirs[:] = sorted(s for s in subdirs if s.lower() not in SKIP_DIRS and not s.startswith("."))
            try:
                st = os.stat(d)
                real = (st.st_dev, st.st_ino)
            except OSError:
                subdirs[:] = []
                continue
            if real in seen:
                subdirs[:] = []
                continue
            seen.add(real)
            kind = None if Path(d) == Path(top) else unit_kind(Path(d), files)
            if kind:
                subdirs[:] = []
                stats["unpacked"] += 1
                yield ("folder", Path(d), kind)
                continue
            for n in files:
                if n.startswith(".") or n.lower().endswith((".xml", ".txt", ".png", ".jpg", ".cfg", ".json", ".sh")):
                    continue
                stats["files"] += 1
                rel = Path(d).relative_to(top).parts
                busy = stats.setdefault("busiest", {})
                key = str(Path(top) / rel[0]) if rel else str(top)
                busy[key] = busy.get(key, 0) + 1
                if stats["files"] > MAX_FILES:
                    stats["stopped"] = True
                    return
                yield ("file", Path(d) / n)


# ---------------------------------------------------------------- classification
def clean_name(stem):
    name = re.sub(r"\s*[\[\(][^\]\)]*[\]\)]", "", stem)
    return re.sub(r"\s+", " ", name).strip(" -_.") or stem


def npdm_program_id(where):
    try:
        data = (child(where, "main.npdm")).read_bytes()[:1 << 20]
        if data[:4] != b"META":
            return None
        aci, = struct.unpack_from("<I", data, 0x70)
        if data[aci:aci + 4] != b"ACI0":
            return None
        return struct.unpack_from("<Q", data, aci + 0x10)[0]
    except (OSError, struct.error, TypeError):
        return None


def folder_rows(console, d, kind):
    base = {"file": "/" + str(d.relative_to(ROOT)), "format": kind, "console": console, "folder": True}
    if kind in ("ps4-folder", "ps3-folder"):
        sfo_path = child(d, "sce_sys") if kind == "ps4-folder" else (child(d, "ps3_game") or d)
        sfo_path = child(sfo_path, "param.sfo") if sfo_path else None
        try:
            sfo = parse_sfo(sfo_path.read_bytes()[:1 << 20]) if sfo_path else None
        except OSError:
            sfo = None
        if not sfo:
            return [base | {"kind": "unreadable", "evidence": ["its PARAM.SFO could not be read"]}]
        cat = str(sfo.get("CATEGORY", ""))
        cats = {"gd": "base", "gp": "update", "ac": "dlc"} if kind == "ps4-folder" else {"DG": "base", "HG": "base", "GD": "update"}
        what = cats.get(cat.lower() if kind == "ps4-folder" else cat.upper(), "other")
        note = "unpacked folder; its PARAM.SFO says CATEGORY %s" % cat
        if what == "update" and kind == "ps4-folder" and child(d, "eboot.bin") and not re.search(r"update|patch", d.name, re.I) \
                and not child(child(d, "sce_sys"), "playgo-chunk.dat.patch") and child(d, "sce_sys"):
            # a game folder whose files already include an update (the update was merged into the game)
            what, note = "base", note + ": a game that already includes update %s" % (sfo.get("APP_VER") or "")
        return [base | {"kind": what, "game_id": sfo.get("TITLE_ID", ""), "title": sfo.get("TITLE"),
                        "version": sfo.get("APP_VER") or sfo.get("VERSION"), "console": "ps4" if kind == "ps4-folder" else "ps3",
                        "evidence": [note]}]
    # Switch / Switch 2 loose dump: bare, or base/ + updates/<version>/ + dlc/<title ID>/<n>/
    rows, title = [], clean_name(d.name if d.name.lower() not in ("base", "updates", "dlc") else d.parent.name)
    parts = []
    if npdm_dir(d):
        parts.append(("base", d, None))
    elif child(d, "romfs") and not (child(d, "base") or child(d, "updates") or child(d, "dlc")):
        m = SWITCH_ID.search(d.name)
        if not m:
            return [base | {"kind": "unknown", "evidence": ["loose romfs folder without a program file or title ID"]}]
        tid = int(m.group(1), 16)
        what, gid = switch_kind(tid)
        return [base | {"kind": what, "title_id": "%016X" % tid, "game_id": "%016X" % gid,
                        "console": console_for_switch_id(tid) or console, "evidence": ["loose romfs folder; title ID from its name"]}]
    for sub in ("base",):
        if child(d, sub) and npdm_dir(child(d, sub)):
            parts.append(("base", child(d, sub), None))
    upd = child(d, "updates")
    if upd:
        for e in sorted(os.scandir(upd), key=lambda e: e.name):
            if e.is_dir():
                parts.append(("update", Path(e.path), int(e.name) if e.name.isdigit() else e.name))
    dlc = child(d, "dlc")
    if dlc:
        for e in sorted(os.scandir(dlc), key=lambda e: e.name):
            if e.is_dir():
                for n in sorted(os.scandir(e.path), key=lambda n: n.name) or [e]:
                    if n.is_dir():
                        parts.append(("dlc", Path(n.path), e.name))
    game = None
    for what, where, extra in parts:
        if what != "dlc" and npdm_dir(where):
            game = game or npdm_program_id(npdm_dir(where))
    for what, where, extra in parts:
        tid = None
        if what == "dlc":
            tid = int(extra, 16) if re.fullmatch(r"[0-9a-fA-F]{16}", extra or "") else None
        elif npdm_dir(where):
            pid = npdm_program_id(npdm_dir(where))
            tid = None if pid is None else (pid & ~0xFFF) | (0x800 if what == "update" else 0)
        gid = (tid & ~0xFFF) - 0x1000 if what == "dlc" and tid else (tid & ~0xFFF if tid else game)
        row = base | {"file": "/" + str(where.relative_to(ROOT)), "kind": what, "title": title if what == "base" else None,
                      "title_id": "%016X" % tid if tid else None, "game_id": "%016X" % gid if gid else None,
                      "console": (console_for_switch_id(tid or gid or 0) or console),
                      "version": extra if what == "update" else None,
                      "evidence": ["loose dump folder (%s)" % ("main.npdm program ID" if tid and what != "dlc" else
                                                               "dlc/<title ID> folder" if tid else "title ID not readable")]}
        rows.append(row)
    return rows


def classify(console, path):
    try:
        size = path.stat().st_size
    except OSError:
        return []
    fmt, info = identify(path, size)
    rel = str(path.relative_to(ROOT))
    base = {"file": "/" + rel, "size": size, "format": fmt or "unknown", "console": console}
    if fmt == "nup":
        if not info or "error" in info:
            return [base | {"kind": "unreadable", "evidence": [info.get("error") if info else "NUP header unreadable"]}]
        rows = []
        for pkg in info["packages"]:
            tid = int(pkg["title_id"], 16)
            game = tid if pkg["kind"] == "base" else (tid - 0x800 if pkg["kind"] == "update" else (tid & ~0xFFF) - 0x1000)
            rows.append(base | {"kind": pkg["kind"], "title_id": pkg["title_id"], "game_id": "%016X" % game,
                                "console": console_for_switch_id(tid) or console, "version": pkg["version"],
                                "display_version": pkg.get("display_version"), "title": pkg.get("title"),
                                "standalone_update": pkg["standalone_update"],
                                "evidence": ["NUP package record (%s)" % pkg["kind"] + (", complete standalone update" if pkg["standalone_update"] else "")]})
        return rows
    if fmt in ("switch-package", "switch-cartridge"):
        if not info or not info.get("title_ids"):
            if fmt == "switch-cartridge":
                return [base | {"kind": "base", "title": clean_name(path.stem),
                                "evidence": ["cartridge image; its title ID is encrypted and not in its name"]}]
            return [base | {"kind": "unknown", "evidence": ["Switch package without a readable title ID"]}]
        rows = []
        for tid in info["title_ids"]:
            kind, game = switch_kind(tid)
            if fmt == "switch-cartridge":
                kind = "base"
            v = VERSION_TAG.search(path.name)
            rows.append(base | {"kind": kind, "title_id": "%016X" % tid, "game_id": "%016X" % game,
                                "console": console_for_switch_id(tid) or console, "version": int(v.group(1)) if v else None,
                                "evidence": ["%s; title ID from %s" % ("PFS0 package" if fmt == "switch-package" else "cartridge image", info["id_source"])]})
        return rows
    if fmt in ("ps4-package", "ps3-package"):
        cid = info.get("content_id", "")
        game = cid.split("-")[1].split("_")[0] if cid.count("-") >= 2 else ""
        kind, version = info.get("kind"), info.get("version")
        if fmt == "ps4-package":
            evidence = "PS4 package %s; type from %s" % (cid, info.get("kind_source"))
        else:
            evidence = "PS3 package %s; content type %s" % (cid, "%X" % info["content_type"] if info.get("content_type") is not None else "unreadable")
            if kind == "gamedata":
                m = PS3_PATCH_NAME.search(path.name) or PS3_PATCH_NAME.search(cid)
                patchy = m or re.search(r"patch|update", cid, re.I)
                kind = "update" if patchy else "dlc"
                if m:
                    version = "%s.%s" % (m.group(1), m.group(2))
                evidence += " (game data: %s)" % ("an update: its patch name" if m else "an update: its content ID" if patchy else "DLC")
        if not kind:
            kind = "update" if TAG_UPDATE.search(path.name) else "dlc" if TAG_DLC.search(path.name) else "package"
            evidence += "; header type unknown, guessed from its name"
        return [base | {"kind": kind, "content_id": cid, "game_id": game, "console": "ps4" if fmt == "ps4-package" else "ps3",
                        "title": info.get("title") if kind == "base" else None, "version": version, "evidence": [evidence]}]
    if fmt == "wii-wad":
        if not info:
            return [base | {"kind": "unreadable", "evidence": ["WAD title metadata could not be read"]}]
        return [base | {"kind": info["kind"], "title_id": info["title_id"], "game_id": info["game_code"].upper(), "console": "wii",
                        "version": info["version"], "title": clean_name(path.stem) if info["kind"] == "base" else None,
                        "evidence": ["WAD, title type %s" % info["title_type"]]}]
    if fmt == "wii-disc":
        return [base | {"kind": "base", "game_id": info["game_id"][:4].upper(), "title": clean_name(path.stem), "console": "wii",
                        "evidence": ["disc image, game ID %s" % info["game_id"]]}]
    if fmt == "xbox360-package":
        return [base | {"kind": info["kind"], "game_id": info["title_id"], "console": "xbox360",
                        "evidence": ["STFS package, content type %s" % info["content_type"]]}]
    # unknown format: evidence only, and only for files that could be content
    name = path.name
    if name.lower().endswith(NOT_CONTENT) or size < MIN_GUESS_SIZE:
        return []
    evidence, kind = [], None
    if TAG_UPDATE.search(name):
        kind = "update"; evidence.append("its name looks like an update")
    if TAG_DLC.search(name):
        kind = kind or "dlc"; evidence.append("its name looks like DLC")
    folder_hint = None
    for i, p in enumerate(path.parts[:-1]):
        low = p.lower()
        if low in ("dlc", "update", "updates", "patch", "patches", "addcont") or re.search(r"[-_ ](update|patch|dlc)$", low):
            folder_hint = Path(*path.parts[:min(i + 2, len(path.parts) - 1)])
            kind = kind or ("dlc" if "dlc" in low or "addcont" in low else "update")
            evidence.append("it is in an update/DLC folder")
            break
    if not kind:
        return []
    m = SWITCH_ID.search(name)
    row = base | {"kind": "possible-" + kind, "evidence": evidence}
    if folder_hint and not (TAG_UPDATE.search(name) or TAG_DLC.search(name)):
        row |= {"file": "/" + str(folder_hint.relative_to(ROOT)), "folder": True, "group": True}
    if m:
        row["game_id"] = m.group(1).upper()
    return [row]


def inspect():
    rows, scanned = [], {}
    media = "/" + str((ROOT / "media").relative_to(ROOT)) + "/"
    for console, info in sorted(consoles().items()):
        folders = info["roms"] + [d for d in content_dirs(console) if d not in info["roms"]]
        folders += [d for d in drive_dirs(console) if d not in folders]
        stats = {"files": 0, "unpacked": 0, "stopped": False}
        groups = {}
        for item in walk(folders, stats):
            new = folder_rows(console, item[1], item[2]) if item[0] == "folder" else classify(console, item[1])
            for r in new:
                if r.pop("group", False):
                    if r["file"] in groups:
                        groups[r["file"]]["files"] += 1
                        continue
                    r["files"] = 1
                    groups[r["file"]] = r
                rows.append(r)
        for r in groups.values():
            r["evidence"] = ["a folder of %d loose files in an update/DLC location; its format is not recognised" % r["files"]]
        if not stats["stopped"]:
            stats.pop("busiest", None)
        scanned[console] = {"folders": ["/" + str(p.relative_to(ROOT)) for p in folders]} | stats
    for r in rows:
        if r["file"].startswith(media):
            r["drive"] = r["file"][len(media):].split("/", 1)[0]
            r["evidence"].append("on drive %s (copy it to GCM to use it)" % r["drive"])
    names = {}
    for r in rows:
        if r.get("title") and r.get("title_id"):
            names.setdefault(r["title_id"], r["title"])
    for r in rows:
        if r.get("kind") == "base" and r.get("title_id") in names and (not r.get("title") or r.get("format") == "loose-dump"):
            r["title"] = names[r["title_id"]]
    games = {}
    for r in rows:
        if r.get("kind") == "base" and r.get("game_id"):
            games.setdefault((r["console"], r["game_id"]), r.get("title") or clean_name(Path(r["file"]).stem))
    for r in rows:
        if r.get("kind") in ("update", "dlc") and r.get("game_id"):
            r["game"] = games.get((r["console"], r["game_id"]))
            if not r["game"]:
                r["evidence"].append("no matching game found (not on this machine, or a different region)")
    # the same item in more than one place is listed once, with its other copies
    first, kept = {}, []
    for r in rows:
        key = (r["console"], r.get("kind"), r.get("title_id") or r.get("content_id"), r.get("version"), r.get("size"))
        if key[2] and r.get("kind") in ("base", "update", "dlc") and key in first:
            first[key].setdefault("copies", []).append(r["file"])
            continue
        first[key] = r
        kept.append(r)
    rows = kept
    return {"schema": "GCM_CONTENT_INSPECTION_V1", "inspected": time.strftime("%Y-%m-%d %H:%M:%S"),
            "consoles": scanned, "content": rows}


def report(res):
    lines = ["GCM content inspection  %s" % res["inspected"], ""]
    for console in sorted(res["consoles"]):
        rows = [r for r in res["content"] if r["console"] == console]
        c = res["consoles"][console]
        if not c["folders"]:
            lines.append("%s: no ROM folder found" % console.upper())
            lines.append("")
            continue
        lines.append("%s: %d files and %d unpacked folders checked in %s" % (console.upper(), c["files"], c["unpacked"], ", ".join(c["folders"])))
        if c.get("stopped"):
            lines.append("  STOPPED after %d files: some folders were not checked" % MAX_FILES)
            for folder, n in sorted(c.get("busiest", {}).items(), key=lambda kv: -kv[1])[:3]:
                lines.append("    %d files in %s" % (n, "/" + str(Path(folder).relative_to(ROOT))))
        for kind in ("base", "update", "dlc", "package", "other", "possible-update", "possible-dlc", "unreadable", "unknown"):
            sel = [r for r in rows if r.get("kind") == kind]
            if not sel:
                continue
            lines.append("  %s (%d)" % (kind.upper(), len(sel)))
            for r in sel[:12]:
                ver = " v%s" % r["version"] if r.get("version") is not None else ""
                if r.get("display_version"):
                    ver += " (%s)" % r["display_version"]
                who = r.get("title") or r.get("game") or ""
                if r.get("copies"):
                    ver += "  [%d copies]" % (len(r["copies"]) + 1)
                lines.append("    %-18s %s%s  %s" % (r.get("title_id") or r.get("game_id") or "", who, ver, ("/".join(r["file"].rsplit("/", 2)[-2:]) if r.get("folder") else r["file"].rsplit("/", 1)[-1])[:70]))
                lines.append("      " + "; ".join(r["evidence"]))
            if len(sel) > 12:
                lines.append("    ... and %d more" % (len(sel) - 12))
        lines.append("")
    return "\n".join(lines)


def main():
    res = inspect()
    text = report(res)
    print(text)
    if "--dry-run" in sys.argv:
        print("DRY RUN: nothing was written")
        return
    for path, data in ((OUTPUT, json.dumps(res, indent=1) + "\n"), (REPORT, text + "\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name("." + path.name + ".tmp")
        tmp.write_text(data)
        os.replace(tmp, path)
    print("PASS: %d items identified; report %s" % (len(res["content"]), REPORT))


if __name__ == "__main__":
    main()
