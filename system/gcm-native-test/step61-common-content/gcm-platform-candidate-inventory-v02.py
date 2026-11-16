"""Common candidates with separately assessed PS3 installed-file evidence."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-candidate-inventory.py":
        "0e766dcaa1553d4353c7cf7e20aff5d6948795ba3957c877ea6b43023e390a10",
    "gcm-ps3-content-relationship.py":
        "991a853cfb929310b2c4bb629633f34ccba6d9012a8e5046ef65b031a28d3e88",
    "gcm-platform-installed-evidence.py":
        "6b0cdfe6e0caa7c24d329c513206876df39635285595294fc678c82e2349e688",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Inventory dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def query(base, sources, system, content_type,
          installed_root=None, allow_unrelated=False):
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")
    if system == "ps4":
        if installed_root is not None:
            raise ValueError("PS4 installed-evidence adapter unavailable")
        return load("gcm-platform-candidate-inventory.py").query(
            base, sources, system, content_type)
    if system != "ps3" or content_type != "update":
        raise ValueError("Inventory adapter unavailable for this scope")

    relationship = load("gcm-ps3-content-relationship.py")
    service = load("gcm-platform-installed-evidence.py")
    rows, problems, seen, matches = [], [], set(), []
    application_id = None
    for raw in sources:
        try:
            source = Path(raw)
            if source.is_symlink():
                raise ValueError("Symlink candidate rejected")
            source = source.resolve(strict=True)
            if str(source) in seen:
                continue
            seen.add(str(source))
            matched = relationship.match(base, source)
            if matched["content_type"] != content_type:
                continue
            if application_id not in (None, matched["application_id"]):
                raise ValueError("Conflicting game identity")
            application_id = matched["application_id"]
            row = {
                "path": str(source),
                "token": str(source).encode("utf-8").hex(),
                "application_id": application_id,
                "content_type": content_type,
                "section": "DETECTED",
                "verification_state": "UNVERIFIED",
                "installed_status": "NOT_ASSESSED",
                "installed_version": None,
                "installed_evidence": None,
                "in_game_availability": "UNVERIFIED",
                "installation_authorized": False,
            }
            if installed_root is not None:
                try:
                    proof = service.verify(
                        base, source, installed_root, system, content_type,
                        allow_unrelated=allow_unrelated)
                except (OSError, ValueError) as exc:
                    row["installed_status"] = "UNRESOLVED"
                    row["installed_error"] = str(exc)[:240]
                else:
                    row["installed_status"] = proof["status"]
                    row["installed_version"] = proof["installed_version"]
                    row["installed_evidence"] = proof
            rows.append(row)
            matches.append((source, matched))
        except (OSError, ValueError) as exc:
            problems.append({"path": str(raw), "error": str(exc)[:240]})

    for source, matched in matches:
        if relationship.match(base, source) != matched:
            raise ValueError("Candidate relationship changed during inventory")
    return {
        "system": system,
        "application_id": application_id,
        "content_type": content_type,
        "query_scope": "SUPPLIED_DISCOVERY_PATHS",
        "complete_storage_inventory": False,
        "rows": sorted(rows, key=lambda row: row["path"]),
        "problems": problems,
    }
