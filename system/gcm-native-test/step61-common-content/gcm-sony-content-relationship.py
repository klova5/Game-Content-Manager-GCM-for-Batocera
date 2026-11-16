"""Match Sony content to internal game metadata; no installation operations."""
import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
    "gcm-platform-content-engine-v03.py":
        "7582f355b1750da2d27ae79de2f9ec9c2976dda080502c98f492eaf2fff417f0",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Relationship dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def match(base_sfo, package, system):
    if system not in ("ps3", "ps4"):
        raise ValueError("Sony relationship scope required")
    reader = load("gcm-sfo-metadata.py")
    engine = load("gcm-platform-content-engine-v03.py")
    base = reader.read(base_sfo)
    title = base["fields"].get("TITLE_ID", "")
    pattern = r"[A-Z]{4}[0-9]{5}" if system == "ps3" else r"CUSA[0-9]{5}"
    if not isinstance(title, str) or not re.fullmatch(pattern, title):
        raise ValueError("Internal game title ID does not match platform")
    candidate = engine.inspect(package, system)
    if candidate["platform_identified"] is not True:
        raise ValueError("Internal package platform required")
    if candidate["content_type"] not in ("update", "dlc"):
        raise ValueError("Internally classified update or DLC required")
    if candidate["title_id"] != title:
        raise ValueError("Package belongs to another game or region")
    if reader.read(base_sfo) != base or engine.inspect(package, system) != candidate:
        raise ValueError("Game or package metadata changed during matching")
    return {
        "schema": "GCM_SONY_CONTENT_RELATIONSHIP_V1",
        "system": system,
        "application_id": title,
        "content_type": candidate["content_type"],
        "version": candidate["version"],
        "base_metadata_path": base["path"],
        "base_metadata_sha256": base["metadata_sha256"],
        "base_stat_identity": base["stat_identity"],
        "package_path": candidate["path"],
        "package_stat_identity": candidate["stat_identity"],
        "relationship_basis": "EXACT_INTERNAL_TITLE_ID",
        "game_relationship_verified": True,
        "package_integrity_verified": False,
        "installation_authorized": False,
    }
