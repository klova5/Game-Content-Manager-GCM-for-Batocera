"""Game-bound PS3 and supported PS4 update file evidence."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-installed-evidence.py":
        "6b0cdfe6e0caa7c24d329c513206876df39635285595294fc678c82e2349e688",
    "gcm-sony-content-relationship.py":
        "529fd5faa1459a0a9b59356381707213adad2ff396268f5e17741db2771a3442",
    "gcm-ps4-installed-integrity.py":
        "7707bfca3c955d19555e07d1956d1751c47f2677a882d061b6e8159ca09a8760",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
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
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")
    if system == "ps3":
        return load("gcm-platform-installed-evidence.py").verify(
            base, source, installed, system, content_type,
            allow_unrelated=allow_unrelated)
    if system != "ps4" or content_type != "update":
        raise ValueError("Installed-evidence adapter unavailable for this scope")

    relationship = load("gcm-sony-content-relationship.py")
    integrity = load("gcm-ps4-installed-integrity.py")
    sfo = load("gcm-sfo-metadata.py")
    started_boot = boot()
    matched = relationship.match(base, source, "ps4")
    if matched["content_type"] != content_type:
        raise ValueError("Requested content type differs from internal metadata")
    proof = integrity.verify(source, installed, allow_unrelated=allow_unrelated)
    metadata_path = Path(proof["installed_root"]) / "sce_sys/param.sfo"
    metadata = sfo.read(metadata_path)
    fields = metadata["fields"]
    if (
        proof.get("status") != "SOURCE_BOUND_INSTALLED_FILES_VERIFIED"
        or proof.get("installation_authorized") is not False
        or proof.get("in_game_availability") != "UNVERIFIED"
        or proof["source_manifest"]["stat_identity"] != matched["package_stat_identity"]
        or fields.get("TITLE_ID") != matched["application_id"]
        or fields.get("CATEGORY") != "gp"
        or fields.get("APP_VER") != matched["version"]
    ):
        raise ValueError("Installed evidence and game relationship disagree")
    if (
        relationship.match(base, source, "ps4") != matched
        or sfo.read(metadata_path) != metadata
        or boot() != started_boot
    ):
        raise ValueError("Game or installed metadata changed during verification")
    for item in proof["verified_files"]:
        target = integrity.safe(
            Path(proof["installed_root"]) / item["relative_path"])
        if integrity.identity(target.stat()) != item["stat_identity"]:
            raise ValueError("Installed files changed before evidence completion")
    return {
        "schema": "GCM_PLATFORM_INSTALLED_EVIDENCE_V1",
        "status": "GAME_BOUND_INSTALLED_FILES_VERIFIED",
        "system": system,
        "application_id": matched["application_id"],
        "content_type": content_type,
        "installed_version": fields["APP_VER"],
        "installed_root": proof["installed_root"],
        "boot_id": started_boot,
        "game_relationship_verified": True,
        "source_bound_installed_files_verified": True,
        "relationship_evidence": matched,
        "installed_evidence": proof,
        "unrelated_file_policy": "PRESERVE" if allow_unrelated else "REJECT",
        "native_process_success_assessed": False,
        "in_game_availability": "UNVERIFIED",
        "installation_authorized": False,
        "changes_performed": False,
    }
