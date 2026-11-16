"""Extract supported PS4 package files into an isolated rehearsal only."""
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
    "gcm-ps4-pfs-reader.py":
        "39a40e507935d43e8713091aebd1d56e435dc37126fa4f8e28b9910240ec940b",
    "gcm-ps4-combined-manifest.py":
        "0018d941fc096c9af0a04daeb3f200e2e4294bf503e46e83bf69db106ba34b13",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Extraction dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def rehearse(source, work):
    sandbox = load("gcm-native-sandbox.py")
    access = load("gcm-ps4-pfs-reader.py")
    combined = load("gcm-ps4-combined-manifest.py")
    system = combined.load("gcm-ps4-system-manifest-v02.py")
    work = Path(work).absolute()
    for candidate in (work, *work.parents):
        if candidate.is_symlink():
            raise ValueError("Symlink rehearsal path rejected")
    work = work.resolve(strict=True)
    if (
        work.parent != ROOT.resolve()
        or not work.name.startswith("step61-ps4-extraction-")
        or not work.is_dir()
        or any(work.iterdir())
    ):
        raise ValueError("New empty dedicated rehearsal directory required")
    source = Path(source).absolute()
    if source.resolve(strict=True).is_relative_to(work):
        raise ValueError("Package source must remain outside rehearsal")
    sandbox.isolate(work)
    os.umask(0o077)

    with access.Reader(source) as reader:
        manifest = combined.manifest(reader)
        if shutil.disk_usage(work).free < manifest["file_bytes"] + 64 * 1024 * 1024:
            raise ValueError("Insufficient rehearsal free space")
        output = work / "output"
        output.mkdir()
        directories = [item for item in manifest["entries"]
                       if item["kind"] == "directory"]
        for item in sorted(directories,
                           key=lambda item: (item["path"].count("/"), item["path"])):
            (output / item["path"]).mkdir()
        files = [item for item in manifest["entries"] if item["kind"] == "file"]
        for item in files:
            target = output / item["path"]
            digest = hashlib.sha256()
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, "wb") as stream:
                if item["origin"] == "SYSTEM_ENTRY":
                    data, encrypted = system.decode(reader, item["entry_id"])
                    if len(data) != item["size"]:
                        raise ValueError("System entry size changed")
                    stream.write(data)
                    digest.update(data)
                elif item["origin"] == "PFS":
                    offset, remaining = item["logical_offset"], item["size"]
                    while remaining:
                        size = min(1024 * 1024, remaining)
                        block = reader.read(offset, size)
                        if len(block) != size:
                            raise ValueError("Short PFS extraction read")
                        stream.write(block)
                        digest.update(block)
                        offset += size
                        remaining -= size
                else:
                    raise ValueError("Unsupported extraction origin")
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != item["sha256"]:
                raise ValueError("Extracted stream hash mismatch: " + item["path"])
        reader.unchanged()

        observed = set()
        for directory, dirs, names in os.walk(output, followlinks=False):
            for name in dirs + names:
                entry = Path(directory) / name
                if entry.is_symlink():
                    raise ValueError("Symlink in rehearsal output")
                observed.add(entry.relative_to(output).as_posix())
        if observed != {item["path"] for item in manifest["entries"]}:
            raise ValueError("Rehearsal output tree differs from manifest")
        for item in files:
            target = output / item["path"]
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            if target.stat().st_size != item["size"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("Output read-back mismatch: " + item["path"])
        reader.unchanged()

        report = {
            "status": "ISOLATED_EXTRACTION_FILES_VERIFIED",
            "source": str(source),
            "output": str(output),
            "manifest": manifest,
            "installation_performed": False,
            "activation_performed": False,
            "in_game_availability": "UNVERIFIED",
        }
        with (work / "report.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return report
