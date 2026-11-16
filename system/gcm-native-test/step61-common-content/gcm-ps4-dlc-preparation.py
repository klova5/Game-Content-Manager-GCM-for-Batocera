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
    "gcm-ps4-dlc-runtime-manifest.py":
        "aaf6e25af1f0e8ead282bf16f2127962b1f9cd5305f8b5e8898c2e8d85d64678",
}

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
    load("gcm-native-sandbox.py").isolate(work)
    runtime = load("gcm-ps4-dlc-runtime-manifest.py")
    service = runtime.load("gcm-platform-content-service-v03.py")
    proof = service.verify_game_content(base, source, "ps4")
    if proof["content_type"] != "dlc":
        raise ValueError("Game-matching DLC required")

    with runtime.load("gcm-ps4-pfs-reader.py").Reader(source) as reader:
        manifest = runtime.from_reader(reader)
        if manifest["package_sha256"] != proof["sha256"]:
            raise ValueError("DLC source evidence disagrees")
        required = sum(
            entry["size"] for entry in manifest["entries"]
            if entry["kind"] == "file")
        if shutil.disk_usage(work).free < required + 64 * 1024 * 1024:
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
                        offset += count
                        remaining -= count
                else:
                    raise ValueError("Unsupported DLC file origin")
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != entry["sha256"]:
                raise ValueError("Extracted DLC stream hash disagrees")
            readback = hashlib.sha256()
            with target.open("rb") as stream:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    readback.update(block)
            if (
                target.stat().st_size != entry["size"]
                or readback.hexdigest() != entry["sha256"]
            ):
                raise ValueError("Extracted DLC read-back disagrees")
            files.append(entry["path"])
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
