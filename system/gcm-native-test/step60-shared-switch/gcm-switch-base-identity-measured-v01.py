
"""Shared internal base identity for NSP and XCI containers."""
import hashlib
import importlib.util
import re
import struct
import subprocess
import tempfile
from pathlib import Path

DEV = Path("/userdata/system/gcm-native-test/v34-development")
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")

def load(path, expected, name):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Metadata dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    s = path.stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def metadata_engine():
    return load(DEV / "gcm-v35-auto-preview-measured-v01.py",
        "39f1a368e0ecbb5696f5d5eaad6859f29ce474406855d4e5a4bd94e7a99f9795",
        "shared_base_metadata")

def xci_application(rom, destination):
    rom = Path(rom).resolve(strict=True)
    before = identity(rom)
    engine = metadata_engine()
    result = subprocess.run(
        [str(engine.NSTOOL), "-k", str(engine.KEYS),
         "-t", "xci", "--fstree", str(rom)],
        capture_output=True, text=True, timeout=90)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError("XCI filesystem inspection failed")
    partition = None
    names = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"  ([^/\s]+)/", line)
        if match:
            partition = match.group(1)
        if partition == "secure":
            name = line.strip()
            if re.fullmatch(r"[0-9a-f]{32}\.cnmt\.nca", name):
                names.append(name)
    if not names or len(names) > 32 or len(names) != len(set(names)):
        raise ValueError("Missing, ambiguous or excessive secure metadata")
    applications = []
    for index, name in enumerate(names):
        folder = Path(destination) / str(index)
        folder.mkdir(parents=True)
        archive = folder / "archive"
        archive.mkdir()
        engine.run_nstool("-t", "xci", "-x",
            "/secure/" + name, str(archive) + "/", rom)
        extracted = archive / name
        if (not extracted.is_file() or extracted.is_symlink()
                or not 0 < extracted.stat().st_size <= 16 * 1024 * 1024):
            raise ValueError("Invalid extracted XCI metadata")
        data = extracted.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if name != digest[:32] + ".cnmt.nca":
            raise ValueError("XCI metadata content ID/hash mismatch")
        # A bounded metadata-only envelope lets the existing NSP inspector
        # perform its normal NCA header and CNMT checks without copying the game.
        name_bytes = name.encode("ascii") + bytes([0])
        envelope = folder / "metadata.nsp"
        envelope.write_bytes(
            struct.pack("<4sIII", b"PFS0", 1, len(name_bytes), 0)
            + struct.pack("<QQII", 0, len(data), 0, 0)
            + name_bytes + data)
        inspected = folder / "inspected"
        inspected.mkdir()
        report = engine.inspect(envelope, inspected)
        if report["cnmt"]["type"] == 0x80:
            applications.append((envelope, report))
    if len(applications) != 1:
        raise ValueError("XCI must contain one unambiguous base application")
    if identity(rom) != before:
        raise ValueError("XCI changed during metadata inspection")
    envelope, report = applications[0]
    return envelope, report

def identify(rom):
    rom = Path(rom).resolve(strict=True)
    before = identity(rom)
    if rom.suffix.lower() == ".nsp":
        packages = load(COMPONENTS / "citron-package-verify-v03.py",
            "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388",
            "shared_base_package")
        _, reader = packages.dependencies()
        result = reader.identify(rom)
        if result["content_type"] != "application":
            raise ValueError("Base application required")
    elif rom.suffix.lower() == ".xci":
        with tempfile.TemporaryDirectory(prefix="gcm-xci-base-") as folder:
            _, report = xci_application(rom, Path(folder))
            cnmt = report["cnmt"]
            result = {
                "application_id": cnmt["title_id"],
                "title_id": cnmt["title_id"],
                "content_type": "application",
                "internal_version": cnmt["version"],
                "source_size": rom.stat().st_size,
                "meta_nca_sha256": report["meta_sha256"],
            }
    else:
        raise ValueError("Base container reader supports NSP and XCI")
    if identity(rom) != before:
        raise ValueError("Base changed during identity query")
    return result
