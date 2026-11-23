#!/usr/bin/env python3
"""GCM report: what the emulator scanner and the content inspector found, for the GAME CONTENT MANAGER page
in the ES main menu, and the manual "install as-is" for files GCM cannot identify.

The page is built from these lines (one per row), so the frontend needs no change when this file grows:
  GROUP\t<title>
  ROW\t<label>\t<value>
  PAGE\t<label>\t<value>\t<page title>\t<arguments>            opens a page from this tool
  ACTION\t<label>\t<value>\t<question>\t<arguments>            asks, runs this tool, shows its OK/ERROR line
Commands: summary | details --system S --emulator E | manual | manual-install --token T | manual-remove --token T | rescan
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
    rows = [r for r in reg.get("emulators", []) if r.get("status") != "excluded"]
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", nargs="?", default="summary",
                   choices=("summary", "details", "manual", "manual-install", "manual-remove", "rescan"))
    p.add_argument("--system"); p.add_argument("--emulator"); p.add_argument("--token")
    a = p.parse_args()
    if a.command == "summary": summary()
    elif a.command == "details": details(a.system, a.emulator)
    elif a.command == "manual": manual()
    elif a.command == "manual-install": return manual_install(a.token)
    elif a.command == "manual-remove": return manual_remove(a.token)
    elif a.command == "rescan": return rescan()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        out("ERROR", exc); sys.exit(1)
