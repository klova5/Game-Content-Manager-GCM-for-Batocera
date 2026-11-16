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
    "gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py":
        "1e1ecab1dcb632431f8ec702bb9797c97c6a138968b12168c85d17363532cc9f",
    "gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py":
        "7801a5317d1b42df7fa23d3f602583f3071a1f57773645202508b6044f252f32",
    "gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01-lookup-v01.py":
        "81bea8efb8ce5a61cd99ab2b725f89acabaa9ebb4d9c506be6e623ae6707d8cd",
    "gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py":
        "2b9ab6a2bdad2e235e82de9dccc8f692dfc5d183dc056b6b8206ac102ba2d79b",
}


# Optional measured telemetry; original content checks remain authoritative.
_GCM_DECLARED_CODE_NAMES = {'gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01.py': 'gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py', 'gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01.py': 'gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py', 'gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01.py': 'gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py', 'gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01.py': 'gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py', 'gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01.py': 'gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01-lookup-v01.py', 'gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01.py': 'gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01-lookup-v01.py', 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01.py': 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py', 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py': 'gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py'}

def _gcm_progress():
    import hashlib as _hashlib
    import importlib.util as _importlib
    from pathlib import Path as _Path
    if "_GCM_PROGRESS_MODULE" not in globals():
        _path = _Path('/userdata/system/gcm-native-test/step61-common-content/gcm-global-action-progress-measured-v01.py')
        if _path.is_symlink() or _hashlib.sha256(_path.read_bytes()).hexdigest() != 'd51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
            raise ValueError("Measured progress dependency changed")
        _spec = _importlib.spec_from_file_location("gcm_measured_progress", _path)
        _module = _importlib.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        globals()["_GCM_PROGRESS_MODULE"] = _module
    return globals()["_GCM_PROGRESS_MODULE"]

def load(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    if name == "gcm-ps4-file-transaction-v02.py":
        # Reuse the established path validator only.
        runtime = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py")
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
    return load("gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py").idle()

def configuration(transaction):
    return load("gcm-ps4-dlc-selection-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py").configuration(
        load("gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py"))

def provider():
    return SimpleNamespace(
        load=load, idle=idle, configuration=configuration, install=install)

def resolve(rom, source):
    game = load("gcm-ps4-base-identity.py").identify(rom)
    store = load("gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py")
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
    load("gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01-lookup-v01.py")
    work = ROOT / ("step61-ps4-dlc-preparation-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    script = """
import hashlib
import importlib.util
import sys
from pathlib import Path
path, base, source, work = map(Path, sys.argv[1:])
if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != "81bea8efb8ce5a61cd99ab2b725f89acabaa9ebb4d9c506be6e623ae6707d8cd":
    raise ValueError("DLC preparation adapter changed")
spec = importlib.util.spec_from_file_location("isolated_dlc_preparation", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.prepare(base, source, work)
"""
    result = _gcm_progress().run_preparation(
        [sys.executable, "-B", "-c", script,
         str(ROOT / "gcm-ps4-dlc-preparation-measured-v01-menu-evidence-v01-lifecycle-v01-space-v01-lookup-v01.py"),
         str(base), str(source), str(work)],
        work, timeout=3600)
    if result.returncode:
        raise ValueError("DLC preparation failed: " + (
            result.stderr or result.stdout)[-1600:])
    report = json.loads((work / "report.json").read_text())
    if report["status"] != "DLC_PREPARATION_FILES_VERIFIED":
        raise ValueError("DLC preparation did not verify")
    return Path(report["output"])

def install(base, source, prepared):
    idle()
    store = load("gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01-lookup-v01.py")
    before = identity(source)
    with store.verification_session(base, source):
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
