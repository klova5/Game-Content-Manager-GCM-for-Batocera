"""Shared content services; capabilities are specific to each operation."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-content-engine-v08-measured-v01.py":
        "f552761612ca1cca8cc798f88d84558f51f7520612596c6036e3458d2f087720",
    "gcm-platform-candidate-inventory-v03-measured-v01-menu-evidence-v01.py":
        "2c47775057b8a11df2e1447b95ce646472fb0deec222eef0ecdd6d7206af3e40",
    "gcm-platform-installed-evidence-v02-measured-v01-menu-evidence-v01.py":
        "f9e92c448b7bad85de220903569726f0e8e1d186487f142b6016b3cfb73a18cd",
    "gcm-game-content-verification-measured-v01.py":
        "e12dd416cff52a6e7fda36c9e203d23429d61d3d8c3633388131d126cb2833cc",
    "gcm-platform-managed-copy-measured-v01-space-v01.py":
        "f2ea670e9536260077b93f6e14a06d2f45633b975a43cbf79d5963328964f987",
    "gcm-platform-verification-batch-v02-measured-v01.py":
        "c6773f58d5569cc82b9a480f89f2b31a0d4ec043494c6508d287068593f1cfe1",
}

_GCM_DECLARED_CODE_NAMES = {'gcm-platform-managed-copy-measured-v01.py': 'gcm-platform-managed-copy-measured-v01-space-v01.py'}

def load(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Shared service dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def capabilities():
    return {
        "schema": "GCM_PLATFORM_SERVICE_CAPABILITIES_V1",
        "discovery": ["ps3", "ps4", "xbox360", "wiiu", "3ds", "psvita", "wii"],
        "payload_verification": {
            "ps4": "SUPPORTED_FORMAT_SUBSET",
            "wii": "SUPPORTED_WAD_SUBSET",
            "3ds": "COMPLETE_UNENCRYPTED_CIA_SYNTHETIC_TESTED",
        },
        "game_bound_payload_verification": ["ps4"],
        "background_payload_verification": ["ps4"],
        "source_bound_installed_update_files": ["ps3", "ps4"],
        "managed_package_copy": ["ps4"],
        "native_installation": [],
        "activation": [],
        "in_game_availability_assessment": [],
        "complete_storage_inventory": False,
    }

def check_dependencies():
    for name in PINS:
        load(name)
    return {"status": "PASS", "capabilities": capabilities()}

def discover(systems=None):
    return load("gcm-platform-content-engine-v08-measured-v01.py").discover(systems)

def inspect(path, system):
    return load("gcm-platform-content-engine-v08-measured-v01.py").inspect(path, system)

def verify_content(path, system):
    return load("gcm-platform-content-engine-v08-measured-v01.py").verify_content(path, system)

def check_container(path, system):
    return load("gcm-platform-content-engine-v08-measured-v01.py").check_container(path, system)

def verify_game_content(base, source, system):
    if system != "ps4":
        raise ValueError("Game-bound payload adapter unavailable for this scope")
    return load("gcm-game-content-verification-measured-v01.py").verify(base, source, system)

def candidates(base, sources, system, content_type,
               installed_root=None, allow_unrelated=False):
    return load("gcm-platform-candidate-inventory-v03-measured-v01-menu-evidence-v01.py").query(
        base, sources, system, content_type,
        installed_root=installed_root, allow_unrelated=allow_unrelated)

def verify_installed(base, source, installed, system, content_type,
                     allow_unrelated=False):
    return load("gcm-platform-installed-evidence-v02-measured-v01-menu-evidence-v01.py").verify(
        base, source, installed, system, content_type,
        allow_unrelated=allow_unrelated)

def preview_copy(base, source, system, content_type):
    return load("gcm-platform-managed-copy-measured-v01-space-v01.py").preview(
        base, source, system, content_type)

def copy_package(base, source, system, content_type):
    return load("gcm-platform-managed-copy-measured-v01-space-v01.py").copy(
        base, source, system, content_type)

def batch_component():
    load("gcm-platform-verification-batch-v02-measured-v01.py")
    return ROOT / "gcm-platform-verification-batch-v02-measured-v01.py"
