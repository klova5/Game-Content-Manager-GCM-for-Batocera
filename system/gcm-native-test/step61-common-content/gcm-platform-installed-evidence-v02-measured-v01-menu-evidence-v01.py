"""Game-bound PS3 and supported PS4 update file evidence."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-installed-evidence-measured-v01.py":
        "847635726ab9d25d7086092da84aac6a7fc781ce31a92fb18a18be77f91310d9",
    "gcm-sony-content-relationship-measured-v01.py":
        "0c006d1a583cbae533845554af8fb54797bfe3f437017701e155bb32fc4747fa",
    "gcm-ps4-installed-integrity-measured-v01.py":
        "b292dc2ab9d6fbcdab1c4a80847c030695ff3ef9d1b380d8f7673f5cc8952ee5",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
}


def _gcm_menu_receipts():
    if '_GCM_MENU_RECEIPTS' not in globals():
        path = ROOT / 'gcm-global-menu-file-evidence.py'
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != 'a2b6d031d580ce3aff78d63b977178ff12e98ecab76e1d157907b10282cbd1e3':
            raise ValueError('Shared menu evidence dependency changed')
        spec = importlib.util.spec_from_file_location('global_menu_receipts', path)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        globals()['_GCM_MENU_RECEIPTS'] = module
    return globals()['_GCM_MENU_RECEIPTS']

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
        return load("gcm-platform-installed-evidence-measured-v01.py").verify(
            base, source, installed, system, content_type,
            allow_unrelated=allow_unrelated)
    if system != "ps4" or content_type != "update":
        raise ValueError("Installed-evidence adapter unavailable for this scope")

    relationship = load("gcm-sony-content-relationship-measured-v01.py")
    integrity = load("gcm-ps4-installed-integrity-measured-v01.py")
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
    if not allow_unrelated:
        _gcm_menu_receipts().publish(
            base, source, proof['installed_root'], proof['source_manifest']['package_sha256'],
            'update', matched['application_id'], proof['source_manifest'],
            {item['relative_path']: item['stat_identity'] for item in proof['verified_files']},
            matched['base_stat_identity'], matched['package_stat_identity'])
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
