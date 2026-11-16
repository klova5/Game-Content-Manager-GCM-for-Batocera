"""Verify PS3 installed files against a fresh decrypted source manifest."""
import hashlib
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Installed-integrity dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def verify(source, installed, allow_unrelated=False):
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")
    entries = load("gcm-ps3-entry-manifest-v02.py",
        "b1114458c734597ac3cbc3d22c44c1ed4b9a03272ef7a50f9a9d48c5d5bac8fc")
    sfo = load("gcm-sfo-metadata.py",
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41")
    manifest = entries.manifest(source)
    if manifest["content_type"] != "update":
        raise ValueError("Internally classified PS3 update required")
    installed = Path(installed).absolute()
    for path in (installed, *installed.parents):
        if path.is_symlink():
            raise ValueError("Symlink in installed title path")
    installed = installed.resolve(strict=True)
    if not installed.is_dir() or installed.name != manifest["title_id"]:
        raise ValueError("Installed directory does not match package title")
    metadata = sfo.read(installed / "PARAM.SFO")
    if (metadata["fields"].get("TITLE_ID") != manifest["title_id"]
            or metadata["fields"].get("CATEGORY") != "GD"):
        raise ValueError("Installed metadata differs from PS3 update identity")
    expected = {item["path"] for item in manifest["entries"] if item["kind"] == "file"}
    observed = set()
    for directory, dirs, files in os.walk(installed, followlinks=False):
        for name in dirs + files:
            if (Path(directory) / name).is_symlink():
                raise ValueError("Symlink in installed content tree")
        observed.update(
            (Path(directory) / name).relative_to(installed).as_posix()
            for name in files)
    if expected - observed:
        raise ValueError("Referenced installed files missing")
    if not allow_unrelated and observed - expected:
        raise ValueError("Unexpected files in isolated extraction")
    verified = []
    for record in manifest["entries"]:
        path = installed / record["path"]
        if record["kind"] == "directory":
            if not path.is_dir():
                raise ValueError("Referenced installed directory missing")
            continue
        if not path.is_file():
            raise ValueError("Regular installed file required")
        before = identity(path.stat())
        if before[2] != record["size"]:
            raise ValueError("Installed file size mismatch: " + record["path"])
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if identity(os.fstat(stream.fileno())) != before:
                raise ValueError("Opened installed file changed")
            remaining = record["size"]
            while remaining:
                block = stream.read(min(8 * 1024 * 1024, remaining))
                if not block:
                    raise ValueError("Installed file truncated")
                digest.update(block)
                remaining -= len(block)
            if stream.read(1):
                raise ValueError("Installed file grew")
        if digest.hexdigest() != record["sha256"] or identity(path.stat()) != before:
            raise ValueError("Installed file hash or identity mismatch: " + record["path"])
        verified.append({
            "path": str(path), "relative_path": record["path"],
            "stat_identity": before, "sha256": record["sha256"],
        })
    if sfo.read(installed / "PARAM.SFO") != metadata:
        raise ValueError("Installed metadata changed during verification")
    if any(identity(Path(item["path"]).stat()) != item["stat_identity"] for item in verified):
        raise ValueError("Installed files changed before completion")
    source_stat = Path(source).stat()
    if identity(source_stat) != manifest["source_stat_identity"]:
        raise ValueError("Source package changed before completion")
    return {
        "status": "SOURCE_BOUND_INSTALLED_FILES_VERIFIED",
        "application_id": manifest["title_id"],
        "content_type": "update",
        "installed_version": metadata["fields"].get("APP_VER"),
        "source_manifest": manifest,
        "installed_root": str(installed),
        "verified_files": verified,
        "unrelated_files_preserved": sorted(observed - expected),
        "native_process_success_assessed": False,
        "in_game_availability": "UNVERIFIED",
        "installation_authorized": False,
    }
