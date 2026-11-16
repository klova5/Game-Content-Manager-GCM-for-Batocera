"""Prepare verified PS4 DLC files inside a dedicated extraction sandbox."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-native-sandbox.py":
        "df71d668d1c5426a03f5f6821b4e9af93dc457efddbbfc667bcc7580f597355e",
    "gcm-ps4-dlc-runtime-manifest-measured-v01-menu-evidence-v01.py":
        "ca701e79f54c62110d2624818381c3207a3820b631ae918216b9ea944a884b71",
}


# Optional measured telemetry; original content checks remain authoritative.
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
        raise ValueError("DLC preparation dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Symlink preparation path rejected")
    return path


def _preparation_lifecycle():
    import importlib.util as _ilu
    _path = Path(__file__).resolve().parent / 'gcm-preparation-lifecycle-v01.py'
    if _path.is_symlink() or hashlib.sha256(_path.read_bytes()).hexdigest() != '7fcc2819de7760fd1a72cdb4043323b4bba2aa70c2a1f8d930ddaf9ffd6d1399':
        raise ValueError('Preparation lifecycle dependency changed')
    _spec = _ilu.spec_from_file_location('gcm_preparation_lifecycle', _path)
    _module = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    return _module

def prepare(base, source, work):
    base = safe(base).resolve(strict=True)
    source = safe(source).resolve(strict=True)
    work = safe(work).resolve(strict=True)
    if (
        work.parent != ROOT
        or not work.name.startswith("step61-ps4-dlc-preparation-")
        or not work.is_dir()
        or any(work.iterdir())
        or source.is_relative_to(work)
        or base.is_relative_to(work)
    ):
        raise ValueError("Empty dedicated DLC preparation directory required")
    progress = _gcm_progress()
    load("gcm-native-sandbox.py").isolate(work)
    progress.publish("CHECKING PACKAGE", source.stem)
    runtime = load("gcm-ps4-dlc-runtime-manifest-measured-v01-menu-evidence-v01.py")
    service = runtime.load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py")
    proof = service.verify_game_content(base, source, "ps4")
    if proof["content_type"] != "dlc":
        raise ValueError("Game-matching DLC required")

    with runtime.load("gcm-ps4-pfs-reader-measured-v01.py").Reader(source) as reader:
        manifest = runtime.from_reader(reader)
        if manifest["package_sha256"] != proof["sha256"]:
            raise ValueError("DLC source evidence disagrees")
        required = sum(
            entry["size"] for entry in manifest["entries"]
            if entry["kind"] == "file")
        publication = safe(source.parent.parent)
        same_filesystem = work.stat().st_dev == publication.stat().st_dev
        peak = _preparation_lifecycle().preparation_peak(required, same_filesystem)
        if (shutil.disk_usage(work).free < peak
            or (not same_filesystem and shutil.disk_usage(publication).free
                < _preparation_lifecycle().preparation_peak(required, False))):
            raise ValueError("Insufficient DLC preparation space")
        output = work / "output"
        output.mkdir(mode=0o700)
        directories = sorted(
            (entry["path"] for entry in manifest["entries"]
             if entry["kind"] == "directory"),
            key=lambda name: (name.count("/"), name))
        for relative in directories:
            (output / relative).mkdir(mode=0o700)

        system = runtime.load("gcm-ps4-system-manifest-v02.py")
        files = []
        extracted = 0
        progress.publish("EXTRACTING", source.stem, 0, required)
        for entry in manifest["entries"]:
            if entry["kind"] != "file":
                continue
            target = output / entry["path"]
            fd = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600)
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as stream:
                if entry["origin"] == "SYSTEM_ENTRY":
                    data, encrypted = runtime.decode_system(
                        reader, entry["entry_id"], system.decode)
                    if len(data) != entry["size"]:
                        raise ValueError("Decoded DLC entry size disagrees")
                    stream.write(data)
                    digest.update(data)
                    extracted += len(data)
                    progress.publish("EXTRACTING", source.stem, extracted, required)
                elif entry["origin"] == "PFS":
                    remaining = entry["size"]
                    offset = entry["logical_offset"]
                    while remaining:
                        count = min(1024 * 1024, remaining)
                        data = reader.read(offset, count)
                        if len(data) != count:
                            raise ValueError("Short DLC filesystem read")
                        stream.write(data)
                        digest.update(data)
                        extracted += len(data)
                        progress.publish("EXTRACTING", source.stem, extracted, required)
                        offset += count
                        remaining -= count
                else:
                    raise ValueError("Unsupported DLC file origin")
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError("Extracted DLC stream hash disagrees")
            readback = hashlib.sha256()
            readback_done = 0
            progress.publish("CHECKING EXTRACTED FILE", entry["path"], 0, entry["size"])
            with target.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    readback.update(block)
                    readback_done += len(block)
                    progress.publish("CHECKING EXTRACTED FILE", entry["path"],
                                     readback_done, entry["size"])
            if (
                target.stat().st_size != entry["size"]
                or readback.hexdigest() != entry["sha256"]
            ):
                raise ValueError("Extracted DLC read-back disagrees")
            files.append(entry["path"])
            progress.publish("EXTRACTING", source.stem, extracted, required)
        observed = {
            item.relative_to(output).as_posix()
            for item in output.rglob("*")
        }
        expected = {entry["path"] for entry in manifest["entries"]}
        if observed != expected:
            raise ValueError("Prepared DLC tree differs from manifest")
        reader.unchanged()

    if service.verify_game_content(base, source, "ps4") != proof:
        raise ValueError("Game or DLC changed during preparation")
    report = {
        "status": "DLC_PREPARATION_FILES_VERIFIED",
        "source": str(source),
        "output": str(output),
        "game_evidence": proof,
        "manifest": manifest,
        "files": len(files),
        "bytes": required,
        "enabled": False,
        "in_game_availability": "UNVERIFIED",
    }
    with (work / "report.json").open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    for directory in [*sorted(
        (item for item in output.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts), reverse=True), output, work]:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return report
