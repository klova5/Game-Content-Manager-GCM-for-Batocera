"""Game-bound installed-file evidence; activation remains separate."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps3-content-relationship-measured-v01.py":
        "76d677c8cfbf874599db32aae3d346ee43c04f4f9a5344e2bdd4f0d090833b19",
    "gcm-ps3-installed-integrity-v02.py":
        "f381a992c80e1cb417e4c6a25cc06ca3fbd2b2b5ca70a6711d81ddfff7aa977c",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Installed-evidence dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def verify(base, source, installed, system, content_type,
           allow_unrelated=False):
    if system != "ps3" or content_type != "update":
        raise ValueError("Installed-evidence adapter unavailable for this scope")
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")

    relationship = load("gcm-ps3-content-relationship-measured-v01.py")
    integrity = load("gcm-ps3-installed-integrity-v02.py")
    started_boot = boot()
    matched = relationship.match(base, source)
    if matched["content_type"] != content_type:
        raise ValueError("Requested content type differs from internal metadata")

    proof = integrity.verify(source, installed,
                             allow_unrelated=allow_unrelated)
    if (
        proof.get("status") != "SOURCE_BOUND_INSTALLED_FILES_VERIFIED"
        or proof.get("application_id") != matched["application_id"]
        or proof.get("content_type") != content_type
        or proof.get("installation_authorized") is not False
        or proof.get("in_game_availability") != "UNVERIFIED"
    ):
        raise ValueError("Installed evidence and game relationship disagree")

    current = relationship.match(base, source)
    if current != matched or boot() != started_boot:
        raise ValueError("Game relationship changed during installed verification")

    return {
        "schema": "GCM_PLATFORM_INSTALLED_EVIDENCE_V1",
        "status": "GAME_BOUND_INSTALLED_FILES_VERIFIED",
        "system": system,
        "application_id": matched["application_id"],
        "content_type": content_type,
        "installed_version": proof["installed_version"],
        "installed_root": proof["installed_root"],
        "boot_id": started_boot,
        "game_relationship_verified": True,
        "source_bound_installed_files_verified": True,
        "relationship_evidence": matched,
        "installed_evidence": proof,
        "unrelated_file_policy": (
            "PRESERVE" if allow_unrelated else "REJECT"
        ),
        "native_process_success_assessed": False,
        "in_game_availability": "UNVERIFIED",
        "installation_authorized": False,
        "changes_performed": False,
    }
