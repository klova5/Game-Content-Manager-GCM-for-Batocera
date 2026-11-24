#!/usr/bin/env python3
"""GCM 4.1 MANUAL INSTALL for PS4 (shadPS4): install an update or DLC GCM did not detect for a game.

The content inspector finds PS4 packages (.pkg) and unpacked folders on every drive. For the game the user
opened, this lists the ones of the chosen kind that are not this game's installed content, including
another region's (e.g. a US DLC for an EU game), and installs the chosen one the way shadPS4 reads it:
  update  /userdata/roms/ps4/<GAME ID>-UPDATE/
  DLC     /userdata/roms/ps4/DLC/<GAME ID>/<entitlement label>/
A package is unpacked with GCM's PS4 extractor; a folder is copied. Every file is checked against its
source, nothing existing is overwritten, and the install is recorded so REMOVE can take it out again.
GCM cannot confirm the game uses it.

  list    --rom R --type update|dlc     ROW MANUAL / ROW INSTALLED lines for the GCM page
  install --rom R --type T --token X    ROW tokens come from list
  remove  --rom R --type T --token X
"""
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path

PREFIX = Path(os.environ.get("GCM_PS4_ROOT", "/"))
def P(p): return PREFIX / str(p).lstrip("/")

COMMON = P("/userdata/system/gcm-native-test/step61-common-content")
PINS = {"gcm-ps4-base-identity.py": "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
        "gcm-ps4-extraction-rehearsal.py": "bc3666be4ff49e0589f4778434c105c109b4f52384b18dd7b099bd83baec3e92"}
ROMS = P("/userdata/roms/ps4")
INSPECTION = P("/userdata/system/add-ons/game-content-manager/content-inspection.json")
RECORDS = P("/userdata/system/game-content/ps4-manual-installs.json")
CACHE = P("/userdata/system/gcm-native-test/state/ps4-manual")
LABEL = re.compile(r"[A-Z0-9]{16}")
_MODS = {}


def clean(v): return " ".join(str(v).replace("\t", " ").split())[:600]
def out(*f): print("\t".join(clean(x) for x in f))


def load(name):
    if name not in _MODS:
        path = COMMON / name
        if os.environ.get("GCM_PS4_TEST_MODULES"):
            path = Path(os.environ["GCM_PS4_TEST_MODULES"]) / name
        elif path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
            raise ValueError("PS4 manual install dependency changed: " + name)
        spec = importlib.util.spec_from_file_location(name.replace("-", "_").replace(".", "_"), path)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        _MODS[name] = m
    return _MODS[name]


def read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n"); os.replace(tmp, path)


def game(rom):
    """The game's title ID (CUSAxxxxx), from the ES entry the same way GCM's PS4 pages resolve it."""
    if os.environ.get("GCM_PS4_TEST_GAME"):
        return os.environ["GCM_PS4_TEST_GAME"]
    return load("gcm-ps4-base-identity.py").identify(rom)["application_id"]


def target_for(app, kind, row):
    if kind == "update":
        return ROMS / (app + "-UPDATE")
    label = None
    cid = row.get("content_id") or ""
    if len(cid) >= 16 and LABEL.fullmatch(cid[-16:]):
        label = cid[-16:]
    elif LABEL.fullmatch(Path(row["file"]).name):
        label = Path(row["file"]).name
    return ROMS / "DLC" / app / label if label else None


def inside(path, folder):
    try:
        Path(path).relative_to(folder)
        return True
    except ValueError:
        return False


def candidates(rom, kind):
    app = game(rom)
    own = [ROMS / app, ROMS / (app + "-UPDATE"), ROMS / "DLC" / app]
    installed = {r["source"] for r in read(RECORDS, {}).values() if r.get("game") == app}
    rows = read(INSPECTION, {}).get("content", [])
    found = {}
    for r in rows:
        if r.get("console") != "ps4" or r.get("kind") != kind or r.get("format") not in ("ps4-package", "ps4-folder"):
            continue
        src = P(r["file"])
        if not src.exists() or any(inside(src, o) for o in own) or r["file"] in installed:
            continue
        target = target_for(app, kind, r)
        tok = hashlib.sha256(("%s|%s|%s" % (app, kind, r["file"])).encode()).hexdigest()[:32]
        found[tok] = {"token": tok, "source": r["file"], "folder": src.is_dir(), "game_id": r.get("game_id") or "",
                      "content_id": r.get("content_id") or "", "target": "/" + str(target.relative_to(PREFIX)) if target else None,
                      "matches": (r.get("game_id") or "") == app, "title": r.get("title") or ""}
        if len(found) >= 100:
            break
    CACHE.mkdir(parents=True, exist_ok=True)
    write(CACHE / ("%s-%s.json" % (app, kind)), {"game": app, "candidates": found})
    return app, found


def listing(rom, kind):
    app, found = candidates(rom, kind)
    for tok, r in sorted(read(RECORDS, {}).items(), key=lambda kv: kv[1]["target"]):
        if r.get("game") == app and r.get("kind") == kind:
            out("ROW", "INSTALLED", "INSTALLED BY YOU", "%s -> %s" % (Path(r["source"]).name, Path(r["target"]).name), tok,
                "REMOVE %s? THE ORIGINAL PACKAGE OR FOLDER IS NOT TOUCHED." % r["target"])
    if not found:
        out("ERROR", "No other PS4 %s was found. GCM's content inspector looks at every drive at boot; use RESCAN NOW on the "
                     "GAME CONTENT MANAGER page after adding files." % ("updates" if kind == "update" else "DLC"))
    for tok, c in sorted(found.items(), key=lambda kv: (not kv[1]["matches"], Path(kv[1]["source"]).name)):
        name = Path(c["source"]).name + ("/" if c["folder"] else "")
        label = "%s · %s" % (name, c["game_id"] or "NO ID")
        if not c["target"]:
            out("ROW", "BLOCKED", "NO DLC LABEL", label, tok, "GCM CANNOT TELL THIS DLC'S LABEL, SO IT CANNOT PLACE IT."); continue
        if P(c["target"]).exists():
            out("ROW", "BLOCKED", "ALREADY THERE", label, tok, "%s ALREADY EXISTS; GCM DOES NOT OVERWRITE IT." % c["target"]); continue
        value = "THIS GAME'S ID" if c["matches"] else "ANOTHER ID"
        warn = ("IT WILL BE INSTALLED TO %s." % c["target"]) + ("" if c["matches"] else
                " ITS ID %s IS NOT THIS GAME'S (%s): IT MAY WORK IF IT IS THE SAME CONTENT FOR ANOTHER REGION, "
                "OR THE GAME MAY IGNORE IT." % (c["game_id"] or "(NONE)", app))
        out("ROW", "MANUAL", value, label, tok, warn)


def tree(folder):
    """{relative path: [size, sha256]} for every file under folder."""
    result = {}
    for d, subs, files in os.walk(folder):
        subs.sort()
        for n in sorted(files):
            p = Path(d) / n
            if p.is_symlink():
                raise ValueError("Links inside the content are not supported: " + n)
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(8 << 20), b""):
                    h.update(b)
            result[str(p.relative_to(folder))] = [p.stat().st_size, h.hexdigest()]
    return result


def digest(t): return hashlib.sha256(json.dumps(t, sort_keys=True).encode()).hexdigest()


def require_stopped():
    if subprocess.run(["pgrep", "-f", "shadps4"], capture_output=True).returncode == 0:
        raise ValueError("Close the game first: shadPS4 is running")


def install(rom, kind, tok):
    app = game(rom)
    found = read(CACHE / ("%s-%s.json" % (app, kind)), {}).get("candidates", {})
    c = found.get(tok)
    if not c or not c.get("target"):
        raise ValueError("That item is no longer in the list; open MANUAL INSTALL again")
    src, target = P(c["source"]), P(c["target"])
    if not src.exists():
        raise ValueError("The source is no longer there: " + c["source"])
    if target.exists():
        raise ValueError(c["target"] + " already exists; GCM does not overwrite it")
    require_stopped()
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name("." + target.name + ".gcm-part")
    work = None
    try:
        if part.exists():
            shutil.rmtree(part)
        if c["folder"]:
            need = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
            if shutil.disk_usage(target.parent).free < need + (256 << 20):
                raise ValueError("Not enough free space for the copy")
            shutil.copytree(src, part, symlinks=False)
            expected = tree(src)
        else:
            work = COMMON / ("step61-ps4-extraction-" + uuid.uuid4().hex)
            work.mkdir()
            # GCM's extractor seals its own process (private read-only mounts), so it runs in a child process.
            r = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "_extract", "--rom", rom, "--type", kind,
                                "--token", str(src) + "\n" + str(work)], capture_output=True, text=True)
            if r.returncode:
                raise ValueError("Unpacking failed: " + (r.stdout + r.stderr).strip().splitlines()[-1][:300] if (r.stdout + r.stderr).strip() else "Unpacking failed")
            unpacked = work / "output"
            expected = tree(unpacked)
            need = sum(v[0] for v in expected.values())
            if shutil.disk_usage(target.parent).free < need + (256 << 20):
                raise ValueError("Not enough free space for the unpacked content")
            shutil.copytree(unpacked, part, symlinks=False)
        got = tree(part)
        if got != expected:
            raise ValueError("The installed files do not match the source")
        os.replace(part, target)
    finally:
        if part.exists():
            shutil.rmtree(part, ignore_errors=True)
        if work is not None and work.exists():
            shutil.rmtree(work, ignore_errors=True)
    records = read(RECORDS, {})
    records[tok] = {"game": app, "kind": kind, "source": c["source"], "target": c["target"], "tree": digest(got),
                    "files": len(got), "installed": time.strftime("%Y-%m-%d %H:%M:%S")}
    write(RECORDS, records)
    out("OK", "Installed to %s (%d files, checked). GCM cannot confirm the game uses it; REMOVE takes it out again." % (c["target"], len(got)))


def remove(rom, kind, tok):
    records = read(RECORDS, {})
    r = records.get(tok)
    if not r:
        raise ValueError("That install is not in GCM's list")
    target = P(r["target"])
    require_stopped()
    if target.exists():
        if digest(tree(target)) != r["tree"]:
            raise ValueError("The installed files changed since GCM installed them, so they were left in place: " + r["target"])
        shutil.rmtree(target)
    del records[tok]
    write(RECORDS, records)
    out("OK", "Removed %s. The original was not touched." % r["target"])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=("list", "install", "remove", "_extract"))
    p.add_argument("--rom", required=True); p.add_argument("--type", choices=("update", "dlc"), required=True)
    p.add_argument("--token", default="")
    a = p.parse_args()
    if a.command == "_extract":
        src, work = a.token.split("\n", 1)
        load("gcm-ps4-extraction-rehearsal.py").rehearse(src, work)
    elif a.command == "list":
        listing(a.rom, a.type)
    elif a.command == "install":
        install(a.rom, a.type, a.token)
    else:
        remove(a.rom, a.type, a.token)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        out("ERROR", exc); sys.exit(1)
