"""Shared content services; capabilities are specific to each operation."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-content-engine-v08.py":
        "562aa4964bab7fd9b5efe8d03e547c7cded5bfa875876100c8f22f3121faf62a",
    "gcm-platform-candidate-inventory-v03.py":
        "e2f0bc40ba733014efa74ce75d7e7e8edddf9abdc86514a8194b61c62adf1af0",
    "gcm-platform-installed-evidence-v02.py":
        "53d1bdacf48674d858e679c485d6ceb9bf9c4225c7353e283cd14f4384a5bb3d",
    "gcm-game-content-verification.py":
        "2e3e241c270611e8846eae1bb854a5b744b91016e8448546b57c15a1fbef1add",
    "gcm-platform-managed-copy.py":
        "84e30b2803c5c7aa2c2d576031567ca66ad260142380e7138e991a97bf787f6a",
    "gcm-platform-verification-batch-v02.py":
        "666134a6803b942968f3287703d7a3f70f4d71b60453a90e9b599d36024bf68a",
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
    return load("gcm-platform-content-engine-v08.py").discover(systems)

def inspect(path, system):
    return load("gcm-platform-content-engine-v08.py").inspect(path, system)

def verify_content(path, system):
    return load("gcm-platform-content-engine-v08.py").verify_content(path, system)

def check_container(path, system):
    return load("gcm-platform-content-engine-v08.py").check_container(path, system)

def verify_game_content(base, source, system):
    if system != "ps4":
        raise ValueError("Game-bound payload adapter unavailable for this scope")
    return load("gcm-game-content-verification.py").verify(base, source, system)

def candidates(base, sources, system, content_type,
               installed_root=None, allow_unrelated=False):
    return load("gcm-platform-candidate-inventory-v03.py").query(
        base, sources, system, content_type,
        installed_root=installed_root, allow_unrelated=allow_unrelated)

def verify_installed(base, source, installed, system, content_type,
                     allow_unrelated=False):
    return load("gcm-platform-installed-evidence-v02.py").verify(
        base, source, installed, system, content_type,
        allow_unrelated=allow_unrelated)

def preview_copy(base, source, system, content_type):
    return load("gcm-platform-managed-copy.py").preview(
        base, source, system, content_type)

def copy_package(base, source, system, content_type):
    return load("gcm-platform-managed-copy.py").copy(
        base, source, system, content_type)

def batch_component():
    load("gcm-platform-verification-batch-v02.py")
    return ROOT / "gcm-platform-verification-batch-v02.py"
