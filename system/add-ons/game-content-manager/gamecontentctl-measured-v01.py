#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import time
import fcntl
import stat
from pathlib import Path

APP_ROOT = Path(os.environ.get("GCM_APP_ROOT", "/userdata/system/add-ons/game-content-manager"))
DATA_ROOT = Path(os.environ.get("GCM_DATA_ROOT", "/userdata/system/game-content"))
DB_PATH = APP_ROOT / "index.sqlite"
RYUJINX_ROOT = Path(os.environ.get("GCM_RYUJINX_ROOT", "/userdata/system/configs/Ryujinx"))
SWITCH_KEYS_DIR = Path(os.environ.get("GCM_SWITCH_KEYS_DIR", "/userdata/bios/switch/keys"))
SCAN_PROFILES = {
    "switch": {
        "extensions": {".nsp", ".nsz"},
        "optional_extensions": {".xci", ".xcz"},
        "marker_names": set(),
        "description": "Nintendo Switch update/DLC packages",
    },
    "ps3": {
        "extensions": {".pkg"},
        "optional_extensions": set(),
        "marker_names": set(),
        "description": "PlayStation 3 PKG content",
    },
    "ps4": {
        "extensions": {".pkg"},
        "optional_extensions": set(),
        "marker_names": set(),
        "description": "PlayStation 4 PKG content",
    },
    "3ds": {
        "extensions": {".cia"},
        "optional_extensions": set(),
        "marker_names": set(),
        "description": "Nintendo 3DS CIA content",
    },
    "wiiu": {
        "extensions": set(),
        "optional_extensions": set(),
        "marker_names": {"meta.xml"},
        "description": "Wii U update/DLC metadata folders",
    },
    "psvita": {
        "extensions": {".pkg", ".vpk", ".zip", ".vci"},
        "optional_extensions": set(),
        "marker_names": set(),
        "description": "PlayStation Vita installable content",
    },
    "xbox360": {
        "extensions": {".stfs", ".con", ".live", ".pirs"},
        "optional_extensions": {""},
        "marker_names": set(),
        "description": "Xbox 360 STFS content",
    },
}

SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/tmp", "/boot", "/overlay")
PRUNE_PREFIXES = (
    "/userdata/system/var/lib/containers",
    "/userdata/system/gcm-es-build",
    "/userdata/system/gcm-native-test",
    "/userdata/system/gcm-es-native-test",
    "/userdata/system/add-ons",
    "/userdata/system/cache",
    "/userdata/system/.cache",
    "/userdata/system/logs",
    "/userdata/system/switch/appimages",
    "/userdata/system/configs/Trash",
    "/userdata/system/game-content",
)
PRUNE_DIR_NAMES = {
    # Keep universal pruning conservative because the user explicitly wants
    # packages discoverable from arbitrary folders on external storage.
    "$RECYCLE.BIN", "System Volume Information", "lost+found",
    "__pycache__", ".git", ".cache",
}
TITLE_RE = re.compile(r"(?i)(?:^|[^0-9a-f])([0-9a-f]{16})(?:[^0-9a-f]|$)")
VERSION_RE = re.compile(r"(?i)(?:^|[^a-z0-9])v(?:ersion)?[ _.-]?(\d+)(?:[^0-9]|$)")
SEMVER_RE = re.compile(r"(?i)(?:^|[^a-z0-9])v(\d+\.\d+(?:\.\d+)?)(?:[^0-9.]|$)")
BRACKET_BUILD_RE = re.compile(r"(?i)\[(\d+)\]\[(?:UPD|DLC)\]")



# Optional measured telemetry; original content checks remain authoritative.
def _gcm_progress():
    import hashlib as _hashlib
    import importlib.util as _importlib
    from pathlib import Path as _Path
    if "_GCM_PROGRESS_MODULE" not in globals():
        _path = _Path('/userdata/system/gcm-native-test/step61-common-content/gcm-global-action-progress-measured-v01.py')
        if _path.is_symlink() or _hashlib.sha256(_path.read_bytes()).hexdigest() != 'd51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
            raise ValueError("Measured progress dependency changed")
        _spec = _importlib.spec_from_file_location("gcm_measured_progress", _path)
        _module = _importlib.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        globals()["_GCM_PROGRESS_MODULE"] = _module
    return globals()["_GCM_PROGRESS_MODULE"]

def db():
    APP_ROOT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute(
        """CREATE TABLE IF NOT EXISTS packages(
        path TEXT PRIMARY KEY,
        system TEXT,
        title_id TEXT,
        application_id TEXT,
        content_type TEXT,
        version TEXT,
        size INTEGER,
        mtime REAL,
        sha256 TEXT,
        state TEXT,
        source_path TEXT,
        metadata_source TEXT,
        last_seen REAL
        )"""
    )
    cols = {row[1] for row in c.execute("PRAGMA table_info(packages)")}
    for name, decl in (("application_id", "TEXT"), ("metadata_source", "TEXT"), ("version", "TEXT")):
        if name not in cols:
            c.execute(f"ALTER TABLE packages ADD COLUMN {name} {decl}")
    c.execute(
        """CREATE TABLE IF NOT EXISTS deep_scan_state(
        system TEXT,
        mount_path TEXT,
        device TEXT,
        status TEXT,
        started REAL,
        completed REAL,
        candidates INTEGER,
        error TEXT,
        PRIMARY KEY(system, mount_path)
        )"""
    )
    c.commit()
    return c


def sha256(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    progress = _gcm_progress()
    total, completed = Path(path).stat().st_size, 0
    progress.publish("VERIFYING REGISTERED FILE", Path(path).name, 0, total)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
            completed += len(b)
            progress.publish("VERIFYING REGISTERED FILE", Path(path).name, completed, total)
    return h.hexdigest()


def scan_profile(system):
    if system not in SCAN_PROFILES:
        raise ValueError(f"No scan profile is defined for system: {system}")
    profile = dict(SCAN_PROFILES[system])
    profile["extensions"] = set(profile.get("extensions", set()))
    profile["optional_extensions"] = set(profile.get("optional_extensions", set()))
    profile["marker_names"] = set(profile.get("marker_names", set()))
    if system == "switch" and os.environ.get("GCM_SWITCH_SCAN_XCI", "").lower() in ("1", "true", "yes", "on"):
        profile["extensions"] |= profile["optional_extensions"]
    if system == "xbox360" and os.environ.get("GCM_XBOX360_SCAN_EXTENSIONLESS", "").lower() in ("1", "true", "yes", "on"):
        profile["extensions"] |= profile["optional_extensions"]
    return profile


def is_profile_candidate(path, system, profile=None):
    profile = profile or scan_profile(system)
    p = Path(path)
    if p.name.lower() in {x.lower() for x in profile.get("marker_names", set())}:
        return True
    return p.suffix.lower() in profile.get("extensions", set())


def detect_system(path, requested_system=None):
    # Extensions such as .pkg are shared by multiple consoles, so the selected
    # system profile is authoritative whenever one is supplied.
    if requested_system:
        try:
            return requested_system if is_profile_candidate(path, requested_system) else ""
        except ValueError:
            return ""
    matches = [s for s in SCAN_PROFILES if is_profile_candidate(path, s)]
    return matches[0] if len(matches) == 1 else "ambiguous" if matches else ""


def classify_name(path):
    n = Path(path).name.lower()
    if any(x in n for x in ("update", "patch", " upd", "[upd", "_upd")):
        return "update"
    if any(x in n for x in ("dlc", "addon", "add-on", "aoc")):
        return "dlc"
    return "unknown"


def infer_title_id(path):
    m = TITLE_RE.search(Path(path).name)
    return m.group(1).upper() if m else ""


def infer_version(path):
    name = Path(path).name

    # Preserve complete dotted versions from package filenames.
    # These are display labels, not verified internal update versions.
    dotted = re.search(
        r"(?i)(?<![a-z0-9])v(?:ersion)?[ _.-]*(\d+(?:\.\d+){1,3})(?!\d)",
        name,
    )
    if dotted:
        build = BRACKET_BUILD_RE.search(name)
        version = dotted.group(1)
        return f"{version} ({build.group(1)})" if build else version
    sem = SEMVER_RE.search(name)
    build = BRACKET_BUILD_RE.search(name)
    if sem and build:
        return f"{sem.group(1)} ({build.group(1)})"
    if sem:
        return sem.group(1)
    m = VERSION_RE.search(name)
    return m.group(1) if m else (build.group(1) if build else "")


def rom_title_id(rom):
    # Prefer an application ID explicitly present in the ROM filename.
    direct = infer_title_id(rom)
    if direct:
        return direct

    # Resolve ordinary filenames through Ryujinx's existing game metadata.
    # This is a conservative title-based fallback, not a ROM-content parser.
    try:
        path = Path(str(rom))
        if path.suffix.lower() not in (".nsp", ".nsz", ".xci", ".xcz"):
            return ""
        if not path.is_file():
            return ""

        wanted = re.sub(r"\\s+", " ", path.stem).strip().casefold()
        if not wanted:
            return ""

        games = Path("/userdata/system/configs/Ryujinx/games")
        if not games.is_dir():
            return ""

        matches = set()

        for directory in games.iterdir():
            if not directory.is_dir():
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{16}", directory.name):
                continue

            metadata = directory / "gui" / "metadata.json"
            if not metadata.is_file():
                continue

            try:
                data = json.loads(metadata.read_text())
            except (OSError, UnicodeError, ValueError):
                continue

            if not isinstance(data, dict):
                continue

            title = data.get("title")
            if not isinstance(title, str):
                continue

            candidate = re.sub(r"\\s+", " ", title).strip().casefold()

            if candidate == wanted:
                matches.add(directory.name.upper())

        # No match or ambiguous matches: never guess.
        return next(iter(matches)) if len(matches) == 1 else ""

    except (OSError, ValueError, TypeError):
        return ""


def switch_related(base_id, candidate_id):
    if not base_id or not candidate_id:
        return False
    try:
        b = int(base_id, 16)
        c = int(candidate_id, 16)
    except ValueError:
        return False
    return c == b + 0x800 or (b + 0x1000 <= c <= b + 0x1FFF)


def _unescape_mount_field(value):
    # /proc/*/mountinfo escapes spaces/tabs/newlines/backslashes as octal.
    return re.sub(
        r"\\([0-7]{3})",
        lambda m: chr(int(m.group(1), 8)),
        value,
    )


_PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "ramfs", "rootfs",
    "overlay", "squashfs", "cgroup", "cgroup2", "pstore", "securityfs",
    "debugfs", "tracefs", "configfs", "mqueue", "hugetlbfs", "efivarfs",
    "autofs", "binfmt_misc", "fusectl",
}


def _mountinfo_roots():
    """Return user-storage mount roots without scanning generic container dirs.

    Batocera keeps its persistent share on /userdata and removable/extra drives
    under /media or /mnt.  We enumerate the actual mounts so a tmpfs such as
    /media itself is not walked, and deduplicate bind/alias mounts by device ID.
    """
    rows = []
    try:
        lines = Path("/proc/self/mountinfo").read_text(errors="replace").splitlines()
    except Exception:
        lines = []

    for line in lines:
        try:
            left, right = line.split(" - ", 1)
            lf = left.split()
            rf = right.split()
            dev_id = lf[2]
            mount_point = Path(_unescape_mount_field(lf[4]))
            mount_opts = set(lf[5].split(","))
            fs_type = rf[0]
            source = _unescape_mount_field(rf[1]) if len(rf) > 1 else ""
        except Exception:
            continue

        mp = str(mount_point)

        # GCM development filesystem is not user game storage.
        # Never traverse the Buildroot image during full-device scans.
        if (
            mp == "/mnt/gcm-batocera-build"
            or mp.startswith("/mnt/gcm-batocera-build/")
        ):
            continue

        wanted = mp == "/userdata" or mp.startswith("/media/") or mp.startswith("/mnt/")
        if not wanted or fs_type in _PSEUDO_FS:
            continue
        if not mount_point.exists() or not mount_point.is_dir():
            continue
        rows.append({
            "path": mount_point,
            "device": dev_id,
            "fs_type": fs_type,
            "source": source,
        })

    # Prefer /userdata, then /media, then /mnt when aliases refer to one device.
    def pref(row):
        p = str(row["path"])
        rank = 0 if p == "/userdata" else 1 if p.startswith("/media/") else 2
        return (rank, len(p), p)

    out, seen_devices = [], set()
    for row in sorted(rows, key=pref):
        key = row["device"]
        if key in seen_devices:
            continue
        seen_devices.add(key)
        out.append(row)
    return out


def candidate_mounts():
    override = os.environ.get("GCM_SCAN_ROOTS", "").strip()
    if override:
        # Test/developer override: preserve distinct explicit paths even when
        # they happen to live on one backing filesystem.
        out, seen = [], set()
        for raw in (x for x in override.split(":") if x):
            p = Path(raw)
            if not p.exists() or not p.is_dir():
                continue
            try:
                rp = str(p.resolve())
                st = p.stat()
            except Exception:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            out.append({
                "path": p,
                "device": f"override:{st.st_dev}:{rp}",
                "fs_type": "override",
                "source": "override",
            })
        return out

    rows = _mountinfo_roots()
    if rows:
        return rows

    # Conservative fallback for unusual test/minimal environments.
    out, seen = [], set()
    for p in (Path("/userdata"),):
        if not p.exists():
            continue
        try:
            st = p.stat()
        except Exception:
            continue
        if st.st_dev in seen:
            continue
        seen.add(st.st_dev)
        out.append({"path": p, "device": str(st.st_dev), "fs_type": "unknown", "source": "fallback"})
    for parent in (Path("/media"), Path("/mnt")):
        if not parent.is_dir():
            continue
        for p in parent.iterdir():
            if not p.is_dir():
                continue
            try:
                st = p.stat()
            except Exception:
                continue
            if st.st_dev in seen:
                continue
            seen.add(st.st_dev)
            out.append({"path": p, "device": str(st.st_dev), "fs_type": "unknown", "source": "fallback"})
    return out


def candidate_roots():
    return [row["path"] for row in candidate_mounts()]


SYSTEM_ALIASES = {
    "switch": ("switch", "nintendo switch"),
    "ps3": ("ps3", "playstation 3"),
    "ps4": ("ps4", "playstation 4"),
    "3ds": ("3ds", "nintendo 3ds"),
    "wiiu": ("wiiu", "wii u"),
    "psvita": ("psvita", "vita", "ps vita"),
    "xbox360": ("xbox360", "xbox 360", "360"),
}


def _dedupe_roots(paths):
    out = []
    seen = set()
    for p in paths:
        try:
            rp = str(Path(p).resolve())
        except Exception:
            continue
        if rp in seen or not Path(rp).is_dir():
            continue
        # Keep separate sibling roots, but avoid adding a root contained by an
        # already selected broader root.
        if any(rp == x or rp.startswith(x.rstrip("/") + "/") for x in seen):
            continue
        seen.add(rp)
        out.append(Path(rp))
    return out


def fast_roots_for_mount(mount_path, system):
    """Return likely console-specific roots without walking the whole drive."""
    root = Path(mount_path)
    aliases = SYSTEM_ALIASES.get(system, (system,))
    candidates = []

    # Common Batocera / archive layouts.
    for alias in aliases:
        for rel in (
            Path("roms") / alias,
            Path("emulators") / "roms" / alias,
            Path("emulators") / alias,
            Path("games") / alias,
            Path("Games") / alias,
            Path(alias),
        ):
            candidates.append(root / rel)

    # Batocera's persistent ROM tree is especially common.
    if str(root) == "/userdata":
        for alias in aliases:
            candidates.append(root / "roms" / alias)

    # Shallow name discovery catches folders such as /userdata/switch DLC updates
    # without recursing into the drive.  Limit this to the root and a few common
    # container directories so it stays effectively instant.
    shallow_parents = [root]
    for rel in ("emulators", "games", "Games", "backups", "Backup", "downloads", "Downloads"):
        p = root / rel
        if p.is_dir():
            shallow_parents.append(p)
    needles = tuple(a.lower() for a in aliases)
    for parent in shallow_parents:
        try:
            with os.scandir(parent) as it:
                for ent in it:
                    try:
                        if not ent.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    low = ent.name.lower()
                    if any(n in low for n in needles):
                        candidates.append(Path(ent.path))
        except OSError:
            pass

    # Remove nonexistent and duplicate roots, then remove roots nested beneath
    # another chosen root.
    existing = []
    for p in candidates:
        if p.is_dir():
            try:
                existing.append(Path(p.resolve()))
            except Exception:
                pass
    uniq = []
    for p in sorted(set(existing), key=lambda x: (len(str(x)), str(x))):
        ps = str(p)
        if any(ps.startswith(str(q).rstrip("/") + "/") for q in uniq):
            continue
        uniq.append(p)
    return uniq


def fast_scan_roots(system):
    rows = []
    for mount in candidate_mounts():
        roots = fast_roots_for_mount(mount["path"], system)
        rows.append(dict(mount, fast_roots=roots))
    return rows


def ryujinx_game_dir(base_id):
    return RYUJINX_ROOT / "games" / base_id.lower()


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def _fsync_dir(path):
    try:
        fd = os.open(str(Path(path).parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(tmpname)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path)
    finally:
        tmp.unlink(missing_ok=True)


def backup_config(path):
    path = Path(path)
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".before-gcm-{stamp}.bak")
    i = 1
    while backup.exists():
        backup = path.with_name(path.name + f".before-gcm-{stamp}-{i}.bak")
        i += 1
    shutil.copy2(path, backup)
    return str(backup)


def pfs0_names(path):
    path = Path(path)
    if path.suffix.lower() != ".nsp":
        return []
    with open(path, "rb") as f:
        hdr = f.read(0x10)
        if len(hdr) != 0x10 or hdr[:4] != b"PFS0":
            return []
        count, str_size, _ = struct.unpack("<III", hdr[4:16])
        if count > 100000 or str_size > 64 * 1024 * 1024:
            return []
        entries = f.read(count * 0x18)
        strings = f.read(str_size)
        if len(entries) != count * 0x18 or len(strings) != str_size:
            return []
        out = []
        for i in range(count):
            off = i * 0x18
            _, _, name_off, _ = struct.unpack("<QQII", entries[off:off + 0x18])
            if name_off >= len(strings):
                continue
            end = strings.find(b"\0", name_off)
            if end < 0:
                end = len(strings)
            out.append(strings[name_off:end].decode("utf-8", "replace"))
        return out


def ryujinx_installed_state(base_id):
    base_id = (base_id or "").upper()
    gd = ryujinx_game_dir(base_id) if base_id else None
    out = {"application_id": base_id, "updates": [], "dlc": []}
    if not gd:
        return out

    updates_path = gd / "updates.json"
    updates = read_json(updates_path, {})
    if isinstance(updates, dict):
        selected = updates.get("selected")
        for p in updates.get("paths", []) or []:
            if not isinstance(p, str):
                continue
            out["updates"].append({
                "path": p,
                "content_type": "update",
                "title_id": infer_title_id(p),
                "version": infer_version(p),
                "active": bool(selected and os.path.realpath(p) == os.path.realpath(selected)),
                "installed": True,
                "config": str(updates_path),
            })

    dlc_path = gd / "dlc.json"
    dlcs = read_json(dlc_path, [])
    if isinstance(dlcs, list):
        for entry in dlcs:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            tids = []
            for n in entry.get("dlc_nca_list", []) or []:
                if not isinstance(n, dict) or n.get("title_id") is None:
                    continue
                try:
                    tids.append(f"{int(n['title_id']):016X}")
                except Exception:
                    pass
            out["dlc"].append({
                "path": entry["path"],
                "content_type": "dlc",
                "title_id": tids[0] if tids else infer_title_id(entry["path"]),
                "version": infer_version(entry["path"]),
                "active": any(bool(n.get("is_enabled", True)) for n in entry.get("dlc_nca_list", []) if isinstance(n, dict)),
                "installed": True,
                "config": str(dlc_path),
                "dlc_nca_list": entry.get("dlc_nca_list", []),
            })
    return out


def ryujinx_known_content(base_id):
    out = {}
    state = ryujinx_installed_state(base_id)
    for row in state["updates"] + state["dlc"]:
        out[os.path.realpath(row["path"])] = {
            "application_id": state["application_id"],
            "title_id": row.get("title_id", ""),
            "content_type": row["content_type"],
            "metadata_source": "ryujinx",
            "version": row.get("version", ""),
            "dlc_nca_list": row.get("dlc_nca_list", []),
        }
    return out


def inspect_switch_candidate(path, base_id="", known=None, cached=None):
    path = Path(path)
    known = known if known is not None else (ryujinx_known_content(base_id) if base_id else {})
    cached = cached or {}
    rp = os.path.realpath(path)
    if rp in known:
        meta = dict(known[rp])
        meta.setdefault("version", infer_version(path))
        return meta
    if rp in cached:
        return dict(cached[rp])

    tid = infer_title_id(path)
    ctype = classify_name(path)
    appid = ""
    if base_id and tid and switch_related(base_id, tid):
        appid = base_id.upper()
        delta = int(tid, 16) - int(base_id, 16)
        ctype = "update" if delta == 0x800 else "dlc" if 0x1000 <= delta <= 0x1FFF else ctype
    elif base_id:
        appid = ""
    return {
        "application_id": appid,
        "title_id": tid,
        "content_type": ctype,
        "metadata_source": "filename" if tid else "unverified",
        "version": infer_version(path),
    }


def _find_prune_names():
    # Names only; path-specific Batocera runtime trees are added separately.
    return sorted(PRUNE_DIR_NAMES)


def _root_specific_prune_paths(root):
    root = Path(root)
    paths = []
    # These are only meaningful when the current mount contains them.
    for raw in PRUNE_PREFIXES:
        p = Path(raw)
        try:
            p.relative_to(root)
        except ValueError:
            continue
        paths.append(str(p))
    try:
        data = DATA_ROOT.resolve()
        data.relative_to(root.resolve())
        paths.append(str(data))
    except Exception:
        pass
    return sorted(set(paths))


def _native_find_command(root, system, profile):
    root = Path(root)
    cmd = ["find", str(root), "-xdev"]

    prune_terms = []
    for name in _find_prune_names():
        if prune_terms:
            prune_terms.append("-o")
        prune_terms.extend(["-name", name])
    for p in _root_specific_prune_paths(root):
        if prune_terms:
            prune_terms.append("-o")
        prune_terms.extend(["-path", p])

    if prune_terms:
        cmd.extend(["(", "-type", "d", "(", *prune_terms, ")", "-prune", ")", "-o"])

    candidate_terms = []
    for marker in sorted(profile.get("marker_names", set())):
        if candidate_terms:
            candidate_terms.append("-o")
        candidate_terms.extend(["-iname", marker])
    for ext in sorted(x for x in profile.get("extensions", set()) if x):
        if candidate_terms:
            candidate_terms.append("-o")
        candidate_terms.extend(["-iname", f"*{ext}"])
    if "" in profile.get("extensions", set()):
        if candidate_terms:
            candidate_terms.append("-o")
        candidate_terms.extend(["!", "-name", "*.*"])

    if not candidate_terms:
        # Impossible predicate, but keep command syntactically valid.
        candidate_terms = ["-false"]

    cmd.extend(["(", "-type", "f", "(", *candidate_terms, ")", "-print0", ")"])
    return cmd


def _run_native_discovery_roots(root_rows, system, profile):
    """Run targeted native find against console-specific roots in parallel."""
    jobs = []
    started = time.monotonic()
    for row in root_rows:
        for root in row.get("fast_roots", []):
            cmd = _native_find_command(root, system, profile)
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                jobs.append((row, root, cmd, proc))
            except Exception as exc:
                jobs.append((row, root, cmd, exc))

    paths = []
    root_stats = []
    for row, root, cmd, proc in jobs:
        rstart = time.monotonic()
        if isinstance(proc, Exception):
            root_stats.append({
                "path": str(root), "mount": str(row["path"]),
                "candidates": 0, "returncode": -1,
                "elapsed_seconds": 0, "error": str(proc),
            })
            continue
        out, err = proc.communicate()
        decoded = [os.fsdecode(x) for x in out.split(b"\0") if x] if out else []
        paths.extend(decoded)
        root_stats.append({
            "path": str(root), "mount": str(row["path"]),
            "candidates": len(decoded), "returncode": proc.returncode,
            "elapsed_seconds": round(time.monotonic() - rstart, 3),
            "error": os.fsdecode(err).strip()[:500] if err else "",
        })
    return paths, root_stats, round(time.monotonic() - started, 3)


def _switch_base_from_candidate(meta):
    tid = (meta.get("title_id") or "").upper()
    ctype = meta.get("content_type") or "unknown"
    if not tid:
        return ""
    try:
        n = int(tid, 16)
    except ValueError:
        return ""
    if ctype == "update" and n >= 0x800:
        return f"{n - 0x800:016X}"
    if ctype == "dlc":
        return f"{n & ~0x1FFF:016X}"
    return ""


def _upsert_external(conn, p, system, meta, now):
    try:
        st = Path(p).stat()
    except OSError:
        return None
    appid = meta.get("application_id", "")
    if system == "switch" and not appid:
        appid = _switch_base_from_candidate(meta)
    conn.execute(
        """INSERT INTO packages(path,system,title_id,application_id,content_type,version,size,mtime,sha256,state,source_path,metadata_source,last_seen)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET system=excluded.system,title_id=excluded.title_id,application_id=excluded.application_id,
        content_type=excluded.content_type,version=excluded.version,size=excluded.size,mtime=excluded.mtime,
        source_path=excluded.source_path,metadata_source=excluded.metadata_source,last_seen=excluded.last_seen""",
        (str(p), system, meta.get("title_id", ""), appid,
         meta.get("content_type", "unknown"), meta.get("version", ""), st.st_size, st.st_mtime,
         "", "external", str(p), meta.get("metadata_source", ""), now),
    )
    return st, appid


def _fast_discovery(system, profile):
    mounts = fast_scan_roots(system)
    paths, root_stats, seconds = _run_native_discovery_roots(mounts, system, profile)
    return mounts, paths, root_stats, seconds


def _deep_find_command(root, system, profile, fast_roots):
    cmd = ["find", str(root), "-xdev"]
    prune_terms = []
    for name in _find_prune_names():
        if prune_terms: prune_terms.append("-o")
        prune_terms.extend(["-name", name])
    for p in _root_specific_prune_paths(root):
        if prune_terms: prune_terms.append("-o")
        prune_terms.extend(["-path", p])
    for p in fast_roots:
        if prune_terms: prune_terms.append("-o")
        prune_terms.extend(["-path", str(p)])

    # Prune well-known ROM directories for other systems.  Arbitrary folders
    # remain searchable, preserving the full-device discovery requirement.
    other_aliases = []
    for sysname, aliases in SYSTEM_ALIASES.items():
        if sysname == system:
            continue
        other_aliases.extend(aliases)
    for alias in other_aliases:
        for pattern in (f"*/roms/{alias}", f"*/emulators/roms/{alias}", f"*/emulators/{alias}"):
            if prune_terms: prune_terms.append("-o")
            prune_terms.extend(["-ipath", pattern])

    if prune_terms:
        cmd.extend(["(", "-type", "d", "(", *prune_terms, ")", "-prune", ")", "-o"])

    candidate_terms = []
    for marker in sorted(profile.get("marker_names", set())):
        if candidate_terms: candidate_terms.append("-o")
        candidate_terms.extend(["-iname", marker])
    for ext in sorted(x for x in profile.get("extensions", set()) if x):
        if candidate_terms: candidate_terms.append("-o")
        candidate_terms.extend(["-iname", f"*{ext}"])
    if not candidate_terms:
        candidate_terms = ["-false"]
    # No -type f here: on NTFS that can add a stat for every entry.  Candidate
    # paths are stat'ed only after the name filter emits them.
    cmd.extend(["(", "(", *candidate_terms, ")", "-print0", ")"])
    return cmd


def deep_index(system, max_seconds=0, force=False):
    """Index unknown locations without blocking the foreground manager scan."""
    profile = scan_profile(system)
    lock_path = Path(f"/tmp/game-content-manager-deep-{system}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return {"started": False, "reason": "already-running"}

    conn = db()
    now = time.time()
    started = time.monotonic()
    totals = {"candidates": 0, "mounts": []}
    try:
        for row in fast_scan_roots(system):
            root = row["path"]
            state = conn.execute(
                "SELECT status,completed FROM deep_scan_state WHERE system=? AND mount_path=?",
                (system, str(root)),
            ).fetchone()
            if state and state[0] == "complete" and not force:
                # A completed background index remains authoritative for one day;
                # normal known-root scans still run every time and catch common
                # additions instantly.
                completed = float(state[1] or 0)
                if time.time() - completed < 86400:
                    totals["mounts"].append({"path": str(root), "status": "cached", "candidates": 0})
                    continue

            conn.execute(
                "INSERT OR REPLACE INTO deep_scan_state(system,mount_path,device,status,started,completed,candidates,error) VALUES(?,?,?,?,?,?,?,?)",
                (system, str(root), row.get("device", ""), "running", time.time(), 0, 0, ""),
            )
            conn.commit()
            cmd = _deep_find_command(root, system, profile, row.get("fast_roots", []))
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            buf = b""
            count = 0
            timed_out = False
            while True:
                if max_seconds and time.monotonic() - started >= max_seconds:
                    timed_out = True
                    proc.terminate()
                    break
                chunk = proc.stdout.read(65536) if proc.stdout else b""
                if not chunk:
                    break
                buf += chunk
                parts = buf.split(b"\0")
                buf = parts.pop()
                for raw in parts:
                    if not raw:
                        continue
                    p = Path(os.fsdecode(raw))
                    if not is_profile_candidate(p, system, profile):
                        continue
                    meta = inspect_candidate(p, system)
                    if _upsert_external(conn, p, system, meta, now):
                        count += 1
                        totals["candidates"] += 1
                        if count % 25 == 0:
                            conn.commit()
            if proc.poll() is None:
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait()
            err = b""
            if proc.stderr:
                try: err = proc.stderr.read()
                except Exception: pass
            status = "partial" if timed_out or proc.returncode == 1 else ("complete" if proc.returncode == 0 else "error")
            conn.execute(
                "INSERT OR REPLACE INTO deep_scan_state(system,mount_path,device,status,started,completed,candidates,error) VALUES(?,?,?,?,?,?,?,?)",
                (system, str(root), row.get("device", ""), status, now, time.time() if status == "complete" else 0, count, os.fsdecode(err).strip()[:1000]),
            )
            conn.commit()
            totals["mounts"].append({"path": str(root), "status": status, "candidates": count})
            if timed_out:
                break
        totals.update({"started": True, "system": system, "elapsed_seconds": round(time.monotonic()-started, 3)})
        return totals
    finally:
        conn.close()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def deep_index_status(system):
    conn = db()
    rows = conn.execute(
        "SELECT mount_path,device,status,started,completed,candidates,error FROM deep_scan_state WHERE system=? ORDER BY mount_path",
        (system,),
    ).fetchall()
    conn.close()
    keys = ["mount_path", "device", "status", "started", "completed", "candidates", "error"]
    return [dict(zip(keys, r)) for r in rows]


def launch_deep_index(system):
    """Start deep indexing detached if it is not already running/still fresh."""
    ctl = Path(__file__).resolve()
    log = APP_ROOT / f"deep-index-{system}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as f:
        try:
            subprocess.Popen(
                ["python3", str(ctl), "deep-index", "--system", system],
                stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            return {"launched": True, "log": str(log)}
        except Exception as exc:
            return {"launched": False, "error": str(exc)}

def _cached_meta_for(conn, system, base_id):
    out = {}
    if system == "switch" and base_id:
        rows = conn.execute(
            "SELECT path,title_id,application_id,content_type,version,size,mtime,source_path,metadata_source FROM packages WHERE application_id=?",
            (base_id,),
        )
    else:
        rows = conn.execute(
            "SELECT path,title_id,application_id,content_type,version,size,mtime,source_path,metadata_source FROM packages WHERE system=?",
            (system,),
        )
    for row in rows:
        pth, tid0, app0, ct0, ver0, size0, mt0, source0, ms0 = row
        meta0 = {
            "title_id": tid0 or "",
            "application_id": app0 or "",
            "content_type": ct0 or "unknown",
            "version": ver0 or "",
            "metadata_source": ms0 or "index",
        }
        if pth:
            out.setdefault(os.path.realpath(pth), dict(meta0, _size=size0, _mtime=mt0))
        if source0:
            out.setdefault(os.path.realpath(source0), dict(meta0, _size=size0, _mtime=None))
    return out


def _generic_candidate_meta(path, system):
    # Discovery profiles are available before every emulator-specific parser is.
    # Unverified candidates are indexed but never promoted to the per-game UI.
    return {
        "title_id": infer_title_id(path),
        "application_id": "",
        "content_type": classify_name(path),
        "version": infer_version(path),
        "metadata_source": f"{system}-candidate",
    }


def inspect_candidate(path, system, base_id="", known=None, cached=None):
    if system == "switch":
        return inspect_switch_candidate(path, base_id, known, cached)
    return _generic_candidate_meta(path, system)


def scan(system=None, title_id=None, rom=None):
    if not system:
        raise ValueError("A selected system is required for a fast scan profile")
    profile = scan_profile(system)
    conn = db()
    started = time.monotonic()
    now = time.time()
    found = []
    base_id = (title_id or (rom_title_id(rom) if rom else "")).upper()
    known = ryujinx_known_content(base_id) if system == "switch" and base_id else {}
    cached_known = _cached_meta_for(conn, system, base_id)
    seen_files = set()

    mounts, discovered, root_stats, discovery_seconds = _fast_discovery(system, profile)
    stats = {
        "system": system,
        "engine": "staged-fast-roots-v1",
        "profile": {
            "extensions": sorted(profile.get("extensions", set())),
            "marker_names": sorted(profile.get("marker_names", set())),
            "description": profile.get("description", ""),
        },
        "mounts": [{
            "path": str(x["path"]), "device": x.get("device", ""),
            "fs_type": x.get("fs_type", ""), "source": x.get("source", ""),
            "fast_roots": [str(r) for r in x.get("fast_roots", [])],
        } for x in mounts],
        "fast_roots": root_stats,
        "discovery_seconds": discovery_seconds,
        "candidate_paths_emitted": len(discovered),
        "candidates": 0, "cached_candidates": 0, "matched": 0,
        "duplicates_skipped": 0, "stat_failures": 0,
        "deep_index": deep_index_status(system),
    }

    for raw in discovered:
        p = Path(raw)
        if not is_profile_candidate(p, system, profile):
            continue
        stats["candidates"] += 1
        try:
            st = p.stat()
            if not stat.S_ISREG(st.st_mode):
                continue
        except OSError:
            stats["stat_failures"] += 1
            continue
        inode_key = (st.st_dev, st.st_ino)
        if inode_key in seen_files:
            stats["duplicates_skipped"] += 1
            continue
        seen_files.add(inode_key)
        rp = os.path.realpath(p)
        cached = cached_known.get(rp)
        if cached and (cached.get("_size") not in (None, st.st_size) or cached.get("_mtime") not in (None, st.st_mtime)):
            cached = None
        if cached:
            stats["cached_candidates"] += 1
        meta = inspect_candidate(p, system, base_id if system == "switch" else "", known, {rp: cached} if cached else {})
        up = _upsert_external(conn, p, system, meta, now)
        if not up:
            continue

        if system == "switch" and base_id:
            appid = meta.get("application_id", "") or _switch_base_from_candidate(meta)
            if appid.upper() != base_id:
                continue
        elif rom:
            continue
        stats["matched"] += 1
        found.append({
            "path": str(p), "system": system, "title_id": meta.get("title_id", ""),
            "application_id": base_id if system == "switch" else meta.get("application_id", ""),
            "content_type": meta.get("content_type", "unknown"), "version": meta.get("version", ""),
            "size": st.st_size, "state": "external", "metadata_source": meta.get("metadata_source", ""),
        })

    conn.commit(); conn.close()
    stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return found, stats

def game_key(system, rom):
    if system == "switch":
        tid = rom_title_id(rom)
        if tid:
            return tid.upper()
    stem = Path(rom).stem
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_")[:96] or "game"


def import_file(src, system, rom, content_type=None):
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(src)
    ctype = content_type or classify_name(src)
    if ctype not in ("update", "dlc"):
        raise ValueError("Package type is not verified as update/DLC; refusing transfer")

    base_id = rom_title_id(rom) if system == "switch" else ""
    if system == "switch" and base_id:
        meta = inspect_switch_candidate(src, base_id)
        if meta.get("application_id", "").upper() != base_id.upper():
            raise ValueError("Package is not verified for the selected Switch game")
        ctype = meta.get("content_type") or ctype
        if ctype not in ("update", "dlc"):
            raise ValueError("Package content type could not be verified")
    else:
        meta = {"title_id": infer_title_id(src), "application_id": base_id, "version": infer_version(src), "metadata_source": "filename"}

    destdir = DATA_ROOT / system / game_key(system, rom) / ("updates" if ctype == "update" else "dlc")
    destdir.mkdir(parents=True, exist_ok=True)
    dest = destdir / src.name

    if dest.exists():
        src_hash = sha256(src)
        dst_hash = sha256(dest)
        if src_hash != dst_hash:
            raise FileExistsError(f"Managed destination already exists with different content: {dest}")
        copied = False
    else:
        fd, tmpname = tempfile.mkstemp(prefix="." + src.name + ".", suffix=".part", dir=destdir)
        os.close(fd)
        tmp = Path(tmpname)
        try:
            with open(src, "rb") as rf, open(tmp, "wb") as wf:
                shutil.copyfileobj(rf, wf, 8 * 1024 * 1024)
                wf.flush(); os.fsync(wf.fileno())
            src_hash = sha256(src); dst_hash = sha256(tmp)
            if src_hash != dst_hash:
                raise IOError("SHA256 verification failed")
            os.replace(tmp, dest); _fsync_dir(dest)
            copied = True
        finally:
            tmp.unlink(missing_ok=True)

    conn = db(); st = dest.stat(); dst_hash = sha256(dest)
    conn.execute(
        """INSERT OR REPLACE INTO packages(path,system,title_id,application_id,content_type,version,size,mtime,sha256,state,source_path,metadata_source,last_seen)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (str(dest), system, meta.get("title_id", ""), meta.get("application_id", ""), ctype, meta.get("version", ""), st.st_size, st.st_mtime, dst_hash, "managed", str(src), meta.get("metadata_source", ""), time.time()),
    )
    conn.commit(); conn.close()
    return {"source": str(src), "destination": str(dest), "sha256": dst_hash, "copied": copied, "source_preserved": src.exists(), "state": "managed", "content_type": ctype, "title_id": meta.get("title_id", ""), "application_id": meta.get("application_id", ""), "version": meta.get("version", "")}


def _managed_row(managed_file):
    conn = db()
    row = conn.execute("SELECT content_type,title_id,application_id,version,source_path,state FROM packages WHERE path=?", (str(Path(managed_file)),)).fetchone()
    conn.close()
    if not row or row[5] != "managed":
        raise ValueError("File is not a managed/verified Game Content Manager copy")
    return {"content_type": row[0], "title_id": row[1] or "", "application_id": row[2] or "", "version": row[3] or "", "source_path": row[4] or "", "state": row[5]}


def ryujinx_apply_registered_update(registered_file, rom, confirmed=False):
    """Select an update already registered in Ryujinx without reinstalling it."""
    if not confirmed:
        raise PermissionError("Apply requires explicit confirmation")

    registered_file = Path(registered_file)
    if not registered_file.is_file():
        raise FileNotFoundError(registered_file)

    base_id = rom_title_id(rom)
    if not base_id:
        raise ValueError("Selected ROM filename has no 16-hex Switch application ID")

    base_id = base_id.upper()
    gd = ryujinx_game_dir(base_id)
    p = gd / "updates.json"

    data = read_json(p, {"selected": None, "paths": []})
    if not isinstance(data, dict):
        raise ValueError("Ryujinx updates.json is not a valid object")

    paths = [x for x in data.get("paths", []) if isinstance(x, str)]
    target_real = os.path.realpath(str(registered_file))

    # Only an update that Ryujinx has already registered for this exact
    # game may be selected by this operation.
    registered_path = None
    for existing in paths:
        if os.path.realpath(existing) == target_real:
            registered_path = existing
            break

    if registered_path is None:
        raise ValueError(
            "Selected update is not already registered in Ryujinx for this game"
        )

    old_selected = data.get("selected")

    if (
        isinstance(old_selected, str)
        and os.path.realpath(old_selected) == target_real
    ):
        return {
            "applied": False,
            "already_active": True,
            "emulator": "ryujinx",
            "content_type": "update",
            "config": str(p),
            "selected": registered_path,
        }

    # Preserve every registered update path. Only the selected update changes.
    newdata = dict(data)
    newdata["paths"] = paths
    newdata["selected"] = registered_path

    backup = backup_config(p)
    write_json_atomic(p, newdata)

    return {
        "applied": True,
        "emulator": "ryujinx",
        "content_type": "update",
        "config": str(p),
        "backup": backup,
        "previous_selected": old_selected,
        "selected": registered_path,
        "preserved_paths": [
            x for x in paths
            if os.path.realpath(x) != target_real
        ],
    }


def _ryujinx_install_legacy(managed_file, rom, confirmed=False):
    if not confirmed:
        raise PermissionError("Install requires explicit confirmation")
    managed_file = Path(managed_file)
    if not managed_file.is_file():
        raise FileNotFoundError(managed_file)
    base_id = rom_title_id(rom)
    if not base_id:
        raise ValueError("Selected ROM filename has no 16-hex Switch application ID")
    base_id = base_id.upper()
    row = _managed_row(managed_file)
    if row["application_id"].upper() != base_id:
        raise ValueError("Managed content does not belong to the selected game")
    ctype = row["content_type"]
    gd = ryujinx_game_dir(base_id); gd.mkdir(parents=True, exist_ok=True)

    if ctype == "update":
        p = gd / "updates.json"
        data = read_json(p, {"selected": None, "paths": []})
        if not isinstance(data, dict):
            data = {"selected": None, "paths": []}
        old_selected = data.get("selected")
        paths = [x for x in data.get("paths", []) if isinstance(x, str)]
        if str(managed_file) not in paths:
            paths.append(str(managed_file))
        # Critical safety rule: preserve all prior update paths; only selection changes after confirmation.
        newdata = dict(data)
        newdata["paths"] = paths
        newdata["selected"] = str(managed_file)
        backup = backup_config(p)
        write_json_atomic(p, newdata)
        return {"installed": True, "emulator": "ryujinx", "content_type": "update", "config": str(p), "backup": backup, "previous_selected": old_selected, "selected": str(managed_file), "preserved_paths": [x for x in paths if x != str(managed_file)]}

    if ctype == "dlc":
        p = gd / "dlc.json"
        data = read_json(p, [])
        if not isinstance(data, list):
            data = []
        tid = (row["title_id"] or infer_title_id(managed_file)).upper()
        if not tid or not switch_related(base_id, tid) or not (0x1000 <= int(tid, 16) - int(base_id, 16) <= 0x1FFF):
            raise ValueError("DLC install requires a verified DLC title ID")

        # Reuse native metadata from an existing registration of the same source/title when possible.
        template = None
        for e in data:
            if not isinstance(e, dict):
                continue
            tids = []
            for n in e.get("dlc_nca_list", []) or []:
                if isinstance(n, dict) and n.get("title_id") is not None:
                    try: tids.append(f"{int(n['title_id']):016X}")
                    except Exception: pass
            if e.get("path") == row["source_path"] or tid in tids:
                template = json.loads(json.dumps(e)); break
        if template is None:
            names = pfs0_names(managed_file)
            ncas = [n for n in names if n.lower().endswith(".nca") and not n.lower().endswith(".cnmt.nca")]
            if not ncas:
                raise ValueError("Could not identify a DLC NCA in this NSP")
            template = {"path": str(managed_file), "dlc_nca_list": [{"path": "/" + Path(ncas[0]).name, "title_id": int(tid, 16), "is_enabled": True}]}
        template["path"] = str(managed_file)

        # Preserve unrelated DLC. Replace only the explicitly confirmed DLC title ID.
        kept, replaced = [], []
        for e in data:
            if not isinstance(e, dict):
                kept.append(e); continue
            tids = []
            for n in e.get("dlc_nca_list", []) or []:
                if isinstance(n, dict) and n.get("title_id") is not None:
                    try: tids.append(f"{int(n['title_id']):016X}")
                    except Exception: pass
            if tid in tids:
                replaced.append(e.get("path", ""))
            else:
                kept.append(e)
        newdata = kept + [template]
        backup = backup_config(p)
        write_json_atomic(p, newdata)
        return {"installed": True, "emulator": "ryujinx", "content_type": "dlc", "config": str(p), "backup": backup, "title_id": tid, "replaced_same_title": replaced, "preserved_dlc_count": len(kept)}

    raise ValueError("Only verified update/DLC packages can be installed")


def list_packages(system=None, rom=None, state=None):
    conn = db()
    q = "SELECT path,system,title_id,application_id,content_type,version,size,sha256,state,source_path,metadata_source,last_seen FROM packages"
    args, wh = [], []
    if system: wh.append("system=?"); args.append(system)
    if state: wh.append("state=?"); args.append(state)
    if rom and system == "switch":
        tid = rom_title_id(rom)
        if tid:
            wh.append("application_id=?")
            args.append(tid.upper())
        else:
            # Never return every indexed package for an unknown game.
            wh.append("1=0")
    if wh: q += " WHERE " + " AND ".join(wh)
    rows = conn.execute(q, args).fetchall(); conn.close()
    keys = ["path", "system", "title_id", "application_id", "content_type", "version", "size", "sha256", "state", "source_path", "metadata_source", "last_seen"]
    return [dict(zip(keys, r)) for r in rows]


def _source_preference(path):
    """Prefer local/persistent fast storage without deleting any alternate source."""
    p = str(path or "")
    if p.startswith("/userdata/system/game-content/"):
        return (0, p)
    if p.startswith("/userdata/"):
        return (1, p)
    if p.startswith("/media/nvme") or p.startswith("/mnt/nvme"):
        return (2, p)
    if p.startswith("/media/") or p.startswith("/mnt/"):
        return (3, p)
    return (4, p)


def _content_identity(row, base_id=""):
    """Identity for exact package versions used to collapse duplicate source copies."""
    ctype = (row.get("content_type") or "unknown").lower()
    tid = (row.get("title_id") or "").upper()
    appid = (row.get("application_id") or base_id or "").upper()
    version = str(row.get("version") or "").strip()
    size = int(row.get("size") or 0)

    if ctype == "update":
        # Update title ID is normally stable across versions; version is what
        # differentiates releases.  Size is a safe fallback when version is absent.
        return ("update", appid, tid, version or f"size:{size}")
    if ctype == "dlc":
        # One DLC title may be revised, so preserve version in the exact identity.
        return ("dlc", appid, tid, version or f"size:{size}")
    return (ctype, appid, tid, version or f"size:{size}")



# GCM BATCH V2 BACKEND
def ryujinx_set_dlc_enabled(registered_files, rom, enabled, confirmed=False):
    if not confirmed:
        raise PermissionError("DLC state change requires explicit confirmation")

    base_id = rom_title_id(rom)
    if not base_id:
        raise ValueError(
            "Selected ROM filename has no 16-hex Switch application ID"
        )

    base_id = base_id.upper()
    gd = ryujinx_game_dir(base_id)
    p = gd / "dlc.json"

    data = read_json(p, [])
    if not isinstance(data, list):
        raise ValueError("Ryujinx dlc.json is not a valid list")

    targets = {
        os.path.realpath(str(Path(x)))
        for x in registered_files
    }

    if not targets:
        raise ValueError("No DLC files were selected")

    newdata = json.loads(json.dumps(data))
    matched = set()
    changed_entries = 0

    for entry in newdata:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue

        rp = os.path.realpath(str(entry["path"]))
        if rp not in targets:
            continue

        matched.add(rp)
        entry_changed = False

        for nca in entry.get("dlc_nca_list", []) or []:
            if not isinstance(nca, dict):
                continue

            old = bool(nca.get("is_enabled", True))

            if old != bool(enabled):
                nca["is_enabled"] = bool(enabled)
                entry_changed = True

        if entry_changed:
            changed_entries += 1

    missing = targets - matched

    if missing:
        raise ValueError(
            "Selected DLC is not registered in Ryujinx for this game: "
            + ", ".join(sorted(missing))
        )

    backup = None

    if changed_entries:
        backup = backup_config(p)
        write_json_atomic(p, newdata)

    return {
        "changed": bool(changed_entries),
        "enabled": bool(enabled),
        "matched_entries": len(matched),
        "changed_entries": changed_entries,
        "config": str(p),
        "backup": backup,
    }


def ryujinx_reset_all(rom, confirmed=False):
    if not confirmed:
        raise PermissionError("Reset requires explicit confirmation")

    base_id = rom_title_id(rom)

    if not base_id:
        raise ValueError(
            "Selected ROM filename has no 16-hex Switch application ID"
        )

    base_id = base_id.upper()
    gd = ryujinx_game_dir(base_id)

    updates_path = gd / "updates.json"
    dlc_path = gd / "dlc.json"

    updates_old = read_json(
        updates_path,
        {"selected": None, "paths": []},
    )

    dlc_old = read_json(
        dlc_path,
        [],
    )

    if not isinstance(updates_old, dict):
        raise ValueError("Ryujinx updates.json is not a valid object")

    if not isinstance(dlc_old, list):
        raise ValueError("Ryujinx dlc.json is not a valid list")

    updates_new = json.loads(json.dumps(updates_old))
    dlc_new = json.loads(json.dumps(dlc_old))

    previous_selected = updates_new.get("selected")
    updates_changed = previous_selected is not None

    # RESET never removes registered update paths.
    updates_new["selected"] = None

    disabled_entries = 0

    for entry in dlc_new:
        if not isinstance(entry, dict):
            continue

        entry_changed = False

        for nca in entry.get("dlc_nca_list", []) or []:
            if not isinstance(nca, dict):
                continue

            if bool(nca.get("is_enabled", True)):
                nca["is_enabled"] = False
                entry_changed = True

        if entry_changed:
            disabled_entries += 1

    updates_backup = (
        backup_config(updates_path)
        if updates_changed and updates_path.exists()
        else None
    )

    dlc_backup = (
        backup_config(dlc_path)
        if disabled_entries and dlc_path.exists()
        else None
    )

    wrote_updates = False
    wrote_dlc = False

    try:
        if updates_changed:
            write_json_atomic(
                updates_path,
                updates_new,
            )
            wrote_updates = True

        if disabled_entries:
            write_json_atomic(
                dlc_path,
                dlc_new,
            )
            wrote_dlc = True

    except Exception:
        # Restore the original configuration if either write fails.
        if wrote_updates:
            write_json_atomic(
                updates_path,
                updates_old,
            )

        if wrote_dlc:
            write_json_atomic(
                dlc_path,
                dlc_old,
            )

        raise

    return {
        "reset": True,
        "application_id": base_id,
        "previous_selected_update": previous_selected,
        "update_unapplied": bool(updates_changed),
        "dlc_entries_disabled": disabled_entries,
        "updates_backup": updates_backup,
        "dlc_backup": dlc_backup,
        "files_preserved": True,
        "registrations_preserved": True,
    }


def ryujinx_install(managed_file, rom, confirmed=False):
    """
    INSTALL registers content only.

    Updates:
      - preserve current selected update
      - add this update to paths
      - APPLY is the only operation that can activate it

    DLC:
      - reuse the proven Ryujinx registration implementation
      - force the newly registered DLC into disabled state
      - ENABLE/DISABLE control activation separately
    """

    if not confirmed:
        raise PermissionError("Install requires explicit confirmation")

    managed_file = Path(managed_file)

    if not managed_file.is_file():
        raise FileNotFoundError(managed_file)

    base_id = rom_title_id(rom)

    if not base_id:
        raise ValueError(
            "Selected ROM filename has no 16-hex Switch application ID"
        )

    base_id = base_id.upper()

    row = _managed_row(managed_file)

    if row["application_id"].upper() != base_id:
        raise ValueError(
            "Managed content does not belong to the selected game"
        )

    if row["content_type"] == "update":
        gd = ryujinx_game_dir(base_id)
        gd.mkdir(parents=True, exist_ok=True)

        p = gd / "updates.json"

        data = read_json(
            p,
            {"selected": None, "paths": []},
        )

        if not isinstance(data, dict):
            data = {
                "selected": None,
                "paths": [],
            }

        old_selected = data.get("selected")

        paths = [
            x
            for x in data.get("paths", [])
            if isinstance(x, str)
        ]

        target_real = os.path.realpath(
            str(managed_file)
        )

        registered_path = next(
            (
                x
                for x in paths
                if os.path.realpath(x) == target_real
            ),
            None,
        )

        if registered_path is None:
            registered_path = str(managed_file)
            paths.append(registered_path)

        newdata = dict(data)
        newdata["paths"] = paths

        # Critical rule:
        # INSTALL does not APPLY an update.
        newdata["selected"] = old_selected

        backup = backup_config(p)
        write_json_atomic(
            p,
            newdata,
        )

        return {
            "installed": True,
            "registered": True,
            "active": bool(
                old_selected
                and os.path.realpath(
                    str(old_selected)
                ) == target_real
            ),
            "emulator": "ryujinx",
            "content_type": "update",
            "config": str(p),
            "backup": backup,
            "selected_unchanged": old_selected,
            "registered_path": registered_path,
            "preserved_paths": [
                x
                for x in paths
                if os.path.realpath(x) != target_real
            ],
        }

    if row["content_type"] == "dlc":
        # Keep the already-proven DLC registration logic.
        result = _ryujinx_install_legacy(
            managed_file,
            rom,
            confirmed=True,
        )

        # INSTALL does not ENABLE DLC.
        state_result = ryujinx_set_dlc_enabled(
            [str(managed_file)],
            rom,
            enabled=False,
            confirmed=True,
        )

        result["enabled"] = False
        result["activation"] = "disabled_until_enabled"
        result["dlc_state"] = state_result

        return result

    raise ValueError(
        "Only verified update/DLC packages can be installed"
    )



def _same_installed_content(installed, candidate, base_id=""):
    """Return True only when candidate is the same installed revision, not merely related."""
    if (installed.get("content_type") or "") != (candidate.get("content_type") or ""):
        return False
    ctype = installed.get("content_type")
    iv = str(installed.get("version") or "").strip()
    cv = str(candidate.get("version") or "").strip()
    itid = (installed.get("title_id") or "").upper()
    ctid = (candidate.get("title_id") or "").upper()

    if ctype == "update":
        # Ryujinx can register an update whose filename does not include its
        # title ID (as on the user's current Mario Kart setup). Version equality
        # is therefore the strongest exact-match signal available here.
        if iv and cv:
            return iv == cv
        return bool(itid and ctid and itid == ctid and int(installed.get("size") or 0) == int(candidate.get("size") or 0))

    if ctype == "dlc":
        if not itid or not ctid or itid != ctid:
            return False
        if iv and cv:
            return iv == cv
        return int(installed.get("size") or 0) == int(candidate.get("size") or 0)

    return _content_identity(installed, base_id) == _content_identity(candidate, base_id)


def _group_packages(rows, base_id=""):
    groups = {}
    for row in rows:
        key = _content_identity(row, base_id)
        g = groups.setdefault(key, [])
        g.append(dict(row))
    return groups


def _preferred_row(rows):
    return min(rows, key=lambda r: _source_preference(r.get("path") or r.get("source_path")))


def _source_descriptors(rows):
    out = []
    seen = set()
    for r in sorted(rows, key=lambda x: _source_preference(x.get("path") or x.get("source_path"))):
        path = r.get("path") or r.get("source_path") or ""
        rp = os.path.realpath(path) if path else ""
        if not path or rp in seen:
            continue
        seen.add(rp)
        out.append({
            "path": path,
            "size": int(r.get("size") or 0),
            "state": r.get("state", "external"),
            "metadata_source": r.get("metadata_source", ""),
        })
    return out


def game_status(system, rom, emulator="ryujinx", do_scan=False):
    base_id = rom_title_id(rom) if system == "switch" else ""

    # An unknown Switch identity must never expose another game's
    # installed, managed, or external update/DLC packages.
    if system == "switch" and not base_id:
        return {
            "system": system,
            "rom": rom,
            "application_id": "",
            "emulator": emulator,
            "rows": [],
            "identity_unresolved": True,
        }

    scan_summary = None
    if do_scan:
        _, scan_summary = scan(system, base_id, rom)

    installed_state = ryujinx_installed_state(base_id) if emulator == "ryujinx" and system == "switch" else {"updates": [], "dlc": []}
    installed = installed_state.get("updates", []) + installed_state.get("dlc", [])
    for x in installed:
        x.setdefault("application_id", base_id.upper() if base_id else "")
        try:
            x["size"] = Path(x["path"]).stat().st_size
        except Exception:
            x["size"] = int(x.get("size") or 0)

    managed = list_packages(system, rom, "managed")
    external = list_packages(system, rom, "external")

    # Eliminate literal path aliases first. Logical duplicate grouping happens below.
    installed_real = {os.path.realpath(x["path"]) for x in installed if x.get("path")}
    managed_real = {os.path.realpath(x["path"]) for x in managed if x.get("path")}
    external = [x for x in external if os.path.realpath(x.get("path", "")) not in installed_real and os.path.realpath(x.get("path", "")) not in managed_real]

    managed_groups = _group_packages(managed, base_id)
    external_groups = _group_packages(external, base_id)
    consumed_managed = set()
    consumed_external = set()
    rows = []

    # Installed state is authoritative. Exact duplicate copies elsewhere are
    # annotations/backups only and never become Transfer/Install actions.
    for inst in installed:
        y = dict(inst)
        y["state"] = "installed"

        if inst.get("content_type") == "update":
            if inst.get("active"):
                y["action"] = "none"
                y["label"] = "Currently Active"
            else:
                y["action"] = "apply"
                y["label"] = "Ready to Apply"
        else:
            y["action"] = "none"
            y["enabled"] = bool(inst.get("active"))
            y["label"] = "Enabled" if inst.get("active") else "Disabled"
        backups = []
        managed_copies = []

        for key, grp in external_groups.items():
            if key in consumed_external:
                continue
            if grp and _same_installed_content(inst, grp[0], base_id):
                backups.extend(grp)
                consumed_external.add(key)
        for key, grp in managed_groups.items():
            if key in consumed_managed:
                continue
            if grp and _same_installed_content(inst, grp[0], base_id):
                managed_copies.extend(grp)
                consumed_managed.add(key)

        backup_sources = _source_descriptors(backups)
        managed_sources = _source_descriptors(managed_copies)

        # If the emulator's installed/selected path is itself the GCM-managed
        # package, managed_groups contains that same physical file.  It is the
        # installed primary source, not an additional managed copy.  Exclude
        # only that exact real path while retaining any genuinely separate
        # managed duplicate files.
        installed_rp = os.path.realpath(inst.get("path", "")) if inst.get("path") else ""
        managed_sources = [
            src for src in managed_sources
            if not installed_rp or os.path.realpath(src.get("path", "")) != installed_rp
        ]

        y["backup_sources"] = backup_sources
        y["backup_count"] = len(backup_sources)
        y["managed_copies"] = managed_sources
        y["managed_copy_count"] = len(managed_sources)
        y["source_count"] = 1 + len(backup_sources) + len(managed_sources)
        rows.append(y)

    # Managed copies are already safely inside GCM. Collapse duplicates and offer
    # one Install action. Any matching external files are backup source annotations.
    for key, grp in managed_groups.items():
        if key in consumed_managed:
            continue
        preferred = _preferred_row(grp)
        y = dict(preferred)
        y["state"] = "managed"
        y["action"] = "install"
        y["label"] = "Ready to Install"
        managed_sources = _source_descriptors(grp)
        y["managed_sources"] = managed_sources
        y["managed_copy_count"] = len(managed_sources)

        ext_grp = external_groups.get(key, [])
        if ext_grp:
            consumed_external.add(key)
        backup_sources = _source_descriptors(ext_grp)
        y["backup_sources"] = backup_sources
        y["backup_count"] = len(backup_sources)
        y["source_count"] = len(managed_sources) + len(backup_sources)
        rows.append(y)

    # Remaining external packages are grouped by exact content revision. Only
    # the preferred source path is actionable; every other copy stays preserved.
    for key, grp in external_groups.items():
        if key in consumed_external:
            continue
        preferred = _preferred_row(grp)
        y = dict(preferred)
        sources = _source_descriptors(grp)
        y["state"] = "external"
        y["action"] = "transfer"
        y["label"] = "External Backup" if len(sources) == 1 else f"{len(sources)} source copies found"
        y["sources"] = sources
        y["source_count"] = len(sources)
        y["preferred_source"] = preferred.get("path", "")
        # `path` remains the CLI/UI action target for backward compatibility.
        y["path"] = preferred.get("path", "")
        rows.append(y)

    order = {"installed": 0, "managed": 1, "external": 2}
    rows.sort(key=lambda r: (
        order.get(r.get("state"), 9),
        0 if r.get("content_type") == "update" else 1,
        0 if r.get("active") else 1,
        r.get("version", ""),
        r.get("title_id", ""),
        r.get("path", ""),
    ))

    if isinstance(scan_summary, dict):
        scan_summary["logical_results"] = len(rows)
        scan_summary["source_copies_collapsed"] = sum(max(0, int(r.get("source_count") or 1) - 1) for r in rows)

    out = {
        "system": system,
        "rom": rom,
        "application_id": base_id.upper() if base_id else "",
        "emulator": emulator,
        "rows": rows,
    }
    if scan_summary is not None:
        out["scan_summary"] = scan_summary
    return out

def diagnostics():
    return {
        "app_root": str(APP_ROOT), "data_root": str(DATA_ROOT),
        "ryujinx_root": str(RYUJINX_ROOT), "ryujinx_exists": RYUJINX_ROOT.exists(),
        "switch_keys_dir": str(SWITCH_KEYS_DIR),
        "prod_keys_present": (SWITCH_KEYS_DIR / "prod.keys").is_file(),
        "title_keys_present": (SWITCH_KEYS_DIR / "title.keys").is_file(),
        "scan_roots": [str(x) for x in candidate_roots()],
        "scan_mounts": [{"path": str(x["path"]), "device": x.get("device", ""), "fs_type": x.get("fs_type", ""), "source": x.get("source", "")} for x in candidate_mounts()],
        "scan_engine": "staged-fast-roots-v1",
        "fast_scan_roots": {s: [str(r) for m in fast_scan_roots(s) for r in m.get("fast_roots", [])] for s in SCAN_PROFILES},
        "deep_index_state": {s: deep_index_status(s) for s in SCAN_PROFILES},
        "scan_profiles": {k: {
            "extensions": sorted(v.get("extensions", set())),
            "optional_extensions": sorted(v.get("optional_extensions", set())),
            "marker_names": sorted(v.get("marker_names", set())),
            "description": v.get("description", ""),
        } for k, v in SCAN_PROFILES.items()},
    }


def main():
    ap = argparse.ArgumentParser(prog="gamecontentctl")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("scan"); p.add_argument("--system", required=True, choices=sorted(SCAN_PROFILES)); p.add_argument("--title-id"); p.add_argument("--rom")
    p = sp.add_parser("deep-index"); p.add_argument("--system", required=True, choices=sorted(SCAN_PROFILES)); p.add_argument("--max-seconds", type=int, default=0); p.add_argument("--force", action="store_true")
    p = sp.add_parser("deep-index-status"); p.add_argument("--system", required=True, choices=sorted(SCAN_PROFILES))
    p = sp.add_parser("launch-deep-index"); p.add_argument("--system", required=True, choices=sorted(SCAN_PROFILES))
    sp.add_parser("scan-profiles")
    p = sp.add_parser("status"); p.add_argument("--system", required=True); p.add_argument("--rom", required=True); p.add_argument("--emulator", default="ryujinx", choices=["ryujinx"]); p.add_argument("--scan", action="store_true")
    p = sp.add_parser("transfer"); p.add_argument("file"); p.add_argument("--system", required=True); p.add_argument("--rom", required=True); p.add_argument("--type", choices=["update", "dlc"])
    p = sp.add_parser("install"); p.add_argument("file"); p.add_argument("--emulator", choices=["ryujinx"], required=True); p.add_argument("--rom", required=True); p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("apply"); p.add_argument("file"); p.add_argument("--emulator", choices=["ryujinx"], required=True); p.add_argument("--rom", required=True); p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("dlc-state")
    p.add_argument("files", nargs="+")
    p.add_argument("--rom", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("reset")
    p.add_argument("--rom", required=True)
    p.add_argument("--emulator", choices=["ryujinx"], required=True)
    p.add_argument("--confirm", action="store_true")
    p = sp.add_parser("list"); p.add_argument("--system"); p.add_argument("--rom"); p.add_argument("--state", choices=["external", "managed"])
    sp.add_parser("diagnostics")
    a = ap.parse_args()
    try:
        if a.cmd == "scan":
            rows, summary = scan(a.system, a.title_id, a.rom)
            result = {"rows": rows, "scan_summary": summary}
        elif a.cmd == "deep-index": result = deep_index(a.system, a.max_seconds, a.force)
        elif a.cmd == "deep-index-status": result = deep_index_status(a.system)
        elif a.cmd == "launch-deep-index": result = launch_deep_index(a.system)
        elif a.cmd == "scan-profiles":
            result = diagnostics()["scan_profiles"]
        elif a.cmd == "status": result = game_status(a.system, a.rom, a.emulator, a.scan)
        elif a.cmd == "transfer": result = import_file(a.file, a.system, a.rom, a.type)
        elif a.cmd == "install": result = ryujinx_install(a.file, a.rom, a.confirm)
        elif a.cmd == "apply": result = ryujinx_apply_registered_update(a.file, a.rom, a.confirm)
        elif a.cmd == "dlc-state": result = ryujinx_set_dlc_enabled(a.files, a.rom, a.enable, a.confirm)
        elif a.cmd == "reset": result = ryujinx_reset_all(a.rom, a.confirm)
        elif a.cmd == "diagnostics": result = diagnostics()
        else: result = list_packages(a.system, a.rom, a.state)
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
