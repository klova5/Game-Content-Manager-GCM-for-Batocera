"""Verify PS4 output files against a freshly decoded package manifest."""
import hashlib
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-pfs-reader-measured-v01.py":
        "e370b9ada6579776a554079c953ab9028a68f4ef9db36e4f12fae373434b9648",
    "gcm-ps4-combined-manifest-measured-v01.py":
        "8e6ca451abf22dd1a612e85475b1bfaf35a9568326acdd6e863355bfc183ea45",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Installed-integrity dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def safe(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError("Symlink in installed output path")
    return path

def verify(source, installed, allow_unrelated=False):
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")
    installed = safe(Path(installed).absolute()).resolve(strict=True)
    if not installed.is_dir():
        raise ValueError("Installed output directory required")
    access = load("gcm-ps4-pfs-reader-measured-v01.py")
    combined = load("gcm-ps4-combined-manifest-measured-v01.py")
    with access.Reader(source) as reader:
        manifest = combined.manifest(reader)
        expected = {item["path"]: item for item in manifest["entries"]}
        observed = set()
        for directory, dirs, files in os.walk(
                installed, followlinks=False,
                onerror=lambda error: (_ for _ in ()).throw(error)):
            for name in dirs + files:
                target = safe(Path(directory) / name)
                observed.add(target.relative_to(installed).as_posix())
        if set(expected) - observed:
            raise ValueError("Referenced installed paths missing")
        extras = observed - set(expected)
        if extras and not allow_unrelated:
            raise ValueError("Unexpected paths in isolated output")
        verified = []
        for relative, item in sorted(expected.items()):
            target = safe(installed / relative)
            if item["kind"] == "directory":
                if not target.is_dir():
                    raise ValueError("Referenced directory missing: " + relative)
                continue
            if not target.is_file():
                raise ValueError("Regular installed file required: " + relative)
            before = identity(target.stat())
            if before[2] != item["size"]:
                raise ValueError("Installed file size mismatch: " + relative)
            digest = hashlib.sha256()
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                if identity(os.fstat(stream.fileno())) != before:
                    raise ValueError("Opened installed file changed")
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                if identity(os.fstat(stream.fileno())) != before:
                    raise ValueError("Installed file changed while reading")
            if (
                digest.hexdigest() != item["sha256"]
                or identity(safe(target).stat()) != before
            ):
                raise ValueError("Installed file hash or identity mismatch: " + relative)
            verified.append({
                "relative_path": relative, "stat_identity": before,
                "sha256": item["sha256"],
            })
        for item in verified:
            target = safe(installed / item["relative_path"])
            if identity(target.stat()) != item["stat_identity"]:
                raise ValueError("Installed files changed before completion")
        reader.unchanged()
        return {
            "status": "SOURCE_BOUND_INSTALLED_FILES_VERIFIED",
            "installed_root": str(installed),
            "source_manifest": manifest,
            "verified_files": verified,
            "unrelated_paths_preserved": sorted(extras),
            "game_relationship_verified": False,
            "installation_authorized": False,
            "in_game_availability": "UNVERIFIED",
        }
