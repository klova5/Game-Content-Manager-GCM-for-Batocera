"""Install supported managed PS4 updates into the existing merged layout."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tomllib
import uuid

ROOT = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/shadps4/user/config.toml")
PINS = {
    "gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py":
        "0fc79563dca135d396b1a9c19e25193c7e85ecdfa6c65ab1497329830d2239fa",
    "gcm-ps4-file-transaction-v02.py":
        "3001f5df3be1afaaae5e6bde45a3a7bcb418eea6dc9f38e494dd3a2218e45766",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Installation dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def idle():
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            executable = os.readlink(process / "exe")
        except FileNotFoundError:
            continue
        except PermissionError:
            raise ValueError("Cannot assess running emulator processes")
        if "shadps4" in Path(executable).name.lower():
            raise ValueError("Close shadPS4 before installing content")

def configuration(transaction):
    transaction.safe(CONFIG)
    before = CONFIG.stat()
    data = CONFIG.read_bytes()
    after = CONFIG.stat()
    fields = lambda st: (st.st_dev, st.st_ino, st.st_size,
                         st.st_mtime_ns, st.st_ctime_ns)
    if fields(before) != fields(after):
        raise ValueError("Emulator configuration changed while reading")
    parsed = tomllib.loads(data.decode())
    settings = parsed.get("GUI", {})
    if parsed.get("General", {}).get("separateUpdateEnabled") is not False:
        raise ValueError("This adapter requires the existing merged-update layout")
    directories = settings.get("installDirs")
    if not isinstance(directories, list) or "/userdata/roms/ps4" not in directories:
        raise ValueError("Configured PS4 game directory differs from adapter")
    return hashlib.sha256(data).hexdigest()

def preview(base, source, prepared):
    transaction = load("gcm-ps4-file-transaction-v02.py")
    service = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")
    sfo = load("gcm-sfo-metadata.py")
    base = transaction.safe(Path(base).absolute()).resolve(strict=True)
    target = transaction.checked_target(base.parent.parent)
    if base != target / "sce_sys/param.sfo":
        raise ValueError("Internal target PARAM.SFO required")
    source = transaction.safe(Path(source).absolute()).resolve(strict=True)
    if source.parent != Path("/userdata/system/game-content/ps4") / target.name / "updates":
        raise ValueError("Managed game-specific update package required")
    prepared = transaction.safe(Path(prepared).absolute()).resolve(strict=True)
    if (
        prepared.name != "output"
        or prepared.parent.parent != ROOT.resolve()
        or not prepared.parent.name.startswith("step61-ps4-extraction-")
    ):
        raise ValueError("Dedicated prepared extraction required")
    idle()
    config_hash = configuration(transaction)
    base_metadata = sfo.read(base)
    payload = service.verify_game_content(base, source, "ps4")
    if payload["content_type"] != "update":
        raise ValueError("Internally classified game-matching update required")
    proof = service.verify_installed(
        base, source, prepared, "ps4", "update", allow_unrelated=False)
    if proof["application_id"] != target.name:
        raise ValueError("Target and verified update identity disagree")
    if proof["installed_evidence"]["source_manifest"]["package_sha256"] != payload["sha256"]:
        raise ValueError("Prepared extraction and verified package disagree")
    operations = []
    for record in proof["installed_evidence"]["source_manifest"]["entries"]:
        destination = transaction.safe(target / record["path"])
        if record["kind"] == "directory":
            if not destination.is_dir():
                raise ValueError("New target directories require separate support: "
                                 + record["path"])
            continue
        if not destination.parent.is_dir():
            raise ValueError("Existing target parent required")
        existing = transaction.read(destination) if destination.exists() else None
        expected = {"size": record["size"], "sha256": record["sha256"]}
        if existing is not None and transaction.same(existing, expected):
            continue
        operations.append({
            "path": record["path"], **expected, "expected_before": existing,
        })
    if sfo.read(base) != base_metadata or configuration(transaction) != config_hash:
        raise ValueError("Game or configuration changed during preview")
    idle()
    return {
        "status": "PREVIEW",
        "application_id": proof["application_id"],
        "version": proof["installed_version"],
        "base": str(base), "source": str(source),
        "prepared": str(prepared), "target": str(target),
        "package_sha256": payload["sha256"],
        "base_metadata": base_metadata,
        "configuration_sha256": config_hash,
        "operations": operations,
        "unrelated_file_policy": "PRESERVE",
        "activation_assessed": False,
    }

def install(base, source, prepared):
    transaction = load("gcm-ps4-file-transaction-v02.py")
    service = load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")
    sfo = load("gcm-sfo-metadata.py")
    plan = preview(base, source, prepared)
    work = ROOT / ("step61-ps4-transaction-install-" + uuid.uuid4().hex)
    work.mkdir(mode=0o700)
    staged = work / "staged"
    staged.mkdir()
    requests = []
    for number, operation in enumerate(plan["operations"]):
        destination = staged / str(number)
        shutil.copyfile(
            transaction.safe(Path(plan["prepared"]) / operation["path"]), destination)
        if not transaction.same(transaction.read(destination), operation):
            raise ValueError("Staged installation file differs from preview")
        requests.append({
            "target": "game/" + operation["path"],
            "staged": "staged/" + str(number),
            "size": operation["size"], "sha256": operation["sha256"],
            "expected_before": operation["expected_before"],
        })
    with (work / "plan.json").open("x") as stream:
        json.dump(plan, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    transaction.sync(work)
    if (
        sfo.read(plan["base"]) != plan["base_metadata"]
        or configuration(transaction) != plan["configuration_sha256"]
    ):
        raise ValueError("Game or configuration changed before installation")
    idle()
    try:
        if requests:
            transaction.apply(work, requests, target_root=plan["target"])
        proof = service.verify_installed(
            plan["base"], plan["source"], plan["target"],
            "ps4", "update", allow_unrelated=True)
        if configuration(transaction) != plan["configuration_sha256"]:
            raise ValueError("Emulator configuration changed during installation")
    except BaseException:
        if (work / "journal.json").exists():
            transaction.rollback(work, target_root=plan["target"])
        raise
    result = {
        "status": "INSTALLED_FILES_VERIFIED",
        "application_id": plan["application_id"],
        "version": plan["version"],
        "transaction_directory": str(work),
        "files_changed": len(requests),
        "installed_evidence": proof,
        "source_preserved": True,
        "configuration_changed": False,
        "activation_assessed": False,
        "in_game_availability": "UNVERIFIED",
    }
    with (work / "result.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    transaction.sync(work)
    return result
