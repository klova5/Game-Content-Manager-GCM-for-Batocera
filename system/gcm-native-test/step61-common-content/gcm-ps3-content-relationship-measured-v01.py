"""PS3 package relationship from internal base and package identities."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps3-base-identity.py":
        "f8741b41b64ce50fd2d754b88893a7e1227564680e789cf0f0ed90a81971330a",
    "gcm-platform-content-engine-v08-measured-v01.py":
        "f552761612ca1cca8cc798f88d84558f51f7520612596c6036e3458d2f087720",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("PS3 relationship dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def match(rom, package):
    base_reader = load("gcm-ps3-base-identity.py")
    engine = load("gcm-platform-content-engine-v08-measured-v01.py")
    before = base_reader.identify(rom)
    candidate = engine.inspect(package, "ps3")
    if candidate["platform_identified"] is not True:
        raise ValueError("Internal PS3 package platform required")
    if candidate["content_type"] not in ("update", "dlc"):
        raise ValueError("Internally classified PS3 update or DLC required")
    if candidate["title_id"] != before["application_id"]:
        raise ValueError("Package belongs to another game, region or edition")
    if base_reader.identify(rom) != before or engine.inspect(package, "ps3") != candidate:
        raise ValueError("PS3 game or package identity changed during matching")
    return {
        "schema": "GCM_PS3_CONTENT_RELATIONSHIP_V1",
        "system": "ps3",
        "base": before["base"],
        "base_identity_evidence": before,
        "source": candidate["path"],
        "source_stat_identity": candidate["stat_identity"],
        "application_id": before["application_id"],
        "content_type": candidate["content_type"],
        "relationship_basis": "EXACT_INTERNAL_TITLE_ID",
        "game_relationship_verified": True,
        "payload_hashes_verified": False,
        "installation_authorized": False,
    }
