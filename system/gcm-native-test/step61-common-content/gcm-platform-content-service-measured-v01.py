"""Shared content services; capabilities are specific to each operation."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-content-engine-v08-measured-v01.py":
        "f552761612ca1cca8cc798f88d84558f51f7520612596c6036e3458d2f087720",
    "gcm-platform-candidate-inventory-v02-measured-v01.py":
        "36e0131f1dccb998f260f377f626a931a1b311593d1f25ddd3baa1399307c193",
    "gcm-platform-installed-evidence-measured-v01.py":
        "847635726ab9d25d7086092da84aac6a7fc781ce31a92fb18a18be77f91310d9",
    "gcm-game-content-verification-measured-v01.py":
        "e12dd416cff52a6e7fda36c9e203d23429d61d3d8c3633388131d126cb2833cc",
    "gcm-platform-managed-copy-measured-v01.py":
        "6cd726f3344398dc9de4d5c8b620865316f03e335eb22b9022ceb5dd2bec26da",
    "gcm-platform-verification-batch-v02-measured-v01.py":
        "c6773f58d5569cc82b9a480f89f2b31a0d4ec043494c6508d287068593f1cfe1",
}

def load(name):
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
        "source_bound_installed_update_files": ["ps3"],
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
    return load("gcm-platform-candidate-inventory-v02-measured-v01.py").query(
        base, sources, system, content_type,
        installed_root=installed_root, allow_unrelated=allow_unrelated)

def verify_installed(base, source, installed, system, content_type,
                     allow_unrelated=False):
    return load("gcm-platform-installed-evidence-measured-v01.py").verify(
        base, source, installed, system, content_type,
        allow_unrelated=allow_unrelated)

def preview_copy(base, source, system, content_type):
    return load("gcm-platform-managed-copy-measured-v01.py").preview(
        base, source, system, content_type)

def copy_package(base, source, system, content_type):
    return load("gcm-platform-managed-copy-measured-v01.py").copy(
        base, source, system, content_type)

def batch_component():
    load("gcm-platform-verification-batch-v02-measured-v01.py")
    return ROOT / "gcm-platform-verification-batch-v02-measured-v01.py"
