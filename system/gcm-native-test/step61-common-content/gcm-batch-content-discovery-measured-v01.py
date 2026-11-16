"""Batch discovery using existing scan profiles; no index writes or verification."""
import hashlib
import importlib.util
import os
from pathlib import Path

CONTROLLER = Path("/userdata/system/add-ons/game-content-manager/gamecontentctl-measured-v01.py")
CONTROLLER_SHA = "c9c2658d6490f78735a08dbeb42577c38fb0c7cdcbc4cc6c52677e73d2c8fca4"
SYSTEMS = ("ps3", "ps4", "xbox360", "wiiu", "3ds", "psvita", "wii")

def controller():
    if CONTROLLER.is_symlink() or hashlib.sha256(
            CONTROLLER.read_bytes()).hexdigest() != CONTROLLER_SHA:
        raise ValueError("Shared scanner dependency changed")
    spec = importlib.util.spec_from_file_location("batch_discovery_native", CONTROLLER)
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    # This module instance only; original scanner code is never modified.
    native.SCAN_PROFILES = dict(native.SCAN_PROFILES)
    native.SCAN_PROFILES["wii"] = {
        "extensions": {".wad"}, "optional_extensions": set(),
        "marker_names": set(), "description": "Wii WAD containers; type unresolved",
    }
    return native

def identity(value):
    return [value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns]

def discover(systems=None):
    requested = list(SYSTEMS if systems is None else systems)
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("Unique system scopes required")
    if any(system not in SYSTEMS for system in requested):
        raise ValueError("Unknown batch system scope")
    native = controller()
    report = {
        "schema": "GCM_BATCH_CONTENT_DISCOVERY_V1",
        "scope": "EXISTING_FAST_SCAN_ROOTS",
        "complete_storage_inventory": False,
        "systems": {},
        "index_changed": False,
        "installation_performed": False,
    }
    for system in requested:
        print("DISCOVER:", system, flush=True)
        profile = native.scan_profile(system)
        mounts, discovered, root_stats, seconds = native._fast_discovery(system, profile)
        candidates = []
        problems = []
        seen = set()
        for raw in discovered:
            path = Path(raw)
            try:
                if not native.is_profile_candidate(path, system, profile):
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                before = path.stat()
                key = (before.st_dev, before.st_ino)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "path": str(path.absolute()),
                    "canonical_path": os.path.realpath(path),
                    "stat_identity": identity(before),
                    "state": "DETECTED",
                    "content_type": "UNRESOLVED",
                    "filename_hint": native.classify_name(path),
                    "game_relationship_verified": False,
                    "package_integrity_verified": False,
                })
                if identity(path.stat()) != identity(before):
                    raise ValueError("Candidate changed during discovery")
            except Exception as exc:
                problems.append({"path": str(path), "error": str(exc)[:240]})
        candidates.sort(key=lambda item: item["path"])
        report["systems"][system] = {
            "candidates": candidates, "problems": problems,
            "mounts_considered": len(mounts),
            "discovery_seconds": seconds,
        }
        print("FOUND:", system, len(candidates),
              "PROBLEMS:", len(problems), flush=True)
    for scope in report["systems"].values():
        for item in scope["candidates"]:
            path = Path(item["path"])
            if (path.is_symlink()
                or os.path.realpath(path) != item["canonical_path"]
                or identity(path.stat()) != item["stat_identity"]):
                raise ValueError("Candidate changed before batch completion")
    return report
