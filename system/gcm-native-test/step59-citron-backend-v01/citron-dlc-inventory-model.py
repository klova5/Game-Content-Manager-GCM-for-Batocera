
"""Normalize verified Citron DLC evidence into one row per content title."""
import re

def title(value):
    if type(value) is not str or not re.fullmatch(r"[0-9A-Fa-f]{16}", value):
        raise ValueError("Invalid title ID")
    return value.upper()

def build_dlc_rows(application_id, evidence, disabled):
    application_id = title(application_id)
    if type(evidence) is not list or len(evidence) > 4096:
        raise ValueError("Invalid evidence collection")
    if type(disabled) is not list or any(type(x) is not str for x in disabled):
        raise ValueError("Invalid disabled keys")

    grouped = {}
    keys = {}
    for item in evidence:
        if type(item) is not dict:
            raise ValueError("Invalid evidence record")
        if title(item["application_id"]) != application_id:
            raise ValueError("Evidence belongs to another game")
        tid = title(item["title_id"])
        if item.get("content_type") != "dlc":
            raise ValueError("Expected DLC evidence")
        if item.get("content_hashes_verified") is not True:
            raise ValueError("Unverified content cannot enter available inventory")
        origin = item.get("origin")
        if origin not in ("UserNAND", "SDMC", "GCM_MANAGED", "GCM_PUBLISHED"):
            raise ValueError("Unknown content origin")
        version = item.get("internal_version")
        if type(version) is not int or not 0 <= version < 2**32:
            raise ValueError("Invalid internal version")

        key = f"DLC {int(tid, 16) & 0x7FF:04d}"
        if key in keys and keys[key] != tid:
            raise ValueError("Ambiguous DLC selection keys")
        keys[key] = tid

        row = grouped.setdefault(tid, {
            "title_id": tid,
            "application_id": application_id,
            "selection_key": key,
            "versions": set(),
            "origins": set(),
            "paths": set(),
        })
        row["versions"].add(version)
        row["origins"].add(origin)
        path = item.get("path")
        if type(path) is not str or not path.startswith("/") or "\0" in path:
            raise ValueError("Invalid evidence path")
        row["paths"].add(path)

    rows = []
    for tid, item in sorted(grouped.items()):
        blocked = "DLC" in disabled or item["selection_key"] in disabled
        registered_files = bool(item["origins"] & {"UserNAND", "SDMC"})
        rows.append({
            "title_id": tid,
            "application_id": application_id,
            "selection_key": item["selection_key"],
            "versions": sorted(item["versions"]),
            "origins": sorted(item["origins"]),
            "paths": sorted(item["paths"]),
            "registered_files_verified": registered_files,
            "configured_enabled": not blocked,
            "in_game_availability": "UNKNOWN",
        })
    return rows
