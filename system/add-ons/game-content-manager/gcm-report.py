#!/usr/bin/env python3
"""GCM report: what the emulator scanner and the content inspector found, for the GAME CONTENT MANAGER page
in the ES main menu, the manual "install as-is" for files GCM cannot identify, and MANUAL CONTENT for consoles
GCM does not manage.

The page is built from these lines (one per row), so the frontend needs no change when this file grows:
  GROUP\t<title>
  ROW\t<label>\t<value>
  PAGE\t<label>\t<value>\t<page title>\t<arguments>            opens a page from this tool
  ACTION\t<label>\t<value>\t<question>\t<arguments>            asks, runs this tool, shows its OK/ERROR line
Commands: summary | details --system S --emulator E | manual | manual-install --token T | manual-remove --token T | rescan
          mc | mc-console --console C | mc-file / mc-games --console C --token T | mc-browse --console C --token T --folder F
          mc-mark --console C --token T --kind game|update|dlc|clear | mc-pair ... --game G|none | mc-copy ... --folder F
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.environ.get("GCM_SCAN_ROOT", "/"))
U = ROOT / "userdata"
GCM = U / "system/add-ons/game-content-manager"
REGISTRY = GCM / "emulator-registry.json"
INSPECTION = GCM / "content-inspection.json"
SCANNER = GCM / "gcm-emulator-scan.py"
INSPECTOR = GCM / "gcm-content-inspect.py"
CONTENT = U / "system/game-content"
MANIFEST = CONTENT / "manual-installs.json"
MAX_ROWS = 100

STATUS = {
    "built-in": "SUPPORTED",
    "compatible": "SUPPORTED VIA %s",
    "compatible-own-storage": "NEEDS WORK: OWN STORAGE",
    "new-console": "NEEDS WORK: NEW CONSOLE",
    "excluded": "SKIPPED",
    "default": "NOT SUPPORTED",
}
NEEDS_WORK = ("compatible-own-storage", "new-console")
# GCM covers only consoles that have updates or DLC: the ones it manages, these (their games had updates or DLC),
# and add-on consoles whose emulator the scanner found to be of an update/DLC-capable family (e.g. a Switch fork).
MANAGED = ("switch", "ps4", "ps3", "wiiu", "psvita", "xbox360", "3ds", "wii", "psp", "switch2")
UPDATE_DLC_CONSOLES = ("xbox", "windows", "ps5", "xboxone", "xboxseries")


def has_updates_or_dlc(system, registry=None):
    if system in MANAGED or system in UPDATE_DLC_CONSOLES:
        return True
    rows = (registry if registry is not None else read(REGISTRY, {})).get("emulators", [])
    return any(r.get("system") == system and r.get("family") for r in rows)


def clean(v):
    """One field: no tabs; line breaks travel as a literal \\n that the page turns back into new lines."""
    return "\\n".join(" ".join(part.split()) for part in str(v).replace("\t", " ").split("\n"))[:600]
def out(*fields): print("\t".join(clean(f) for f in fields))


def read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def token(path): return hashlib.sha256(str(path).encode()).hexdigest()[:32]


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def target_dir(console, kind):
    """Where a file GCM cannot identify is copied as-is, and what that means."""
    base = kind.replace("possible-", "") if kind.startswith("possible-") else "unknown"
    if console == "switch" and base in ("update", "dlc"):
        return CONTENT / "switch" / ("updates" if base == "update" else "dlc"), "CITRON READS THIS FOLDER"
    return CONTENT / console / "manual" / base, "KEPT FOR YOU; THE EMULATOR MAY NOT READ THIS FOLDER"


def label_of(r):
    return {"possible-update": "POSSIBLE UPDATE", "possible-dlc": "POSSIBLE DLC", "unknown": "UNKNOWN", "unreadable": "DAMAGED"}.get(r.get("kind"), str(r.get("kind")).upper())


def unidentified(inspection, manifest):
    targets = {m["target"] for m in manifest.values()} | {m["source"] for m in manifest.values()}
    rows = []
    for r in inspection.get("content", []):
        if r.get("kind") not in ("possible-update", "possible-dlc", "unknown"):
            continue
        if r["file"] in targets:
            continue
        rows.append(r)
    return rows


# ---------------------------------------------------------------- pages
def summary():
    reg = read(REGISTRY, {})
    ins = read(INSPECTION, {})
    manifest = read(MANIFEST, {})
    rows = [r for r in reg.get("emulators", []) if r.get("status") != "excluded" and has_updates_or_dlc(r.get("system"), reg)]
    if not reg:
        out("GROUP", "EMULATORS AND CONSOLES")
        out("ROW", "NO SCAN YET", "USE RESCAN NOW")
    else:
        work = [r for r in rows if r.get("status") in NEEDS_WORK]
        rest = [r for r in rows if r.get("status") not in NEEDS_WORK]
        if work:
            out("GROUP", "NEEDS WORK BEFORE GCM CAN MANAGE IT")
            for r in work:
                emu_row(r)
        out("GROUP", "EMULATORS (SCANNED %s)" % reg.get("scanned", "?"))
        for r in sorted(rest, key=lambda r: (r.get("status") != "built-in" and r.get("status") != "compatible", r["system"], r["emulator"])):
            emu_row(r)
    out("GROUP", "CONTENT FOUND (CHECKED %s)" % ins.get("inspected", "NEVER"))
    content = ins.get("content", [])
    consoles = sorted({r["console"] for r in content})
    if not consoles:
        out("ROW", "NOTHING CHECKED YET", "USE RESCAN NOW")
    for c in consoles:
        sel = [r for r in content if r["console"] == c]
        parts = []
        for kind, name in (("base", "GAMES"), ("update", "UPDATES"), ("dlc", "DLC")):
            n = sum(r.get("kind") == kind for r in sel)
            if n:
                parts.append("%d %s" % (n, name))
        drive = sum(1 for r in sel if r.get("drive") and r.get("kind") in ("update", "dlc"))
        if drive:
            parts.append("%d ON A BACKUP DRIVE" % drive)
        out("ROW", c.upper(), " · ".join(parts) or "NO UPDATES OR DLC")
    out("GROUP", "FILES GCM CANNOT IDENTIFY")
    pending = unidentified(ins, manifest)
    out("PAGE", "NOT IDENTIFIED", "%d FILES" % len(pending), "FILES GCM CANNOT IDENTIFY", "manual")
    if manifest:
        out("PAGE", "COPIED AS-IS BY YOU", "%d FILES" % len(manifest), "FILES GCM CANNOT IDENTIFY", "manual")
    out("GROUP", "WINDOWS GAMES")
    out("PAGE", "MANUAL CONTENT", "MARK, PAIR AND COPY FILES YOURSELF", "MANUAL CONTENT", "mc")
    out("GROUP", "")
    out("ACTION", "RESCAN NOW", "", "SCAN EMULATORS AND CONTENT NOW?\n\nTHIS CAN TAKE A FEW MINUTES.", "rescan")


def emu_row(r):
    status = r.get("status", "default")
    value = STATUS.get(status, status.upper())
    if "%s" in value:
        value = value % str(r.get("backend") or r.get("route_as") or "").upper()
    out("PAGE", "%s · %s" % (r["system"].upper(), r["emulator"].upper()), value, r["emulator"].upper(),
        "details --system %s --emulator %s" % (r["system"], r["emulator"]))


def details(system, emulator):
    reg = read(REGISTRY, {})
    found = [r for r in reg.get("emulators", []) if r.get("system") == system and r.get("emulator") == emulator]
    if not found:
        out("ERROR", "This emulator is not in the last scan; use RESCAN NOW"); return
    r = found[0]
    status = r.get("status", "default")
    out("ROW", "CONSOLE", system.upper())
    out("ROW", "STATUS", (STATUS.get(status, status) % str(r.get("backend") or "").upper()) if "%s" in STATUS.get(status, "") else STATUS.get(status, status.upper()))
    if r.get("family"):
        out("ROW", "FAMILY", r["family"].upper())
    if r.get("backend") and status in ("built-in", "compatible"):
        out("ROW", "HANDLED BY", "GCM'S %s BACKEND" % r["backend"].upper())
    out("GROUP", "WHY")
    for i, e in enumerate(r.get("evidence", []), 1):
        out("ROW", str(i), e.upper())
    out("GROUP", "WHAT IT NEEDS")
    need = {
        "built-in": "NOTHING: GCM MANAGES ITS UPDATES AND DLC",
        "compatible": "NOTHING: IT SHARES A SUPPORTED EMULATOR'S STORAGE",
        "compatible-own-storage": "A GCM BACKEND POINTED AT ITS OWN FOLDERS",
        "new-console": "A GCM BACKEND FOR THIS CONSOLE AND ITS MENU ENTRIES",
        "excluded": "NOTHING: GCM LEAVES IT ALONE ON PURPOSE",
        "default": "GCM DOES NOT KNOW HOW IT STORES UPDATES AND DLC",
    }.get(status, "")
    out("ROW", "", need)


def manual():
    ins = read(INSPECTION, {})
    manifest = read(MANIFEST, {})
    if manifest:
        out("GROUP", "COPIED AS-IS BY YOU")
        for t, m in sorted(manifest.items(), key=lambda kv: kv[1]["target"]):
            out("ACTION", Path(m["target"]).name, "%s %s" % (m["console"].upper(), label_of(m)),
                "REMOVE THIS COPY?\n\n%s\n\nTHE ORIGINAL FILE IS NOT TOUCHED." % m["target"], "manual-remove --token " + t)
    pending = unidentified(ins, manifest)
    out("GROUP", "NOT IDENTIFIED (%d)" % len(pending))
    if not pending:
        out("ROW", "NONE", "")
    for r in pending[:MAX_ROWS]:
        name = Path(r["file"]).name
        value = "%s %s" % (r["console"].upper(), label_of(r))
        if r.get("folder"):
            out("ROW", name, value + " · FOLDER: COPY IT YOURSELF"); continue
        where, note = target_dir(r["console"], r.get("kind", "unknown"))
        why = "; ".join(r.get("evidence", []))[:200]
        out("ACTION", name, value,
            "GCM CANNOT CHECK THIS FILE (%s).\n\nIT WILL BE COPIED AS-IS TO\n%s\n(%s)\n\nCOPY IT ANYWAY?" % (why.upper(), "/" + str(where.relative_to(ROOT)), note),
            "manual-install --token " + token(r["file"]))
    if len(pending) > MAX_ROWS:
        out("ROW", "... AND %d MORE" % (len(pending) - MAX_ROWS), "SEE /USERDATA/SYSTEM/LOGS/GCM-CONTENT-INSPECTION.TXT")


# ---------------------------------------------------------------- actions
def save_manifest(m):
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_name("." + MANIFEST.name + ".tmp")
    tmp.write_text(json.dumps(m, indent=1) + "\n"); os.replace(tmp, MANIFEST)


def manual_install(t):
    ins = read(INSPECTION, {})
    manifest = read(MANIFEST, {})
    found = [r for r in unidentified(ins, manifest) if token(r["file"]) == t]
    if len(found) != 1:
        out("ERROR", "That file is no longer in the list; use RESCAN NOW"); return 1
    r = found[0]
    src = ROOT / r["file"].lstrip("/")
    if r.get("folder") or not src.is_file():
        out("ERROR", "The file is no longer there: " + r["file"]); return 1
    if src.stat().st_size != r.get("size"):
        out("ERROR", "The file changed since it was checked; use RESCAN NOW"); return 1
    where, _note = target_dir(r["console"], r.get("kind", "unknown"))
    where.mkdir(parents=True, exist_ok=True)
    dest = where / src.name
    if dest.exists():
        out("ERROR", "A file with that name is already there: /" + str(dest.relative_to(ROOT))); return 1
    free = shutil.disk_usage(where).free
    if free < src.stat().st_size + (64 << 20):
        out("ERROR", "Not enough free space for the copy"); return 1
    tmp = where / ("." + src.name + ".gcm-part")
    try:
        shutil.copyfile(src, tmp)
        with open(tmp, "rb+") as f:
            os.fsync(f.fileno())
        digest = sha_file(tmp)
        if digest != sha_file(src):
            raise OSError("the copy does not match the original")
        os.replace(tmp, dest)
    except OSError as exc:
        out("ERROR", "Copy failed: %s" % exc); return 1
    finally:
        if tmp.exists():
            tmp.unlink()
    manifest[token("/" + str(dest.relative_to(ROOT)))] = {
        "source": r["file"], "target": "/" + str(dest.relative_to(ROOT)), "console": r["console"], "kind": r.get("kind"),
        "size": dest.stat().st_size, "sha256": digest, "installed": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_manifest(manifest)
    out("OK", "Copied as-is to /" + str(dest.relative_to(ROOT)) + ". GCM could not check it; if the game misbehaves, remove it here.")
    return 0


def manual_remove(t):
    manifest = read(MANIFEST, {})
    m = manifest.get(t)
    if not m:
        out("ERROR", "That copy is not in GCM's list"); return 1
    dest = ROOT / m["target"].lstrip("/")
    if dest.exists():
        if sha_file(dest) != m["sha256"]:
            out("ERROR", "The copy changed since GCM made it, so it was left in place: " + m["target"]); return 1
        dest.unlink()
    del manifest[t]
    save_manifest(manifest)
    out("OK", "Removed " + m["target"] + ". The original file was not touched.")
    return 0


def rescan():
    results = []
    for tool, name in ((SCANNER, "emulators"), (INSPECTOR, "content")):
        if not tool.is_file():
            results.append(name + ": tool missing"); continue
        r = subprocess.run([sys.executable, "-B", str(tool)], capture_output=True, text=True, timeout=1800,
                           env=dict(os.environ))
        last = [l for l in r.stdout.splitlines() if l.startswith(("PASS", "STOP"))]
        results.append("%s: %s" % (name, (last[-1] if last else "exit %d" % r.returncode)))
    out("OK", "Scan finished. " + " / ".join(results))
    return 0


# ---------------------------------------------------------------- MANUAL CONTENT (4.1)
# Consoles GCM does not manage: list their files, let the user mark each one as a game, an update or DLC,
# pair updates and DLC with a game, and copy them as-is to a folder the user picks. GCM cannot check such
# files or confirm that the emulator uses them; every copy is checked against its original, recorded,
# and can be removed again.
import base64, re
import xml.etree.ElementTree as ET

MARKS = CONTENT / "manual-content.json"
# MANUAL CONTENT covers Windows only: its updates and DLC are files that go into the game's own folder, so
# copying them to a folder the user picks is the right way to install them.
MANUAL_CONTENT_CONSOLES = ("windows",)
SYSTEM_LISTS = [ROOT / "usr/share/emulationstation/es_systems.cfg"]
ADDON_LISTS = U / "system/configs/emulationstation"
SKIP = {"images", "videos", "manuals", "media", "downloaded_images", "downloaded_videos", "thumbnails", "boxart",
        "screenshots", ".gcm-uninstalled", "__pycache__"}
NOT_FILES = (".xml", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".md", ".nfo", ".json", ".cfg", ".ini",
             ".bak", ".old", ".srm", ".sav", ".state", ".keys")
MAX_FILES = 2000
COPY_ROOTS = ("roms", "saves", "bios", "system/game-content", "system/configs")  # under /userdata
UPDATE_TAG = re.compile(r"\[(?:upd|update|patch)\]|\bupdate\b|\bpatch\b|\(v\d|\[v[1-9]\d*\]|[-_ ](update|patch)\b", re.I)
DLC_TAG = re.compile(r"\[dlc\]|\(dlc\)|\bdlc\b|\badd-?on\b|\baoc\b|\bexpansion\b", re.I)
KIND_NAME = {"game": "GAME", "update": "UPDATE", "dlc": "DLC"}


def rel(p): return "/" + str(Path(p).relative_to(ROOT))
def ftok(p): return token(rel(p))
def dtok(p): return base64.urlsafe_b64encode(rel(p).encode()).decode().rstrip("=")


def from_dtok(t):
    try:
        text = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That folder is not valid")
    if not text.startswith("/") or os.path.normpath(text) != text or ".." in text.split("/"):
        raise ValueError("That folder is not valid")
    return ROOT / text.lstrip("/")


def allowed(folder):
    """A folder GCM may copy into: under /userdata/{roms,saves,bios,system/game-content,system/configs}, no links."""
    folder = Path(folder)
    if ".." in folder.parts or os.path.normpath(str(folder)) != str(folder):
        return False
    if not folder.is_dir() or any(q.is_symlink() for q in [folder, *folder.parents] if q != ROOT and str(q) != "/"):
        return False
    return any(folder == U / r or (U / r) in folder.parents for r in COPY_ROOTS)


def consoles_unmanaged():
    """{system: {"name": full name, "path": ROM folder}} for the consoles MANUAL CONTENT covers (Windows)."""
    out_ = {}
    lists = [p for p in SYSTEM_LISTS if p.is_file()] + sorted(ADDON_LISTS.glob("es_systems*.cfg"))
    for f in lists:
        try:
            tree = ET.fromstring(f.read_text(errors="replace"))
        except (ET.ParseError, OSError):
            continue
        for s in tree.iter("system"):
            name = (s.findtext("name") or "").strip()
            path = (s.findtext("path") or "").strip()
            if not name or name not in MANUAL_CONTENT_CONSOLES or not path:
                continue
            p = ROOT / path.lstrip("/")
            if p.is_dir():
                out_[name] = {"name": (s.findtext("fullname") or name).strip(), "path": p}
    return out_


def console_files(folder):
    files = []
    for d, subs, names in os.walk(folder):
        subs[:] = sorted(x for x in subs if x.lower() not in SKIP and not x.startswith(".") and not (Path(d) / x).is_symlink())
        if len(Path(d).relative_to(folder).parts) >= 4:
            subs[:] = []
        for n in sorted(names):
            if n.startswith(".") or n.lower().endswith(NOT_FILES) or n == "gamelist.xml":
                continue
            p = Path(d) / n
            if p.is_file() and not p.is_symlink():
                files.append(p)
                if len(files) >= MAX_FILES:
                    return files
    return files


def guess(p):
    if UPDATE_TAG.search(p.name) or any(x.lower() in ("update", "updates", "patch", "patches") for x in p.parts[-4:-1]):
        return "update"
    if DLC_TAG.search(p.name) or any(x.lower() in ("dlc", "addcont", "addons", "add-ons") for x in p.parts[-4:-1]):
        return "dlc"
    return "game"


def marks(): return read(MARKS, {})


def save_marks(m):
    MARKS.parent.mkdir(parents=True, exist_ok=True)
    tmp = MARKS.with_name("." + MARKS.name + ".tmp")
    tmp.write_text(json.dumps(m, indent=1) + "\n"); os.replace(tmp, MARKS)


def console_of(console):
    found = consoles_unmanaged().get(console)
    if not found:
        raise ValueError("MANUAL CONTENT covers Windows games only")
    return found


def file_of(console, t):
    c = console_of(console)
    for p in console_files(c["path"]):
        if ftok(p) == t:
            return c, p
    raise ValueError("That file is no longer there; go back and reopen the console")


def count_text(n): return ("%d+ FILES" % n) if n >= MAX_FILES else ("%d FILES" % n)


def letter(name):
    c = name[:1].upper()
    return c if "A" <= c <= "Z" else "#"


def by_letter(items, name=lambda x: x.name):
    groups = {}
    for x in items:
        groups.setdefault(letter(name(x)), []).append(x)
    return sorted(groups.items(), key=lambda kv: (kv[0] == "#", kv[0]))


def kind_of(m, p):
    entry = m.get(ftok(p), {})
    return entry.get("kind") or guess(p), bool(entry.get("kind"))


def copies_of(p):
    return {t: e for t, e in read(MANIFEST, {}).items() if e.get("source") == rel(p)}


def mc():
    found = consoles_unmanaged()
    rows = []
    for name, c in sorted(found.items()):
        n = len(console_files(c["path"]))
        if n:
            rows.append((name, c, n))
    out("GROUP", "CONSOLES")
    if not rows:
        out("ROW", "NONE WITH FILES", "")
    for name, c, n in rows:
        out("PAGE", c["name"].upper(), count_text(n), c["name"].upper(), "mc-console --console " + name)
    out("GROUP", "HOW IT WORKS")
    out("ROW", "1", "MARK A FILE AS A GAME, AN UPDATE OR DLC (GCM GUESSES FROM ITS NAME)")
    out("ROW", "2", "PAIR AN UPDATE OR DLC WITH ITS GAME")
    out("ROW", "3", "COPY IT AS-IS INTO THE GAME'S FOLDER (OR ANOTHER FOLDER YOU PICK)")
    out("ROW", "", "GCM CANNOT CHECK THESE FILES OR CONFIRM THE EMULATOR USES THEM")


def file_row(console, m, p, k, marked):
    parts = ["MARKED" if marked else "GUESSED"]
    if k != "game":
        g = m.get(ftok(p), {}).get("game_name")
        parts.append("FOR " + g.upper() if g else "NO GAME")
        n = len(copies_of(p))
        if n:
            parts.append("%d COPIED" % n)
    out("PAGE", p.name, " · ".join(parts), p.name, "mc-file --console %s --token %s" % (console, ftok(p)))


def mc_console(console, kind=None, only=None):
    """A console's files by type; a type with more than MAX_ROWS files is split by first letter."""
    c = console_of(console)
    m = marks()
    files = console_files(c["path"])
    groups = {"game": [], "update": [], "dlc": []}
    for p in files:
        k, marked = kind_of(m, p)
        groups[k].append((p, marked))
    if kind in groups:
        sel = [x for x in groups[kind] if only is None or letter(x[0].name) == only]
        out("GROUP", "%s%s (%d)" % (KIND_NAME[kind].replace("GAME", "GAMES").replace("UPDATE", "UPDATES"), " · " + only if only else "", len(sel)))
        for p, marked in sel[:MAX_ROWS * 5]:
            file_row(console, m, p, kind, marked)
        return
    if len(files) >= MAX_FILES:
        out("ROW", "ONLY THE FIRST %d FILES ARE LISTED" % MAX_FILES, "")
    for k, title in (("update", "UPDATES"), ("dlc", "DLC"), ("game", "GAMES")):
        out("GROUP", "%s (%d)" % (title, len(groups[k])))
        if len(groups[k]) > MAX_ROWS:
            for lt, items in by_letter(groups[k], name=lambda x: x[0].name):
                out("PAGE", lt, "%d FILES" % len(items), "%s · %s" % (title, lt), "mc-console --console %s --kind %s --letter %s" % (console, k, lt))
            continue
        for p, marked in groups[k]:
            file_row(console, m, p, k, marked)


def mc_file(console, t):
    c, p = file_of(console, t)
    m = marks()
    k, marked = kind_of(m, p)
    entry = m.get(t, {})
    out("ROW", "FILE", p.name)
    out("ROW", "FOLDER", rel(p.parent))
    out("ROW", "SIZE", "%.1f MB" % (p.stat().st_size / 1e6))
    out("ROW", "TYPE", KIND_NAME[k] + (" (MARKED BY YOU)" if marked else " (GCM'S GUESS FROM ITS NAME)"))
    out("GROUP", "MARK AS")
    for kind in ("game", "update", "dlc"):
        if kind != k or not marked:
            out("ACTION", "MARK AS " + KIND_NAME[kind], "", "MARK %s AS %s?" % (p.name, KIND_NAME[kind]),
                "mc-mark --console %s --token %s --kind %s" % (console, t, kind))
    if marked:
        out("ACTION", "CLEAR MY MARK", "", "USE GCM'S GUESS FOR %s AGAIN?" % p.name, "mc-mark --console %s --token %s --kind clear" % (console, t))
    if k == "game":
        return
    out("GROUP", "GAME")
    out("PAGE", "FOR GAME", (entry.get("game_name") or "NONE CHOSEN").upper(), "CHOOSE THE GAME", "mc-games --console %s --token %s" % (console, t))
    out("GROUP", "COPY")
    out("PAGE", "COPY TO A FOLDER", "", "CHOOSE A FOLDER", "mc-browse --console %s --token %s --folder start" % (console, t))
    for mt, e in sorted(copies_of(p).items(), key=lambda kv: kv[1]["target"]):
        out("ACTION", "COPIED TO", e["target"], "REMOVE THIS COPY?\n\n%s\n\nTHE ORIGINAL FILE IS NOT TOUCHED." % e["target"], "manual-remove --token " + mt)


def mc_games(console, t, only=None):
    c, p = file_of(console, t)
    m = marks()
    games = [q for q in console_files(c["path"]) if kind_of(m, q)[0] == "game"]
    if only:
        games = [q for q in games if letter(q.name) == only]
    out("GROUP", "GAMES ON THIS CONSOLE%s (%d)" % (" · " + only if only else "", len(games)))
    if not games:
        out("ROW", "NO GAMES", "MARK A FILE AS A GAME FIRST")
    if len(games) > MAX_ROWS and not only:
        for lt, items in by_letter(games):
            out("PAGE", lt, "%d GAMES" % len(items), "CHOOSE THE GAME · " + lt, "mc-games --console %s --token %s --letter %s" % (console, t, lt))
        games = []
    for g in games[:MAX_ROWS * 5]:
        out("ACTION", g.stem, rel(g.parent), "%s IS FOR\n%s?" % (p.name, g.stem), "mc-pair --console %s --token %s --game %s" % (console, t, ftok(g)))
    if m.get(t, {}).get("game"):
        out("GROUP", "")
        out("ACTION", "NO GAME", "", "REMOVE THE GAME CHOSEN FOR %s?" % p.name, "mc-pair --console %s --token %s --game none" % (console, t))


def mc_browse(console, t, folder):
    c, p = file_of(console, t)
    if folder == "start":
        m = marks()
        game = m.get(t, {}).get("game")
        starts = []
        if game:
            for q in console_files(c["path"]):
                if ftok(q) == game:
                    starts.append(("THE GAME'S FOLDER", q.parent))
        starts += [("THIS CONSOLE'S ROM FOLDER", c["path"]), ("THIS CONSOLE'S SAVES FOLDER", U / "saves" / console),
                   ("GCM'S MANUAL FOLDER", CONTENT / console / "manual"), ("ALL ROM FOLDERS", U / "roms"),
                   ("EMULATOR SETTINGS FOLDERS", U / "system/configs"), ("BIOS FOLDER", U / "bios")]
        out("GROUP", "START FROM")
        shown = set()
        for label, f in starts:
            if f in shown:
                continue
            shown.add(f)
            if label == "GCM'S MANUAL FOLDER" and not f.exists():
                out("ACTION", label, rel(f), "CREATE %s AND COPY %s THERE?\n\nGCM CANNOT CHECK THIS FILE OR CONFIRM THE EMULATOR USES IT." % (rel(f), p.name),
                    "mc-copy --console %s --token %s --folder %s" % (console, t, dtok(f)))
            elif allowed(f):
                out("PAGE", label, rel(f), "CHOOSE A FOLDER", "mc-browse --console %s --token %s --folder %s" % (console, t, dtok(f)))
        return
    f = from_dtok(folder)
    if not allowed(f):
        out("ERROR", "GCM cannot copy into that folder"); return
    out("GROUP", rel(f))
    out("ACTION", "COPY INTO THIS FOLDER", "",
        "COPY %s AS-IS TO\n%s ?\n\nGCM CANNOT CHECK THIS FILE OR CONFIRM THE EMULATOR USES IT.%s" % (
            p.name, rel(f), "\n\nFILES IN A ROM FOLDER MAY APPEAR IN THE GAME LIST." if (U / "roms") in [f, *f.parents] else ""),
        "mc-copy --console %s --token %s --folder %s" % (console, t, folder))
    parent = f.parent
    if allowed(parent):
        out("PAGE", ".. (UP ONE FOLDER)", rel(parent), "CHOOSE A FOLDER", "mc-browse --console %s --token %s --folder %s" % (console, t, dtok(parent)))
    subs = []
    try:
        subs = sorted(e for e in f.iterdir() if e.is_dir() and not e.name.startswith(".") and not e.is_symlink())
    except OSError:
        pass
    out("GROUP", "FOLDERS (%d)" % len(subs))
    for s in subs[:MAX_ROWS]:
        out("PAGE", s.name, "", "CHOOSE A FOLDER", "mc-browse --console %s --token %s --folder %s" % (console, t, dtok(s)))


def mc_mark(console, t, kind):
    c, p = file_of(console, t)
    m = marks()
    entry = m.get(t, {})
    if kind == "clear":
        entry.pop("kind", None)
    elif kind in KIND_NAME:
        entry.update(kind=kind, console=console, file=rel(p))
    else:
        out("ERROR", "Unknown type"); return 1
    if kind == "game" or (kind == "clear" and guess(p) == "game"):
        entry.pop("game", None); entry.pop("game_name", None)
    if entry:
        m[t] = entry
    else:
        m.pop(t, None)
    save_marks(m)
    out("OK", "%s is now %s." % (p.name, "GCM's guess again" if kind == "clear" else "marked as " + KIND_NAME[kind].lower()))
    return 0


def mc_pair(console, t, game):
    c, p = file_of(console, t)
    m = marks()
    entry = m.setdefault(t, {"console": console, "file": rel(p)})
    if game == "none":
        entry.pop("game", None); entry.pop("game_name", None)
        save_marks(m); out("OK", "No game is chosen for %s now." % p.name); return 0
    for g in console_files(c["path"]):
        if ftok(g) == game:
            entry.update(game=game, game_name=g.stem, game_file=rel(g))
            save_marks(m)
            out("OK", "%s is paired with %s." % (p.name, g.stem)); return 0
    out("ERROR", "That game is no longer there"); return 1


def mc_copy(console, t, folder):
    c, p = file_of(console, t)
    f = from_dtok(folder)
    if f == CONTENT / console / "manual" and not f.exists():
        f.mkdir(parents=True, exist_ok=True)
    if not allowed(f):
        out("ERROR", "GCM cannot copy into that folder"); return 1
    dest = f / p.name
    if dest.exists():
        out("ERROR", "A file with that name is already there: " + rel(dest)); return 1
    if shutil.disk_usage(f).free < p.stat().st_size + (64 << 20):
        out("ERROR", "Not enough free space for the copy"); return 1
    tmp = f / ("." + p.name + ".gcm-part")
    try:
        shutil.copyfile(p, tmp)
        with open(tmp, "rb+") as fh:
            os.fsync(fh.fileno())
        digest = sha_file(tmp)
        if digest != sha_file(p):
            raise OSError("the copy does not match the original")
        os.replace(tmp, dest)
    except OSError as exc:
        out("ERROR", "Copy failed: %s" % exc); return 1
    finally:
        if tmp.exists():
            tmp.unlink()
    m = marks()
    k, _ = kind_of(m, p)
    manifest = read(MANIFEST, {})
    manifest[token(rel(dest))] = {"source": rel(p), "target": rel(dest), "console": console, "kind": k,
                                  "game": m.get(t, {}).get("game_name"), "size": dest.stat().st_size, "sha256": digest,
                                  "installed": time.strftime("%Y-%m-%d %H:%M:%S"), "manual_content": True}
    save_manifest(manifest)
    out("OK", "Copied as-is to %s. GCM could not check it; if the game misbehaves, remove the copy from this file's page." % rel(dest))
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", nargs="?", default="summary",
                   choices=("summary", "details", "manual", "manual-install", "manual-remove", "rescan",
                            "mc", "mc-console", "mc-file", "mc-games", "mc-browse", "mc-mark", "mc-pair", "mc-copy"))
    p.add_argument("--system"); p.add_argument("--emulator"); p.add_argument("--token")
    p.add_argument("--console"); p.add_argument("--kind"); p.add_argument("--game"); p.add_argument("--folder")
    p.add_argument("--letter")
    a = p.parse_args()
    if a.command == "summary": summary()
    elif a.command == "details": details(a.system, a.emulator)
    elif a.command == "manual": manual()
    elif a.command == "manual-install": return manual_install(a.token)
    elif a.command == "manual-remove": return manual_remove(a.token)
    elif a.command == "rescan": return rescan()
    elif a.command == "mc": mc()
    elif a.command == "mc-console": mc_console(a.console, a.kind, a.letter)
    elif a.command == "mc-file": mc_file(a.console, a.token)
    elif a.command == "mc-games": mc_games(a.console, a.token, a.letter)
    elif a.command == "mc-browse": mc_browse(a.console, a.token, a.folder)
    elif a.command == "mc-mark": return mc_mark(a.console, a.token, a.kind)
    elif a.command == "mc-pair": return mc_pair(a.console, a.token, a.game)
    elif a.command == "mc-copy": return mc_copy(a.console, a.token, a.folder)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        out("ERROR", exc); sys.exit(1)
