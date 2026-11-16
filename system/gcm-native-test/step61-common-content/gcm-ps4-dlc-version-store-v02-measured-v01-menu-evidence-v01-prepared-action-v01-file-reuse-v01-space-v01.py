"""Verified PS4 DLC storage, independent of enabled entitlement directories."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
MANAGED = Path("/userdata/system/game-content/ps4")
PINS = {
    "gcm-global-action-progress-measured-v01.py":
        "d51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e",
    "gcm-ps4-dlc-runtime-manifest-measured-v01-menu-evidence-v01-space-v01.py":
        "0529ad24bad1de1e9a4e84f22315424d2b1cafea7d93a39086d0e018acee09ff",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
}


# Optional measured telemetry; original content checks remain authoritative.

def _gcm_menu_receipts():
    if '_GCM_MENU_RECEIPTS' not in globals():
        path = ROOT / 'gcm-global-menu-file-evidence.py'
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != 'a2b6d031d580ce3aff78d63b977178ff12e98ecab76e1d157907b10282cbd1e3':
            raise ValueError('Shared menu evidence dependency changed')
        spec = importlib.util.spec_from_file_location('global_menu_receipts', path)
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        globals()['_GCM_MENU_RECEIPTS'] = module
    return globals()['_GCM_MENU_RECEIPTS']

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
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("DLC store dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink DLC path rejected")
    return path

def identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def verify(base, source, installed):
    _menu_base_before = identity(Path(base))
    _menu_source_before = identity(Path(source))
    progress = _gcm_progress()
    label = Path(source).stem
    progress.publish("CHECKING PREPARED EVIDENCE", label)
    runtime = load("gcm-ps4-dlc-runtime-manifest-measured-v01-menu-evidence-v01-space-v01.py")
    evidence = _prepared_actions().fetch(base, source)
    manifest = evidence["manifest"]
    installed = safe(installed).resolve(strict=True)
    if not installed.is_dir():
        raise ValueError("Installed DLC directory required")
    expected = {entry["path"] for entry in manifest["entries"]}
    observed = set()
    for directory, dirs, files in os.walk(installed, followlinks=False):
        for name in dirs + files:
            item = safe(Path(directory) / name)
            observed.add(item.relative_to(installed).as_posix())
    if observed != expected:
        raise ValueError("Installed DLC paths differ from package")
    verified = []
    total_bytes = sum(
        entry["size"] for entry in manifest["entries"]
        if entry["kind"] == "file")
    completed_bytes = 0
    progress.publish("VERIFYING FILES", label, 0, total_bytes)
    for entry in manifest["entries"]:
        item = installed / entry["path"]
        if entry["kind"] == "directory":
            if not item.is_dir():
                raise ValueError("Installed DLC directory missing")
            continue
        if not item.is_file():
            raise ValueError("Installed DLC regular file required")
        before = identity(item)
        if before[2] != entry["size"]:
            raise ValueError("Installed DLC size mismatch")
        digest = hashlib.sha256()
        with item.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if [opened.st_dev, opened.st_ino, opened.st_size,
                opened.st_mtime_ns, opened.st_ctime_ns] != before:
                raise ValueError("Installed DLC changed before reading")
            remaining = entry["size"]
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError("Installed DLC truncated")
                digest.update(block)
                remaining -= len(block)
                completed_bytes += len(block)
                progress.publish(
                    "VERIFYING FILES", label, completed_bytes, total_bytes)
            if stream.read(1):
                raise ValueError("Installed DLC grew")
        if digest.hexdigest() != entry["sha256"] or identity(item) != before:
            raise ValueError("Installed DLC hash or identity mismatch: " + entry["path"])
        verified.append((item, before))
    progress.publish("CHECKING IDENTITY", label)
    metadata = load("gcm-sfo-metadata.py").read(installed / "sce_sys/param.sfo")
    title = evidence["game_evidence"]["application_id"]
    content_id = metadata["fields"].get("CONTENT_ID", "")
    match = re.fullmatch(
        r"[A-Z]{2}[0-9]{4}-" + re.escape(title) + r"_00-([A-Z0-9_]{16})",
        content_id)
    if not match or metadata["fields"].get("TITLE_ID") != title:
        raise ValueError("DLC entitlement and game identity disagree")
    if any(identity(item) != before for item, before in verified):
        raise ValueError("Installed DLC changed before completion")
    if load("gcm-sfo-metadata.py").read(installed / "sce_sys/param.sfo") != metadata:
        raise ValueError("DLC metadata changed during verification")
    _gcm_menu_receipts().publish(
        base, source, installed, manifest['package_sha256'], 'dlc', title,
        manifest, {str(path.relative_to(installed)): before for path, before in verified},
        _menu_base_before, _menu_source_before)
    return {
        "status": "GAME_BOUND_DLC_FILES_VERIFIED",
        "application_id": title,
        "content_id": content_id,
        "entitlement_label": match.group(1),
        "installed_root": str(installed),
        "package_sha256": manifest["package_sha256"],
        "manifest": manifest,
        "in_game_availability": "UNVERIFIED",
    }

def publish(base, source, prepared):
    source = safe(source).resolve(strict=True)
    proof = verify(base, source, prepared)
    title = proof["application_id"]
    if source.parent != MANAGED / title / "dlc":
        raise ValueError("Managed game-specific DLC source required")
    directory = safe(MANAGED / title / "installed-dlc")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = proof["package_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid package digest")
    slot = safe(directory / digest)
    record_path = safe(directory / (digest + ".json"))
    lock = os.open(directory / ".lock",
                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not slot.exists():
            temporary = Path(tempfile.mkdtemp(prefix=".preparing-", dir=directory))
            output = temporary / "output"
            _gcm_progress().copy_tree(prepared, output, Path(source).stem, "INSTALLING")
            verify(base, source, output)
            for current, dirs, files in os.walk(output, topdown=False):
                for name in files:
                    with (Path(current) / name).open("rb") as stream:
                        os.fsync(stream.fileno())
                sync(current)
            os.rename(output, slot)
            sync(directory)
            temporary.rmdir()
        final = verify(base, source, slot)
        record = {
            "schema": "GCM_PS4_INSTALLED_DLC_V1",
            "application_id": title,
            "content_id": final["content_id"],
            "entitlement_label": final["entitlement_label"],
            "source": str(source),
            "package_sha256": digest,
            "installed_root": str(slot),
            "installation_state": "INSTALLED",
            "enablement_performed": False,
            "in_game_availability": "UNVERIFIED",
        }
        if record_path.exists():
            if json.loads(record_path.read_text()) != record:
                raise ValueError("Existing DLC record disagrees")
        else:
            fd, temporary_name = tempfile.mkstemp(prefix=".record-", dir=directory)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(record, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, record_path)
                sync(directory)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return record
    finally:
        os.close(lock)

def _prepared_actions():
    path=ROOT / 'gcm-prepared-dlc-action-manifest-file-reuse-v01.py'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='5def913bd58c2d81ba4e5c307ede27bf21b514e4ccecd79aedd8eb59a4db087e':
        raise ValueError('Prepared DLC action evidence dependency changed')
    spec=importlib.util.spec_from_file_location('prepared_dlc_action_evidence',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

_file_verify = verify
def verify(base,source,installed):
    helper=_prepared_actions()
    proof=helper.reuse_root(base,source,installed)
    if proof is not None:
        _gcm_progress().publish('CHECKING PREPARED FILES',Path(source).stem)
        return proof
    before=helper.binding(base,source);before_tree=helper.tree(installed)
    proof=_file_verify(base,source,installed)
    helper.publish_root(base,source,installed,proof,before,before_tree)
    return proof

def rename_verified(base,source,origin,destination):
    helper=_prepared_actions()
    helper.rename_verified(base,source,origin,destination)
    proof=helper.reuse_root(base,source,destination)
    snapshot=helper.tree(destination);bound=helper.binding(base,source)
    _gcm_menu_receipts().publish(base,source,destination,proof['package_sha256'],'dlc',
        proof['application_id'],proof['manifest'],
        {path:value[1] for path,value in snapshot.items() if path and value[0]=='file'},
        bound['base_identity'],bound['source_identity'])
