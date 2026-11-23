#!/usr/bin/env python3
"""GCM emulator scanner: finds the emulators on this Batocera and decides, for each one, whether GCM can
manage its updates and DLC.

  built-in     GCM already supports it by name
  compatible   a new emulator that uses the same settings and storage as a backend GCM has (e.g. Eden
               linked to Citron's folder): GCM handles it with that backend
  compatible-own-storage
               it stores updates and DLC like a supported family but in its own folders; GCM's backend
               would change the other emulator's files, so it is not enabled
  excluded     an emulator GCM deliberately leaves alone
  default      no family matched: GCM leaves it alone (unsupported)

Writes only GCM's emulator registry and a report; games, emulators and their settings are never changed.
  python3 gcm-emulator-scan.py           scan, write the registry and report, print a summary
  python3 gcm-emulator-scan.py --dry-run scan and print; write nothing
A file emulator-registry-overrides.json next to the registry can force an emulator "off" (default) or to a
family name, e.g. {"eden": "off"}.
"""
import json, os, re, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(os.environ.get("GCM_SCAN_ROOT", "/"))          # tests use a scratch root
U = ROOT / "userdata"
GCM = U / "system/add-ons/game-content-manager"
REGISTRY = GCM / "emulator-registry.json"
OVERRIDES = GCM / "emulator-registry-overrides.json"
REPORT = U / "system/logs/gcm-emulator-scan.txt"
SYSTEM_LISTS = [ROOT / "usr/share/emulationstation/es_systems.cfg"]
ADDON_LISTS = U / "system/configs/emulationstation"

# Consoles GCM manages, and the backend family for every emulator GCM supports by name.
GCM_SYSTEMS = ("switch", "ps4", "ps3", "wiiu", "psvita", "xbox360", "3ds", "wii", "psp", "switch2")
BUILTIN = {
    "citron": "yuzu", "citron-emu": "yuzu",
    "ryujinx": "ryujinx", "ryujinx-emu": "ryujinx",
    "shadps4": "shadps4", "rpcs3": "rpcs3", "cemu": "cemu", "vita3k": "vita3k",
    "xenia": "xenia", "xenia-canary": "xenia", "xenia-edge": "xenia", "xenia_canary": "xenia", "xenia_edge": "xenia",
    "azahar": "citra", "azahar-libretro": "citra", "libretro-azahar": "citra", "citra": "citra", "citra-emu": "citra",
    "dolphin": "dolphin", "dolphin-emu": "dolphin", "ppsspp": "ppsspp",
    "lichii": "lichii", "lichii-366": "lichii",
}
EXCLUDED = (("nextendo", "GCM's menu skips this emulator"), ("xemu", "GCM's menu skips this emulator"))
# Where each backend reads and writes: a new emulator is only enabled if its files are these files.
BACKEND_PATHS = {
    "yuzu": ["system/configs/yuzu/qt-config.ini"],
    "ryujinx": ["system/configs/Ryujinx"],
    "shadps4": ["system/configs/shadps4/user/config.toml"],
    "rpcs3": ["system/configs/rpcs3/vfs.yml", "saves/ps3/rpcs3"],
    "cemu": ["system/configs/cemu/settings.xml"],
    "vita3k": ["system/configs/vita3k/config.yml"],
    "citra": ["saves/3ds/azahar-emu", "saves/3ds/citra-emu", "saves/3ds/Azahar"],
    "xenia": ["system/configs/xenia", "system/configs/xenia-canary", "system/configs/xenia-edge"],
    "dolphin": [], "ppsspp": [],
    "lichii": ["system/add-ons/switch2/eglantine/master/isolated/config/lichii/qt-config.ini"],
}
# The emulator name GCM's backend is called with when it handles a compatible emulator.
ROUTE_AS = {"yuzu": "citron-emu", "ryujinx": "ryujinx-emu", "shadps4": "shadps4", "rpcs3": "rpcs3", "cemu": "cemu",
            "vita3k": "vita3k", "xenia": "xenia", "citra": "azahar", "dolphin": "dolphin", "ppsspp": "ppsspp", "lichii": "lichii"}
BACKEND = {"yuzu": "citron", "ryujinx": "ryujinx", "shadps4": "shadps4", "rpcs3": "rpcs3", "cemu": "cemu",
           "vita3k": "vita3k", "xenia": "xenia", "citra": "azahar", "dolphin": "dolphin", "ppsspp": "ppsspp", "lichii": "switch2"}


def read(p, limit=1 << 20):
    try:
        with open(p, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def ini_has(text, *keys):
    """Every key is set (non-empty) somewhere in an INI-style file, in any section and either key spelling."""
    def present(key):
        k = re.escape(key).replace(r"\ ", r"(?:\ |%20)")
        return re.search(r"^\s*(?:[^=\n]*[\\/])?" + k + r"\s*=\s*\S", text, re.M | re.I) is not None
    return all(present(k) for k in keys)


def find(d, name, want_dir=False, max_depth=8, max_entries=40000):
    """Paths named `name` (fnmatch pattern; may contain one '/' for a parent/child pair) under d,
    searching at most max_depth levels and max_entries entries, without following links into other folders."""
    import fnmatch
    parent, _, leaf = name.rpartition("/")
    hits, seen, base = [], 0, str(d)
    for top, dirs, files in os.walk(base, followlinks=False):
        depth = top[len(base):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
        seen += len(dirs) + len(files)
        for n in (dirs if want_dir else files):
            if fnmatch.fnmatch(n, leaf) and (not parent or os.path.basename(top) == parent):
                hits.append(os.path.join(top, n))
                if len(hits) >= 5:
                    return hits
        if seen > max_entries:
            break
    return hits


# Each family: name hints (a hint alone is never enough), and file checks that prove the layout on disk.
def yuzu_check(d):
    for ini in find(d, "qt-config.ini"):
        t = read(ini)
        if ini_has(t, "nand_directory") and re.search(r"external_content_dirs", t, re.I):
            return ini, "qt-config.ini with a NAND folder and external content folders (yuzu layout)"
    return None


def ryujinx_check(d):
    for cfg in find(d, "Config.json"):
        try:
            data = json.loads(read(cfg))
        except ValueError:
            continue
        base = Path(cfg).parent
        if isinstance(data, dict) and "game_dirs" in data and ((base / "bis").is_dir() or (base / "games").is_dir()):
            return cfg, "Config.json with game folders, and Ryujinx's bis/games storage"
    return None


def citra_check(d):
    for ini in find(d, "qt-config.ini"):
        t = read(ini)
        if ini_has(t, "sdmc_directory") or re.search(r"^\s*(?:[^=\n]*[\\/])?use_custom_storage", t, re.M | re.I):
            return ini, "qt-config.ini with an SDMC folder (Citra layout)"
    for sd in find(d, "sdmc/Nintendo 3DS", want_dir=True)[:1]:
        return sd, "sdmc/Nintendo 3DS storage (Citra layout)"
    return None


def shadps4_check(d):
    for cfg in find(d, "config.toml"):
        t = read(cfg)
        if re.search(r"^\s*installDirs\s*=", t, re.M) and re.search(r"^\s*addonInstallDir\s*=", t, re.M):
            return cfg, "config.toml with installDirs and addonInstallDir (shadPS4 layout)"
    return None


def file_with(pattern, needle, why):
    def check(d):
        for f in find(d, pattern):
            if needle is None or re.search(needle, read(f), re.I | re.M):
                return f, why
        return None
    return check


FAMILIES = {
    "yuzu": (("switch",), ("yuzu", "citron", "eden", "sudachi", "suyu", "torzu", "eglantine", "lichii", "hortensia"), yuzu_check),
    "ryujinx": (("switch",), ("ryujinx", "ryubing", "kenjinx"), ryujinx_check),
    "citra": (("3ds",), ("citra", "azahar", "lime3ds", "lime"), citra_check),
    "shadps4": (("ps4",), ("shadps4",), shadps4_check),
    "rpcs3": (("ps3",), ("rpcs3",), file_with("vfs.yml", r"/dev_hdd0/", "vfs.yml mapping /dev_hdd0/ (RPCS3 layout)")),
    "cemu": (("wiiu",), ("cemu",), file_with("settings.xml", r"<mlc_path", "settings.xml with an MLC path (Cemu layout)")),
    "vita3k": (("psvita",), ("vita3k", "vita"), file_with("config.yml", r"pref-path", "config.yml with pref-path (Vita3K layout)")),
    "xenia": (("xbox360",), ("xenia",), file_with("xenia*.config.toml", r"content_root|\[Storage\]", "Xenia config with content storage (Xenia layout)")),
    "dolphin": (("wii",), ("dolphin",), file_with("Dolphin.ini", r"^\s*NAND", "Dolphin.ini with a NAND root (Dolphin layout)")),
    "ppsspp": (("psp",), ("ppsspp",), file_with("ppsspp.ini", None, "ppsspp.ini (PPSSPP layout)")),
    # Switch 2 (NUP games): GCM's Switch 2 backend works on Lichii's isolated settings
    "lichii": (("switch2",), ("lichii", "eglantine", "hortensia"), yuzu_check),
}


def inventory():
    """{(system, emulator): {"cores", "source", "addon", "command"}} from Batocera's and the add-ons' system
    lists. A console with only a launch command and no named emulators is listed with emulator ""."""
    found = {}
    stock = [p for p in SYSTEM_LISTS if p.is_file()]
    addon = sorted(ADDON_LISTS.glob("es_systems*.cfg"))
    for f in stock + addon:
        try:
            root = ET.fromstring(read(f, 8 << 20))
        except ET.ParseError:
            continue
        for system in root.iter("system"):
            name = (system.findtext("name") or "").strip()
            if not name:
                continue
            command = (system.findtext("command") or "").strip()
            emus = [(e.get("name") or "").strip() for e in system.iter("emulator")]
            pairs = [(e, [(c.text or "").strip() for c in em.iter("core") if (c.text or "").strip()])
                     for e, em in zip(emus, system.iter("emulator")) if e] or [("", [])]
            for ename, cores in pairs:
                entry = found.setdefault((name, ename), {"cores": [], "source": str(f.relative_to(ROOT)),
                                                         "addon": f in addon, "command": command})
                entry["cores"] = sorted(set(entry["cores"]) | set(cores))
                entry["addon"] = entry["addon"] or f in addon
                entry["command"] = entry["command"] or command
    return found


def command_dirs(command):
    """The add-on folders a console's launch command uses (never /userdata/system itself or the ROMs)."""
    dirs = []
    for path in re.findall(r"(/userdata/[^\s\"'%;|&]+)", command):
        p = ROOT / path.lstrip("/")
        d = p if p.is_dir() else p.parent
        rel = d.relative_to(ROOT).parts if d.is_relative_to(ROOT) else ()
        if len(rel) >= 4 and rel[2] not in ("roms",) and d.is_dir() and d not in dirs:
            dirs.append(d)
            if len(rel) >= 5 and rel[2] == "add-ons" and d.parent not in dirs:
                dirs.append(d.parent)
    return dirs


def candidate_dirs(system, names):
    """Settings and storage folders that may belong to an emulator with one of these names."""
    dirs, roots = [], [U / "system/configs", U / "system/.config", U / "system/.local/share", U / "saves" / system]
    for r in roots:
        if not r.is_dir():
            continue
        for d in r.iterdir():
            low = d.name.lower()
            if d.is_dir() and any(len(n) >= 3 and (n in low or low in n) and len(low) >= 3 for n in names):
                dirs.append(d)
    return dirs


def shares_backend(family, proof):
    """True if the proof file is, or lies inside, what the family's backend works on (links followed)."""
    real = os.path.realpath(proof)
    for rel in BACKEND_PATHS[family]:
        target = os.path.realpath(str(U / rel))
        if os.path.exists(target) and (real == target or real.startswith(target.rstrip("/") + "/")):
            return True
    return False


def classify(system, emulator, cores, overrides, extra_dirs=(), managed=True):
    names = sorted({emulator.lower(), *(c.lower() for c in cores)} - {"libretro", ""})
    result = {"system": system, "emulator": emulator, "cores": cores, "status": "default",
              "family": None, "backend": None, "route_as": None, "evidence": [], "config": None}
    forced = overrides.get(emulator)
    if forced == "off":
        result["evidence"].append("turned off in emulator-registry-overrides.json")
        return result
    for prefix, why in EXCLUDED:
        if emulator.lower().startswith(prefix):
            result.update(status="excluded")
            result["evidence"].append(why)
            return result
    if not managed:
        return new_console(result, system, names, extra_dirs, forced)
    for n in [emulator.lower()] + [c.lower() for c in cores]:
        fam = BUILTIN.get(n)
        if fam and system in FAMILIES[fam][0]:
            result.update(status="built-in", family=fam, backend=BACKEND[fam], route_as=n)
            result["evidence"].append("supported by name (%s)" % n)
            return result
    families = [forced] if forced in FAMILIES else [f for f, (systems, _, _) in FAMILIES.items() if system in systems]
    for fam in families:
        systems, hints, check = FAMILIES[fam]
        hinted = forced == fam or any(h in n for h in hints for n in names)
        for d in candidate_dirs(system, names):
            proof = check(d)
            if proof:
                shared = shares_backend(fam, proof[0])
                result.update(status="compatible" if shared else "compatible-own-storage", family=fam,
                              backend=BACKEND[fam], route_as=ROUTE_AS[fam] if shared else None,
                              config=str(Path(proof[0]).relative_to(ROOT)))
                result["evidence"].append(proof[1] + " in " + str(d.relative_to(ROOT)))
                result["evidence"].append(("same settings and storage as the %s backend (%s): enabled" % (BACKEND[fam], os.path.realpath(proof[0]).replace(str(ROOT), "/", 1).replace("//", "/")))
                                          if shared else ("its own settings and storage, not the %s backend's: not enabled" % BACKEND[fam]))
                result["evidence"].append(("name matches the %s family" % fam) if hinted else "files match even though the name is new")
                if forced == fam:
                    result["evidence"].append("family set in emulator-registry-overrides.json")
                return result
    result["evidence"].append("no known family's files were found for it" if families else "GCM does not manage this console")
    return result


def new_console(result, system, names, extra_dirs, forced):
    """An add-on console GCM does not manage yet: find which family its emulator's files belong to."""
    result["status"] = "new-console"
    dirs = list(extra_dirs) + [d for d in candidate_dirs(system, names) if d not in extra_dirs]
    families = [forced] if forced in FAMILIES else list(FAMILIES)
    for fam in families:
        _, hints, check = FAMILIES[fam]
        for d in dirs:
            proof = check(d)
            if proof:
                result.update(family=fam, config=str(Path(proof[0]).relative_to(ROOT)))
                result["evidence"].append("%s family files: %s in %s" % (fam, proof[1], str(d.relative_to(ROOT))))
                if any(h in n for h in hints for n in names):
                    result["evidence"].append("name matches the %s family" % fam)
                break
        if result["family"]:
            break
    if not result["family"]:
        result["evidence"].append("no known family's files were found in %s" % (", ".join(str(d.relative_to(ROOT)) for d in dirs[:3]) or "its folders"))
    result["evidence"].append("GCM does not manage the %s console yet: not enabled" % system)
    return result


def scan():
    try:
        overrides = json.loads(read(OVERRIDES)) if OVERRIDES.is_file() else {}
        if not isinstance(overrides, dict):
            overrides = {}
    except ValueError:
        overrides = {}
    rows = []
    for (s, e), info in sorted(inventory().items()):
        managed = s in GCM_SYSTEMS
        if not managed and not info["addon"]:
            continue                     # Batocera's own consoles that GCM does not manage are not listed
        row = classify(s, e, info["cores"], overrides, command_dirs(info["command"]), managed)
        row.update(source=info["source"], addon=info["addon"])
        if not e:
            row["emulator"] = "(launch command)"
        rows.append(row)
    version = read(ROOT / "usr/share/batocera/batocera.version").strip()
    return {"schema": "GCM_EMULATOR_REGISTRY_V1", "scanned": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batocera": version, "emulators": rows}


def report(reg):
    lines = ["GCM emulator scan  %s  (Batocera %s)" % (reg["scanned"], reg["batocera"] or "unknown"), ""]
    order = ("compatible", "compatible-own-storage", "new-console", "built-in", "excluded", "default")
    for status in order:
        rows = [r for r in reg["emulators"] if r["status"] == status]
        if not rows:
            continue
        title = "ADD-ON CONSOLES AND EMULATORS (not managed by GCM yet)" if status == "new-console" else status.upper()
        lines.append("%s (%d)" % (title, len(rows)))
        for r in rows:
            fam = (" -> %s backend" % r["backend"]) if r["route_as"] else ""
            lines.append("  %-8s %-22s%s" % (r["system"], r["emulator"], fam))
            for e in r["evidence"]:
                lines.append("           " + e)
        lines.append("")
    return "\n".join(lines)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def main():
    reg = scan()
    text = report(reg)
    print(text)
    if "--dry-run" in sys.argv:
        print("DRY RUN: nothing was written")
        return
    write(REGISTRY, json.dumps(reg, indent=1) + "\n")
    write(REPORT, text + "\n")
    counts = {s: sum(r["status"] == s for r in reg["emulators"]) for s in ("built-in", "compatible", "compatible-own-storage", "new-console", "excluded", "default")}
    print("PASS: %d emulators scanned (%s); registry %s" % (len(reg["emulators"]),
          ", ".join("%d %s" % (n, s) for s, n in counts.items()), REGISTRY))


if __name__ == "__main__":
    main()
