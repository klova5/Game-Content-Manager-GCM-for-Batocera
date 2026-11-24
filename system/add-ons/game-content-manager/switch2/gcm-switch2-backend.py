#!/usr/bin/env python3
"""GCM Switch 2 backend (Lichii): updates and DLC for games stored as NUP containers, on GCM's shared
folder-console menu (same wire protocol as the PS3 / Wii U / Vita backend).

Content lives in three places:
  the game file      /userdata/roms/switch2/<game>.nup        what Lichii plays (base + updates + DLC)
  GCM's store        /userdata/system/game-content/switch2/<game title ID>/<kind>-<title ID>-v<version>.nup
                     one package per file: everything GCM can put into or take out of a game
  sources            NUP files and loose dumps found by a scan (other drives included), copied into the
                     store by VERIFY (prepare)
A game file is only ever changed by gcm-nup.py: it writes the new file beside the old one, checks it, and
only then replaces it. Anything taken out of a game is copied to the store first, so nothing is lost.
"""
import argparse, contextlib, fcntl, glob, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, threading, time, uuid
from pathlib import Path

PREFIX = Path(os.environ.get("GCM_S2_ROOT", "/"))
def P(path): return PREFIX / str(path).lstrip("/")

HERE = Path(__file__).resolve().parent
NUP_TOOL = HERE / "gcm-nup.py"
NUP_SHA = '383bff65eb8d28b0bf145b572f5f008605d98132e40c50d88880db045fb4cdfd'
STATE = P("/userdata/system/gcm-native-test/state/switch2")
STORE = P("/userdata/system/game-content/switch2")
ROMS = P("/userdata/roms/switch2")
LAUNCHER = P("/userdata/system/add-ons/switch2/eglantine/launch-eglantine.sh")
INSPECTION = P("/userdata/system/add-ons/game-content-manager/content-inspection.json")
SCHEMA = "GCM_SWITCH2_JOB_V1"
OPS = ("prepare", "install", "apply", "uninstall", "enable", "disable", "recover", "forget", "scan-fast", "scan-deep", "scan-both")
COPY_KEPT = " (GCM copy kept)"
EMULATORS = ("lichii", "lichii-366")
LICHII_RUNNING = "eglantine/master/lichii/usr/bin/lichii"
KINDS = {0: "base", 1: "update", 2: "dlc"}


def clean(v): return "".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in str(v))[:1200]
def boot(): return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def nup():
    if "_nup" not in globals():
        if NUP_TOOL.is_symlink() or (NUP_SHA and hashlib.sha256(NUP_TOOL.read_bytes()).hexdigest() != NUP_SHA):
            raise ValueError("GCM NUP tool changed")
        spec = importlib.util.spec_from_file_location("gcm_nup", NUP_TOOL)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        globals()["_nup"] = m
    return globals()["_nup"]


def atom(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".%d.tmp" % os.getpid())
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def read(path): return json.loads(Path(path).read_text())


@contextlib.contextmanager
def lock(path, blocking=True):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError:
        raise ValueError("Another Switch 2 job is starting; try again")
    finally:
        os.close(fd)


# ---------------------------------------------------------------- games
def game_file(rom):
    """The .nup the ES entry plays: the ROM itself, the launcher's mapping for a .switch2 entry, or a
    .nup in the same folder whose name starts with the entry's name."""
    rom = Path(rom)
    if rom.suffix.lower() == ".nup":
        return rom
    try:
        text = LAUNCHER.read_text(errors="replace")
        for m in re.finditer(r'"([^"]+\.switch2)"\)\s*\n\s*GAME="\$ROMS/([^"]+)"', text):
            if m.group(1) == rom.name:
                return rom.parent / m.group(2)
    except OSError:
        pass
    found = sorted(p for p in rom.parent.glob("*.nup") if p.name.startswith(rom.stem))
    if len(found) == 1:
        return found[0]
    raise ValueError("No Switch 2 game file found for " + rom.name)


def describe(path):
    """Packages of a NUP file, cached by its size and modification time (the table can be large)."""
    st = path.stat()
    key = hashlib.sha256(("%s|%d|%d" % (path, st.st_size, st.st_mtime_ns)).encode()).hexdigest()
    cache = STATE / "describe" / (key + ".json")
    if cache.exists():
        return read(cache)
    rows = nup().Nup(path).describe()
    atom(cache, rows)
    return rows


def game(rom):
    path = game_file(rom)
    if not path.is_file():
        raise ValueError("Switch 2 game file missing: " + path.name)
    pkgs = describe(path)
    bases = [p for p in pkgs if p["kind"] == "base"]
    if bases:
        gid = bases[0]["title_id"]
    else:
        tid = int(pkgs[0]["title_id"], 16)
        gid = "%016X" % (tid & ~0xFFF)
    return {"rom": str(Path(rom).absolute()), "file": str(path), "title_id": gid,
            "title": (bases[0]["title"] if bases else None) or path.stem}


def belongs(gid, title_id):
    """Whether a package title ID belongs to game gid (base, update, or DLC by the Switch rule)."""
    g, t = int(gid, 16), int(title_id, 16)
    if t & ~0xFFF == g:
        return True
    return ((t & ~0xFFF) - 0x1000) == g


def item_id(gid, kind, title_id, version):
    return hashlib.sha256(("%s|%s|%s|%d" % (gid, kind, title_id.upper(), int(version))).encode()).hexdigest()


def store_dir(gid): return STORE / gid


def store_items(gid):
    out = {}
    folder = store_dir(gid)
    if folder.is_dir():
        for f in sorted(folder.glob("*.nup")):
            try:
                pk = describe(f)
            except Exception:
                continue
            # manual-*.nup: chosen for this game with MANUAL INSTALL, whatever its own title ID says
            if len(pk) == 1 and pk[0]["kind"] in ("update", "dlc") and (belongs(gid, pk[0]["title_id"]) or f.name.startswith("manual-")):
                p = pk[0]
                out[item_id(gid, p["kind"], p["title_id"], p["version"])] = dict(p, file=str(f))
    return out


def game_items(g):
    out = {}
    for p in describe(Path(g["file"])):
        if p["kind"] in ("update", "dlc"):
            out[item_id(g["title_id"], p["kind"], p["title_id"], p["version"])] = dict(p, file=g["file"])
    return out


def shown_version(p):
    """The version Lichii shows: the package's display version, else its number decoded as Lichii does."""
    v = int(p["version"])
    return p.get("display_version") or ("%d.%d.%d" % (v >> 16, (v >> 8) & 0xFF, v & 0xFF) if v else "v0")


def label(p):
    ver = shown_version(p)
    if p["kind"] == "update":
        return "Update " + ver
    return (p.get("title") or "DLC " + p["title_id"]) + ("" if p.get("title") else "") + " (" + p["title_id"] + ")"


def scope(rom, emulator, kind):
    if emulator not in EMULATORS:
        raise ValueError("Not a Switch 2 emulator: " + str(emulator))
    if kind not in ("update", "dlc"):
        raise ValueError("Content type required")
    g = game(rom)
    return {"rom": g["rom"], "emulator": "lichii", "kind": kind, "game": g["title_id"], "file": g["file"]}


def index_path(s): return STATE / "index" / ("%s-%s.json" % (s["game"], s["kind"]))


def inventory(s):
    g = {"file": s["file"], "title_id": s["game"]}
    inside, stored = game_items(g), store_items(s["game"])
    rows = []
    for iid, p in inside.items():
        if p["kind"] == s["kind"]:
            rows.append({"section": "AVAILABLE", "state": "ENABLED", "label": label(p) + (COPY_KEPT if iid in stored else ""),
                         "token": "folder:" + iid})
    for iid, p in stored.items():
        if p["kind"] == s["kind"] and iid not in inside:
            rows.append({"section": "AVAILABLE", "state": "PREPARED", "label": label(p), "token": "folder:" + iid})
    path = index_path(s)
    if path.exists():
        doc = read(path)
        for c in doc.get("candidates", []):
            iid = item_id(s["game"], c["kind"], c["title_id"], c["version"])
            if iid in inside or iid in stored or c["kind"] != s["kind"]:
                continue
            if not Path(c["source"]).exists():
                continue
            rows.append({"section": "DETECTED", "state": "UNVERIFIED", "label": Path(c["source"]).name + " (" + c["title_id"] + " v%d)" % c["version"],
                         "token": c["token"]})
    for r in recovery_rows(s):
        rows.append(r)
    return rows


def recovery_rows(s):
    rows = []
    part = Path(s["file"]).with_name("." + Path(s["file"]).name + ".gcm-part")
    if part.exists() and not running_job():
        rows.append({"section": "AVAILABLE", "state": "RECOVERY_REQUIRED", "label": "Interrupted change to the game file",
                     "token": "folder:" + hashlib.sha256(str(part).encode()).hexdigest()})
    return rows


# ---------------------------------------------------------------- scan
def mounted_drives():
    drives = [P("/userdata")]
    try:
        for line in open("/proc/self/mounts"):
            parts = line.split()
            if len(parts) > 2 and (parts[1].startswith("/media/") or parts[1].startswith("/mnt/")) and parts[2] not in ("tmpfs", "proc", "sysfs", "overlay", "squashfs"):
                d = P(parts[1].replace("\\040", " "))
                if d.is_dir() and not d.is_symlink() and d not in drives:
                    drives.append(d)
    except OSError:
        pass
    media = P("/media")
    if PREFIX != Path("/") and media.is_dir():
        drives += [d for d in sorted(media.iterdir()) if d.is_dir() and not d.is_symlink() and d not in drives]
    return drives


def fast_roots(drive):
    out = []
    for rel in ("roms/switch2", "roms/switch2_update", "roms/switch2_dlc", "batocera-roms/switch2", "switch2",
                "system/game-content/switch2-incoming", "Switch 2", "switch2_update"):
        p = drive / rel
        if p.is_dir() and not p.is_symlink():
            out.append(p)
    return out


def npdm_program_id(path):
    try:
        return nup().npdm_program_id(path.read_bytes()[:1 << 20])
    except OSError:
        return None


def candidates_in(top, gid, deep, cancelled, budget):
    """(source, kind, title_id, version, how) for this game's updates and DLC under top."""
    store = str(STORE)
    found = []
    for d, subs, files in os.walk(top, followlinks=False):
        budget[0] += 1
        if budget[0] > 200000 or cancelled():
            break
        here = Path(d)
        if str(here).startswith(store):
            subs[:] = []; continue
        depth = len(here.relative_to(top).parts)
        subs[:] = sorted(x for x in subs if not x.startswith(".") and x.lower() not in ("romfs", "exefs", "images", "videos", "media")
                         and not (here / x).is_symlink())
        if depth >= (12 if deep else 4):
            subs[:] = []
        names = {n.lower(): n for n in subs}
        # loose dumps: updates/<version>/exefs/main.npdm, dlc/<title ID>/<n>/romfs, bare exefs+romfs
        if "exefs" in {x.lower() for x in os.listdir(here) if (here / x).is_dir()}:
            pid = npdm_program_id(here / "exefs" / "main.npdm")
            parent = here.parent.name.lower()
            if pid is not None and "%016X" % (pid & ~0xFFF) == gid and parent == "updates" and here.name.isdigit():
                found.append((str(here), "update", "%016X" % ((pid & ~0xFFF) | 0x800), int(here.name), "loose dump"))
        if here.parent.parent.name.lower() == "dlc" and re.fullmatch(r"[0-9A-Fa-f]{16}", here.parent.name) and \
                any(x.lower() == "romfs" for x in os.listdir(here)) and belongs(gid, here.parent.name):
            found.append((str(here), "dlc", here.parent.name.upper(), 0, "loose dump"))
        for f in files:
            if not f.lower().endswith(".nup"):
                continue
            path = here / f
            try:
                pk = describe(path)
            except Exception:
                continue
            for p in pk:
                if p["kind"] in ("update", "dlc") and belongs(gid, p["title_id"]):
                    found.append((str(path), p["kind"], p["title_id"], p["version"], "package %d" % p["index"]))
    return found


def scan(d):
    s = d["scope"]
    mode = d["operation"][5:]
    modes = ("fast", "deep") if mode == "both" else (mode,)
    found, budget = {}, [0]
    cancelled = lambda: read(job_path(d["job_id"])).get("cancel_requested")
    game_path = Path(s["file"]).resolve()
    for m in modes:
        for drive in mounted_drives():
            tops = fast_roots(drive) if m == "fast" else [drive]
            for top in tops:
                if cancelled():
                    raise InterruptedError("Scan cancelled; previous results kept")
                d["phase"] = m.upper() + " SCAN " + str(top); save(d)
                for src, kind, tid, ver, how in candidates_in(top, s["game"], m == "deep", cancelled, budget):
                    if Path(src).resolve() == game_path:
                        continue
                    token = "external:" + hashlib.sha256(("%s|%s|%s|%d|%s" % (src, kind, tid, ver, how)).encode()).hexdigest()
                    found[token] = {"token": token, "source": src, "kind": kind, "title_id": tid, "version": ver, "how": how}
                    if len(found) > 100:
                        raise ValueError("More than 100 matching items; previous results kept")
    for kind in ("update", "dlc"):
        atom(index_path(dict(s, kind=kind)), {"game": s["game"], "candidates": [c for c in found.values() if c["kind"] == kind],
                                              "created": time.time(), "directories": budget[0]})
    d["scan_matches"] = len(found)


# ---------------------------------------------------------------- actions
def running_job():
    for f in (STATE / "jobs").glob("*/state.json"):
        try:
            j = read(f)
        except (OSError, ValueError):
            continue
        if j.get("status") == "RUNNING" and j.get("boot_id") == boot() and alive(j):
            return j
    return None


def alive(j):
    try:
        parts = Path("/proc/%d/stat" % j["pid"]).read_text().rsplit(")", 1)[1].split()
        return parts[0] != "Z" and parts[19] == j.get("process_start")
    except (OSError, TypeError, KeyError, IndexError):
        return False


def require_stopped():
    r = subprocess.run(["pgrep", "-f", LICHII_RUNNING], capture_output=True)
    if r.returncode == 0:
        raise ValueError("Close the game first: Lichii is running")


def manual_index_path(s): return STATE / "index" / ("%s-%s-manual.json" % (s["game"], s["kind"]))


def source_entry(s, token):
    found = []
    for path in (index_path(s), manual_index_path(s)):
        doc = read(path) if path.exists() else {}
        found += [c for c in doc.get("candidates", []) if c["token"] == token]
    if len(found) != 1:
        raise ValueError("Selection is no longer in the scan results; scan again")
    return found[0]


def store_path(gid, kind, tid, ver): return store_dir(gid) / ("%s-%s-v%d.nup" % (kind, tid, ver))


def manual_list(s):
    """MANUAL INSTALL: every Switch 2 update or DLC package the content inspector found that is not already
    in this game, in GCM's store or detected for it, including packages with another game's ID. Each is
    offered with a warning; choosing one copies it to GCM's store for this game (prepare)."""
    try:
        rows = json.loads(INSPECTION.read_text()).get("content", [])
    except (OSError, ValueError):
        rows = []
    g = {"file": s["file"], "title_id": s["game"]}
    known = set(game_items(g)) | set(store_items(s["game"]))
    doc = read(index_path(s)) if index_path(s).exists() else {}
    known |= {item_id(s["game"], c["kind"], c["title_id"], c["version"]) for c in doc.get("candidates", [])}
    game_path = Path(s["file"]).resolve()
    found = {}
    for r in rows:
        if r.get("console") != "switch2" or r.get("kind") != s["kind"] or not r.get("title_id"):
            continue
        src = P(r["file"])
        if not src.exists() or src.resolve() == game_path:
            continue
        version = r.get("version") if isinstance(r.get("version"), int) else 0
        how = "loose dump"
        if src.is_file():
            try:
                pk = [p for p in describe(src) if p["kind"] == r["kind"] and p["title_id"] == r["title_id"] and p["version"] == version]
            except Exception:
                continue
            if not pk:
                continue
            how = "package %d" % pk[0]["index"]
        if item_id(s["game"], r["kind"], r["title_id"], version) in known:
            continue
        token = "manual:" + hashlib.sha256(("%s|%s|%s|%d|%s" % (src, r["kind"], r["title_id"], version, how)).encode()).hexdigest()
        found[token] = {"token": token, "source": str(src), "kind": r["kind"], "title_id": r["title_id"], "version": version,
                        "how": how, "manual": True, "matches": belongs(s["game"], r["title_id"])}
        if len(found) >= 100:
            break
    atom(manual_index_path(s), {"game": s["game"], "candidates": list(found.values()), "created": time.time()})
    return list(found.values())


def prepare(s, token, d):
    """Copies a detected source into GCM's store as a checked single-package file."""
    c = source_entry(s, token)
    out = store_path(s["game"], c["kind"], c["title_id"], c["version"])
    if c.get("manual"):
        out = store_dir(s["game"]) / ("manual-%s-%s-v%d.nup" % (c["kind"], c["title_id"], c["version"]))
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tool = nup()
    src = Path(c["source"])
    part = out.with_name("." + out.name + ".gcm-part")
    try:
        if src.is_dir():
            spec = tool.pack_spec(src, c["kind"], int(c["title_id"], 16), c["version"])
            with watch(d, part, tool.spec_bytes(spec), "COPYING " + src.name):
                tool.write_nup([spec], part)
            tool.verify(part, full=True)
        else:
            n = tool.Nup(src)
            idx = int(c["how"].split()[1])
            spec = n.package_spec(idx)
            if (KINDS[spec["kind"]], "%016X" % spec["title_id"], spec["version"]) != (c["kind"], c["title_id"], c["version"]):
                raise ValueError("Source changed since the scan; scan again")
            with watch(d, part, tool.spec_bytes(spec), "COPYING " + src.name):
                _p, runs = tool.write_nup([spec], part, n.header_version)
            tool.verify(part, full=True)
            tool.check_copies(part, runs)
        os.replace(part, out)
    finally:
        if part.exists():
            part.unlink()


@contextlib.contextmanager
def watch(d, part, total, phase):
    """Reports a job's progress from the size of the file being written."""
    stop = threading.Event()
    def run():
        while not stop.wait(2):
            try:
                done = part.stat().st_size if part.exists() else 0
                d.update(phase=phase, percent=min(99, 100 * done // total) if total else -1); save(d)
            except Exception:
                pass
    t = threading.Thread(target=run, daemon=True); t.start()
    try:
        yield
    finally:
        stop.set(); t.join(5)


def rebuild(s, d, add=(), remove=(), phase="APPLYING CHANGES"):
    """One rewrite of the game file: packages `remove` (item IDs) are first copied to the store when the
    store has no copy, then left out; packages `add` (store files) are put in."""
    tool = nup()
    g = {"file": s["file"], "title_id": s["game"]}
    game_path = Path(s["file"])
    current = tool.Nup(game_path)
    inside = game_items(g)
    stored = store_items(s["game"])
    for iid in remove:
        if iid in inside and iid not in stored:
            p = inside[iid]
            out = store_path(s["game"], p["kind"], p["title_id"], p["version"])
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name("." + out.name + ".gcm-part")
            try:
                spec = current.package_spec(p["index"])
                with watch(d, part, tool.spec_bytes(spec), "SAVING " + label(p) + " TO GCM"):
                    _p, runs = tool.write_nup([spec], part, current.header_version)
                tool.verify(part, full=True, expect=[(spec["kind"], spec["title_id"], spec["version"])])
                tool.check_copies(part, runs)
                os.replace(part, out)
            finally:
                if part.exists():
                    part.unlink()
    keep = [current.package_spec(p["index"]) for p in describe(game_path)
            if item_id(s["game"], p["kind"], p["title_id"], p["version"]) not in remove]
    for f in add:
        src = tool.Nup(f)
        for i in range(len(src.packages)):
            spec = src.package_spec(i)
            keep = [k for k in keep if not tool.same_package(k, spec)]
            keep.append(spec)
    if not any(k["kind"] in (0, 1) for k in keep):
        raise ValueError("This change would leave the game without a program")
    require_stopped()
    part = game_path.with_name("." + game_path.name + ".gcm-part")
    with watch(d, part, sum(tool.spec_bytes(k) for k in keep), phase):
        tool.replace_game(game_path, keep, current.header_version)


def plan(s, operation, tokens):
    g = {"file": s["file"], "title_id": s["game"]}
    inside, stored = game_items(g), store_items(s["game"])
    known = set(inside) | set(stored)
    ids = []
    for t in tokens:
        if not re.fullmatch(r"folder:[0-9a-f]{64}", t):
            raise ValueError("Select GCM items first")
        if t[7:] not in known:
            raise ValueError("That item is no longer available; reopen the page")
        ids.append(t[7:])
    return inside, stored, ids


def perform(d):
    s, op, tokens = d["scope"], d["operation"], d["tokens"]
    if op.startswith("scan-"):
        return scan(d)
    if op == "prepare":
        if not tokens:
            raise ValueError("Select detected items first")
        d["total"] = len(tokens)
        for t in tokens:
            d["label"] = Path(source_entry(s, t)["source"]).name; save(d)
            prepare(s, t, d); d["complete"] += 1; save(d)
        return
    if op == "recover":
        part = Path(s["file"]).with_name("." + Path(s["file"]).name + ".gcm-part")
        if part.exists():
            part.unlink()
        for f in store_dir(s["game"]).glob(".*.gcm-part") if store_dir(s["game"]).is_dir() else []:
            f.unlink()
        return
    inside, stored, ids = plan(s, op, tokens)
    if op == "forget":
        # REMOVE GCM COPY: only for items that are in the game file, so the game keeps its own copy.
        for iid in ids:
            if iid not in inside:
                raise ValueError("This item is not in the game; its GCM copy is the only one, so it is kept")
            if iid in stored:
                Path(stored[iid]["file"]).unlink()
            d["complete"] += 1; save(d)
        return
    require_stopped()
    d["mutation_started"] = True; d["total"] = max(1, len(ids)); save(d)
    if op in ("install", "enable"):
        add, remove = [], []
        for iid in ids:
            if iid in inside:
                continue
            p = stored[iid]
            add.append(p["file"])
            if p["kind"] == "update":  # one update in the game: newer or older ones go back to the store
                remove += [k for k, q in inside.items() if q["kind"] == "update" and k != iid]
        if add:
            rebuild(s, d, add, set(remove), "INSTALLING")
    elif op in ("disable", "uninstall"):
        out = [iid for iid in ids if iid in inside]
        if out:
            rebuild(s, d, (), set(out), "REMOVING")
        if op == "uninstall":
            # The store copy is deleted only when the detected source it came from still exists.
            index = []
            for kind in ("update", "dlc"):
                f = index_path(dict(s, kind=kind))
                if f.exists():
                    index += read(f).get("candidates", [])
            for iid in ids:
                p = store_items(s["game"]).get(iid)
                if p and any(item_id(s["game"], c["kind"], c["title_id"], c["version"]) == iid and Path(c["source"]).exists() for c in index):
                    Path(p["file"]).unlink()
    elif op == "apply":
        if s["kind"] != "dlc":
            raise ValueError("Apply is for DLC")
        want = set(ids)
        add = [stored[i]["file"] for i in want if i not in inside and i in stored and stored[i]["kind"] == "dlc"]
        remove = {i for i, q in inside.items() if q["kind"] == "dlc" and i not in want}
        if add or remove:
            rebuild(s, d, add, remove, "APPLYING CHANGES")
    d["complete"] = d["total"]


# ---------------------------------------------------------------- jobs (shared menu wire protocol)
def job_path(job_id):
    if not re.fullmatch("[0-9a-f]{32}", job_id or ""):
        raise ValueError("Invalid job ID")
    return STATE / "jobs" / job_id / "state.json"


def save(d):
    with lock(job_path(d["job_id"]).parent / ".lock"):
        cur = read(job_path(d["job_id"]))
        if cur.get("nonce") != d["nonce"]:
            raise ValueError("Job ownership changed")
        if cur.get("cancel_requested"):
            d["cancel_requested"] = True
        d["updated"] = time.time(); atom(job_path(d["job_id"]), d)


def start(s, operation, tokens, confirmed):
    if not confirmed:
        raise ValueError("Explicit action confirmation required")
    if operation not in OPS:
        raise ValueError("Unsupported Switch 2 action")
    with lock(STATE / ".start.lock", blocking=False):
        if running_job():
            raise ValueError("A Switch 2 job is already running")
        if not operation.startswith("scan-") and operation not in ("prepare", "recover"):
            inside, stored, ids = plan(s, operation, tokens)
            if operation == "forget":
                if any(i not in inside for i in ids):
                    raise ValueError("This item is not in the game; its GCM copy is the only one, so it is kept")
                if not any(i in stored for i in ids):
                    raise ValueError("There is no GCM copy to remove")
            else:
                require_stopped()
        job_id = uuid.uuid4().hex
        d = {"schema": SCHEMA, "job_id": job_id, "nonce": uuid.uuid4().hex, "scope": s, "operation": operation, "tokens": tokens,
             "boot_id": boot(), "pid": None, "process_start": None, "status": "RUNNING", "phase": "QUEUED", "label": "",
             "total": len(tokens), "complete": 0, "percent": -1, "error": "", "recovery_required": False,
             "created_epoch": time.time(), "mutation_started": False}
        atom(job_path(job_id), d)
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "worker", "--job-id", job_id],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            try:
                st = Path("/proc/%d/stat" % child.pid).read_text().rsplit(")", 1)[1].split()[19]
                break
            except (OSError, IndexError):
                time.sleep(0.02)
        else:
            st = None
        d.update(pid=child.pid, process_start=st)
        cur = read(job_path(job_id))
        if cur["status"] == "RUNNING":
            cur.update(pid=child.pid, process_start=st); atom(job_path(job_id), cur); d = cur
        return d


def worker(job_id):
    path = job_path(job_id)
    for _ in range(100):  # wait for the starter to record our PID
        d = read(path)
        if d.get("pid") == os.getpid():
            break
        time.sleep(0.05)
    d = read(path)
    try:
        perform(d)
        d.update(status="PASS", phase="COMPLETE", percent=100)
    except Exception as exc:
        d.update(status="ERROR", phase="CANCELLED" if isinstance(exc, InterruptedError) else "STOPPED",
                 error=clean(exc), recovery_required=Path(d["scope"]["file"]).with_name("." + Path(d["scope"]["file"]).name + ".gcm-part").exists())
    with lock(path.parent / ".lock"):
        cur = read(path)
        if cur.get("cancel_requested"):
            d["cancel_requested"] = True
        atom(path, d)
    return 0 if d["status"] == "PASS" else 1


def status(s, operation, job_id=None, selected=None):
    if job_id:
        d = read(job_path(job_id))
    else:
        found = []
        for f in (STATE / "jobs").glob("*/state.json"):
            try:
                j = read(f)
            except (OSError, ValueError):
                continue
            if j.get("scope") != s:
                continue
            if not (j.get("operation") == operation or (operation == "scan" and j.get("operation", "").startswith("scan-"))):
                continue
            if selected is not None and j.get("tokens") != selected:
                continue
            found.append(j)
        if not found:
            return None
        d = max(found, key=lambda x: x["created_epoch"])
    if d["status"] == "RUNNING" and (d["boot_id"] != boot() or not alive(d)) and time.time() - d["created_epoch"] > 5:
        d = dict(d, status="ERROR", phase="INTERRUPTED", error="The job stopped unexpectedly; use RECOVER if offered",
                 recovery_required=bool(d.get("mutation_started")))
    return d


def emit(d, wire="BATCH"):
    if d is None:
        print(wire + "\tSTATUS\tNOT_STARTED"); return
    fields = {"OPERATION": d["operation"], "ELAPSED_SECONDS": int(time.time() - d["created_epoch"]), "STATUS": d["status"],
              "ID": d["job_id"], "JOB_ID": d["job_id"], "PHASE": d["phase"], "LABEL": d["label"], "TOTAL": d["total"],
              "COMPLETE": d["complete"], "PERCENT": d["percent"], "ERROR": d["error"],
              "RECOVERY_REQUIRED": "YES" if d.get("recovery_required") else "NO"}
    if wire == "INSTALL":
        fields["STATUS"] = "INSTALLED" if d["status"] == "PASS" else d["status"]
    for k, v in fields.items():
        print(wire + "\t" + k + "\t" + clean(v))
    if d["error"]:
        print("BATCH\tFAILURE\tSwitch 2 action\t" + clean(d["error"]))


def parse_tokens(raw):
    result = raw.split(",") if raw else []
    if len(result) > 32 or len(result) != len(set(result)):
        raise ValueError("Up to 32 unique selections required")
    return result


def summary(rom, emulator):
    s = scope(rom, emulator, "update")
    g = {"file": s["file"], "title_id": s["game"]}
    inside = game_items(g)
    upd = [p for p in inside.values() if p["kind"] == "update"]
    dlc = [p for p in inside.values() if p["kind"] == "dlc"]
    if upd:
        best = max(upd, key=lambda p: p["version"])
        u = "v" + shown_version(best) + " ACTIVE"
    else:
        u = "NONE"
    return "SUMMARY\t%s\t%d ENABLED" % (u, len(dlc))


def menu(args):
    if args.mode == "summary":
        print(summary(args.rom, args.emulator)); return
    s = scope(args.rom, args.emulator, args.type)
    if args.mode == "list":
        for row in inventory(s):
            state = row["state"]
            if state == "PREPARED":
                state = "DISABLED" if args.type == "dlc" else "FOLDER_PREPARED"
            elif args.type == "update":
                state = {"ENABLED": "ACTIVE", "DISABLED": "INSTALLED"}.get(state, state)
            print("\t".join(("ROW", row["section"], state, clean(row["label"]), row["token"])))
    elif args.mode == "manual-list":
        found = manual_list(s)
        if not found:
            print("ERROR\tNo other Switch 2 %s packages were found. GCM's content inspector looks at every drive at boot; use RESCAN NOW on the GAME CONTENT MANAGER page after adding files." % ("update" if args.type == "update" else "DLC"))
        for c in sorted(found, key=lambda c: (not c["matches"], Path(c["source"]).name)):
            label = "%s · %s v%d" % (Path(c["source"]).name, c["title_id"], c["version"])
            if c["matches"]:
                value, warn = "THIS GAME'S ID", "ITS ID MATCHES THIS GAME, BUT IT WAS NOT DETECTED BY A SCAN."
            else:
                value = "ANOTHER ID"
                warn = ("ITS TITLE ID %s IS NOT THIS GAME'S (%s). LICHII ONLY USES AN UPDATE OR DLC WHOSE ID MATCHES "
                        "THE GAME, SO IT WILL PROBABLY BE IGNORED." % (c["title_id"], s["game"]))
            print("\t".join(("ROW", "MANUAL", value, clean(label), c["token"], clean(warn))))
    elif args.mode == "capabilities":
        print("CAPABILITY\tMANUAL_INSTALL\t1")
        print("CAPABILITY\tCOMMIT_AVAILABLE_DLC\t1")
        print("CAPABILITY\tSELECT_UPDATE\t1")
    elif args.mode == "select-update":
        d = start(s, "install", parse_tokens(args.token), args.confirm)
        while d["status"] == "RUNNING":
            time.sleep(0.5); d = status(s, "install", d["job_id"])
        if d["status"] != "PASS":
            raise ValueError(d["error"])
        print("OK\tCONFIGURED_UPDATE_SELECTED")
    elif args.mode == "install-job":
        d = start(s, "install", parse_tokens(args.token), True) if args.menu_action == "start" else status(s, "install", selected=parse_tokens(args.token))
        emit(d, "INSTALL")
    else:
        raise ValueError("Unsupported Switch 2 menu operation")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", nargs="?", choices=("start", "status", "worker", "cancel", "capabilities"))
    p.add_argument("--rom"); p.add_argument("--emulator"); p.add_argument("--type", choices=("update", "dlc"), default="update")
    p.add_argument("--mode"); p.add_argument("--operation", choices=OPS + ("scan",)); p.add_argument("--tokens", default=""); p.add_argument("--token", default="")
    p.add_argument("--action", dest="menu_action"); p.add_argument("--job-id"); p.add_argument("--confirm", action="store_true")
    p.add_argument("--wire", default="BATCH", choices=("BATCH", "ACTION", "INSTALL"))
    args = p.parse_args()
    if args.action == "worker":
        return worker(args.job_id)
    if args.mode:
        menu(args); return 0
    if args.action == "capabilities":
        print("CAPABILITY\tUNINSTALL_DLC\t1"); return 0
    if args.action == "cancel":
        path = job_path(args.job_id)
        with lock(path.parent / ".lock"):
            d = read(path)
            if not d["operation"].startswith("scan-") or d["status"] != "RUNNING":
                raise ValueError("Only running scans can be cancelled")
            d["cancel_requested"] = True; atom(path, d)
        emit(d, args.wire); return 0
    s = scope(args.rom, args.emulator, args.type)
    if args.action == "start":
        emit(start(s, args.operation, parse_tokens(args.tokens), args.confirm), args.wire)
    elif args.action == "status":
        emit(status(s, args.operation, args.job_id, parse_tokens(args.tokens) if args.tokens else None), args.wire)
    else:
        raise ValueError("Switch 2 command required")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + clean(exc)); sys.exit(1)
