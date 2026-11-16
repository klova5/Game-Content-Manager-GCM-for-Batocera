"""Fresh game-bound verification; no copy, registration or activation."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-content-engine-v03-measured-v01.py":
        "79ab06710a523667d65869b187976bccac5f301a101bcf6e19d39cb46cd9312d",
    "gcm-sony-content-relationship-measured-v01.py":
        "0c006d1a583cbae533845554af8fb54797bfe3f437017701e155bb32fc4747fa",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Verification dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def verify(base_metadata, source, system):
    # More platforms connect here when both adapters have passed their tests.
    if system != "ps4":
        raise ValueError("Game-bound payload verification not yet connected for platform")
    base_metadata, source = Path(base_metadata), Path(source)
    if base_metadata.is_symlink() or source.is_symlink():
        raise ValueError("Regular game metadata and source required")
    base_metadata = base_metadata.resolve(strict=True)
    source = source.resolve(strict=True)
    if base_metadata.samefile(source):
        raise ValueError("Game metadata and content must differ")
    engine = load("gcm-platform-content-engine-v03-measured-v01.py")
    relationships = load("gcm-sony-content-relationship-measured-v01.py")
    before = relationships.match(base_metadata, source, system)
    payload = engine.verify_content(source, system)
    after = relationships.match(base_metadata, source, system)
    if before != after:
        raise ValueError("Game relationship changed during payload verification")
    if (
        payload.get("status") != "PAYLOAD_HASHES_VERIFIED"
        or payload.get("system") != system
        or payload.get("title_id") != before["application_id"]
        or payload.get("content_type") != before["content_type"]
        or payload.get("version") != before["version"]
        or payload.get("stat_identity") != before["package_stat_identity"]
        or before.get("game_relationship_verified") is not True
        or payload.get("installation_authorized") is not False
    ):
        raise ValueError("Payload and relationship evidence disagree")
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if payload.get("boot_id") != boot_id:
        raise ValueError("Verification boot identity changed")
    return {
        "schema": "GCM_GAME_CONTENT_PROOF_V1",
        "status": "PASS",
        "system": system,
        "base": str(base_metadata),
        "source": str(source),
        "application_id": before["application_id"],
        "title_id": payload["title_id"],
        "content_type": payload["content_type"],
        "version": payload["version"],
        "sha256": payload["sha256"],
        "source_stats": [
            before["base_stat_identity"], before["package_stat_identity"]],
        "boot_id": boot_id,
        "game_relationship_verified": True,
        "payload_hashes_verified": True,
        "installation_authorized": False,
        "changes_performed": False,
        "relationship_evidence": before,
        "payload_evidence": payload,
    }
