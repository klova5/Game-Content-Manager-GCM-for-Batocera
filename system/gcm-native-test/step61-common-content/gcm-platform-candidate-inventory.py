"""Current Sony candidates; verification and installed state stay separate."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-sony-content-relationship.py":
        "529fd5faa1459a0a9b59356381707213adad2ff396268f5e17741db2771a3442",
    "gcm-platform-verification-store.py":
        "851961927b00fe9166ee2bb28c7d583a552a032c05fd2c125549e4ebd031deaa",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Inventory dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def query(base_metadata, sources, system, content_type):
    if system not in ("ps3", "ps4") or content_type not in ("update", "dlc"):
        raise ValueError("Unsupported inventory scope")
    relationship = load("gcm-sony-content-relationship.py")
    store = load("gcm-platform-verification-store.py")
    base_metadata = Path(base_metadata).resolve(strict=True)
    reader = relationship.load("gcm-sfo-metadata.py")
    before = reader.read(base_metadata)
    rows, problems, seen = [], [], set()
    for raw in sources:
        try:
            source = Path(raw)
            if source.is_symlink():
                raise ValueError("Symlink candidate rejected")
            source = source.resolve(strict=True)
            if str(source) in seen:
                continue
            seen.add(str(source))
            matched = relationship.match(base_metadata, source, system)
            if matched["content_type"] != content_type:
                continue
            proof = store.lookup(base_metadata, source, content_type, system)
            verified = proof is not None
            if verified and (
                proof["application_id"] != matched["application_id"]
                or proof["version"] != matched["version"]
                or proof["source_stats"] != [
                    matched["base_stat_identity"], matched["package_stat_identity"]]
            ):
                raise ValueError("Stored proof and candidate disagree")
            rows.append({
                "path": str(source),
                "token": str(source).encode("utf-8").hex(),
                "application_id": matched["application_id"],
                "content_type": content_type,
                "version": matched["version"],
                "section": "AVAILABLE" if verified else "DETECTED",
                "verification_state": "VERIFIED_EXTERNAL" if verified else "UNVERIFIED",
                "sha256": proof["sha256"] if verified else None,
                "stat_identity": matched["package_stat_identity"],
                "installed_status": "NOT_ASSESSED",
                "installation_authorized": False,
            })
        except (OSError, ValueError) as exc:
            problems.append({"path": str(raw), "error": str(exc)[:240]})
    if reader.read(base_metadata) != before:
        raise ValueError("Game metadata changed during inventory query")
    for row in rows:
        if store.identity(row["path"]) != row["stat_identity"]:
            raise ValueError("Candidate changed before inventory completed")
    return {
        "system": system,
        "application_id": before["fields"]["TITLE_ID"],
        "content_type": content_type,
        "query_scope": "SUPPLIED_DISCOVERY_PATHS",
        "complete_storage_inventory": False,
        "rows": sorted(rows, key=lambda row: row["path"]),
        "problems": problems,
    }
