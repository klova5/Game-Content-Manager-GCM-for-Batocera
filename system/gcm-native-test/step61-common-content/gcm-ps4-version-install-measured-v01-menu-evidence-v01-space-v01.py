"""Install PS4 updates into a verified version store without selecting them."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-update-install-v02-measured-v01-menu-evidence-v01-space-v01.py":
        "b635d0c4de75639c5518a855754512469227ca824fbf163efedddd7391c84a16",
    "gcm-ps4-update-version-store-measured-v01-menu-evidence-v01-space-v01.py":
        "7f6e6ac883d680cc08f851a08c382f83b73b080b4cae3c2034af155189976bcc",
}

def dependency(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Version installation dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load(name):
    return dependency("gcm-ps4-update-install-v02-measured-v01-menu-evidence-v01-space-v01.py").load(name)

def idle():
    return dependency("gcm-ps4-update-install-v02-measured-v01-menu-evidence-v01-space-v01.py").idle()

def configuration(transaction):
    # Retain the current configuration guard while changing the write destination.
    return dependency("gcm-ps4-update-install-v02-measured-v01-menu-evidence-v01-space-v01.py").configuration(transaction)

def checked(base, source, prepared):
    store = dependency("gcm-ps4-update-version-store-measured-v01-menu-evidence-v01-space-v01.py")
    base = store.safe(base).resolve(strict=True)
    identity = store.load("gcm-ps4-base-identity.py").identify(base.parent.parent)
    if Path(identity["metadata_path"]).resolve(strict=True) != base:
        raise ValueError("Base metadata and game target disagree")
    source = store.safe(source).resolve(strict=True)
    expected = store.MANAGED / identity["application_id"] / "updates"
    if source.parent != expected:
        raise ValueError("Managed game-specific update required")
    service = store.load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")
    proof = service.verify_installed(
        base, source, prepared, "ps4", "update", allow_unrelated=False)
    return store, base.parent.parent, source, proof

def preview(base, source, prepared):
    store, rom, source, proof = checked(base, source, prepared)
    return {
        "status": "PREVIEW",
        "application_id": proof["application_id"],
        "version": proof["installed_version"],
        "source": str(source),
        "destination_policy": "SEPARATE_INSTALLED_VERSION",
        "base_game_files_changed": False,
        "selection_performed": False,
        "in_game_availability": "UNVERIFIED",
    }

def install(base, source, prepared):
    idle()
    store, rom, source, proof = checked(base, source, prepared)
    record = store.publish(rom, source, prepared)
    verified = store.verify(rom, record)
    return {
        "status": "INSTALLED_FILES_VERIFIED",
        "application_id": record["application_id"],
        "version": record["version"],
        "installed_record": record,
        "installed_evidence": verified,
        "source_preserved": True,
        "configuration_changed": False,
        "activation_assessed": False,
        "selection_performed": False,
        "base_game_files_changed": False,
        "in_game_availability": "UNVERIFIED",
    }
