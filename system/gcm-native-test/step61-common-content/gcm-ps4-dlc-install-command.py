"""Prepare and install verified PS4 DLC without enabling it."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-base-identity.py":
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438",
    "gcm-ps4-dlc-version-store.py":
        "3193c40a796a00aeb1bcd4bfa25714e75e055e30f73417e55234d08109062058",
    "gcm-ps4-dlc-selection.py":
        "b7db3388b8bf030b9e458b8891bf2acdb6165c513c2d662ceaa54a46808a626b",
    "gcm-ps4-dlc-preparation.py":
        "b2ba6f70a728dc3ba000e963712315c75abb7124773cf635e372d8baaed54961",
    "gcm-platform-content-service-v03.py":
        "a17c9940e33ad8f2488523b9fec7cd576d4166008de6e9ec356b45429472cf9c",
}

def load(name):
    if name == "gcm-ps4-file-transaction-v02.py":
        # Reuse the established path validator only.
        runtime = load("gcm-platform-content-service-v03.py")
        path = ROOT / name
        expected = "3001f5df3be1afaaae5e6bde45a3a7bcb418eea6dc9f38e494dd3a2218e45766"
    else:
        path = ROOT / name
        expected = PINS[name]
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("DLC command dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    st = Path(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def idle():
    return load("gcm-ps4-dlc-selection.py").idle()

def configuration(transaction):
    return load("gcm-ps4-dlc-selection.py").configuration(
        load("gcm-ps4-dlc-version-store.py"))

def provider():
    return SimpleNamespace(
        load=load, idle=idle, configuration=configuration, install=install)

def resolve(rom, source):
    game = load("gcm-ps4-base-identity.py").identify(rom)
    store = load("gcm-ps4-dlc-version-store.py")
    source = store.safe(source).resolve(strict=True)
    if (
        not source.is_file()
        or source.parent != store.MANAGED / game["application_id"] / "dlc"
    ):
        raise ValueError("Managed game-specific DLC package required")
    base = Path(game["metadata_path"])
    if (
        base.parent.parent.parent != Path("/userdata/roms/ps4")
        or base.parent.parent.name != game["application_id"]
    ):
        raise ValueError("Existing PS4 game target required")
    return provider(), base, source

def prepare(source):
    source = Path(source)
    base = Path("/userdata/roms/ps4") / source.parent.parent.name / "sce_sys/param.sfo"
    resolve(base.parent.parent, source)
    load("gcm-ps4-dlc-preparation.py")
    work = ROOT / ("step61-ps4-dlc-preparation-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    script = """
import hashlib
import importlib.util
import sys
from pathlib import Path
path, base, source, work = map(Path, sys.argv[1:])
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != "b2ba6f70a728dc3ba000e963712315c75abb7124773cf635e372d8baaed54961":
    raise ValueError("DLC preparation adapter changed")
spec = importlib.util.spec_from_file_location("isolated_dlc_preparation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.prepare(base, source, work)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script,
         str(ROOT / "gcm-ps4-dlc-preparation.py"),
         str(base), str(source), str(work)],
        capture_output=True, text=True, timeout=3600)
    if result.returncode:
        raise ValueError("DLC preparation failed: " + (
            result.stderr or result.stdout)[-1600:])
    report = json.loads((work / "report.json").read_text())
    if report["status"] != "DLC_PREPARATION_FILES_VERIFIED":
        raise ValueError("DLC preparation did not verify")
    return Path(report["output"])

def install(base, source, prepared):
    idle()
    store = load("gcm-ps4-dlc-version-store.py")
    before = identity(source)
    record = store.publish(base, source, prepared)
    proof = store.verify(base, source, record["installed_root"])
    metadata_before = identity(base)
    files = []
    for entry in proof["manifest"]["entries"]:
        if entry["kind"] == "file":
            path = Path(record["installed_root"]) / entry["path"]
            files.append({"path": str(path), "identity": identity(path)})
    if identity(source) != before:
        raise ValueError("DLC source changed during installation")
    return {
        "status": "INSTALLED_FILES_VERIFIED",
        "installed_record": record,
        "installed_evidence": proof,
        "verified_files": files,
        "base_metadata": str(base),
        "base_metadata_identity": metadata_before,
        "managed_source_identity": before,
        "source_preserved": True,
        "configuration_changed": False,
        "activation_assessed": False,
        "enablement_performed": False,
        "in_game_availability": "UNVERIFIED",
    }
