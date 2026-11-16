"""Common candidates with separately assessed PS3 installed-file evidence."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-candidate-inventory-measured-v01.py":
        "78ece636b567fa0cf576c3232bee26dbbfd1eb1ebe95df7eb52c64d6b35ec31c",
    "gcm-ps3-content-relationship-measured-v01.py":
        "76d677c8cfbf874599db32aae3d346ee43c04f4f9a5344e2bdd4f0d090833b19",
    "gcm-platform-installed-evidence-measured-v01.py":
        "847635726ab9d25d7086092da84aac6a7fc781ce31a92fb18a18be77f91310d9",
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
        return load("gcm-platform-candidate-inventory-measured-v01.py").query(
            base, sources, system, content_type)
    if system != "ps3" or content_type != "update":
        raise ValueError("Inventory adapter unavailable for this scope")

    relationship = load("gcm-ps3-content-relationship-measured-v01.py")
    service = load("gcm-platform-installed-evidence-measured-v01.py")
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
