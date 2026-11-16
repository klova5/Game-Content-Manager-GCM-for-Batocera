"""Bounded XML and ZIP-contained SFO metadata; no extraction or installation."""
import hashlib
import importlib.util
import os
import re
import stat
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
MAX_XML = 1024 * 1024

def parser():
    path = ROOT / "gcm-sfo-metadata.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41":
        raise ValueError("Shared SFO reader changed")
    spec = importlib.util.spec_from_file_location("archive_sfo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def xml_fields(data):
    if not 0 < len(data) <= MAX_XML:
        raise ValueError("XML metadata outside bounds")
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("XML declarations are not supported")
    tree = ET.fromstring(data)
    result = {}
    for name in ("title_id", "title_version", "longname_en", "shortname_en"):
        nodes = tree.findall(".//" + name)
        if len(nodes) > 1:
            raise ValueError("Duplicate XML identity field")
        if nodes:
            result[name] = (nodes[0].text or "").strip()
    if not re.fullmatch("[0-9a-fA-F]{16}", result.get("title_id", "")):
        raise ValueError("Internal Wii U title ID required")
    result["title_id"] = result["title_id"].upper()
    return result

def read_xml(path):
    shared = parser()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular XML metadata required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= MAX_XML:
            raise ValueError("XML metadata outside bounds")
        data = stream.read(MAX_XML + 1)
        fields = xml_fields(data)
        if shared.identity(before) != shared.identity(os.fstat(stream.fileno())):
            raise ValueError("XML changed during reading")
    if shared.identity(before) != shared.identity(path.stat()):
        raise ValueError("XML changed before completion")
    return {
        "path": str(path.absolute()), "fields": fields,
        "stat_identity": shared.identity(before),
        "metadata_sha256": hashlib.sha256(data).hexdigest(),
        "package_integrity_verified": False,
        "game_relationship_verified": False,
    }

def read_zip_sfo(path):
    shared = parser()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Regular ZIP/VPK required")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        with zipfile.ZipFile(stream) as archive:
            entries = archive.infolist()
            if len(entries) > 100000:
                raise ValueError("Archive entry count exceeds limit")
            names = set()
            metadata = []
            for entry in entries:
                name = entry.filename
                relative = PurePosixPath(name)
                if (relative.is_absolute() or "\\" in name or "\0" in name
                    or ".." in relative.parts):
                    raise ValueError("Unsafe archive member")
                normalized = relative.as_posix().casefold()
                if normalized in names:
                    raise ValueError("Duplicate archive member")
                names.add(normalized)
                if normalized == "sce_sys/param.sfo":
                    metadata.append(entry)
            if len(metadata) != 1:
                raise ValueError("Exactly one root sce_sys/param.sfo required")
            entry = metadata[0]
            if (entry.flag_bits & 1 or entry.is_dir()
                or stat.S_ISLNK(entry.external_attr >> 16)
                or not 20 <= entry.file_size <= shared.MAX_SIZE):
                raise ValueError("Unsupported archive metadata member")
            with archive.open(entry) as member:
                data = member.read(shared.MAX_SIZE + 1)
            if len(data) != entry.file_size:
                raise ValueError("Truncated archive metadata")
            fields = shared.parse(data)
            if not re.fullmatch("[A-Z]{4}[0-9]{5}", fields.get("TITLE_ID", "")):
                raise ValueError("Internal Vita title ID required")
        if shared.identity(before) != shared.identity(os.fstat(stream.fileno())):
            raise ValueError("Archive changed during metadata reading")
    if shared.identity(before) != shared.identity(path.stat()):
        raise ValueError("Archive changed before completion")
    return {
        "path": str(path.absolute()), "fields": fields,
        "stat_identity": shared.identity(before),
        "metadata_sha256": hashlib.sha256(data).hexdigest(),
        "package_integrity_verified": False,
        "game_relationship_verified": False,
    }
