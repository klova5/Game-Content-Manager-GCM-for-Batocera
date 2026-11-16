"""Wii U folder identity from code/app.xml, cross-checked with meta.xml."""
import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def reader():
    path = ROOT / "gcm-xml-archive-metadata.py"
    expected = "9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared XML reader changed")
    spec = importlib.util.spec_from_file_location("wiiu_shared_xml", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def read(path):
    path = Path(path).absolute()
    if path.name.lower() != "meta.xml" or path.parent.name.lower() != "meta":
        raise ValueError("Native meta/meta.xml layout required")
    app_path = path.parent.parent / "code/app.xml"
    for candidate in (path, app_path):
        for part in (candidate, *candidate.parents):
            if part.is_symlink():
                raise ValueError("Symlink in Wii U metadata path")
    xml = reader()
    meta, app = xml.read_xml(path), xml.read_xml(app_path)
    title = int(app["fields"]["title_id"], 16)
    high = title >> 32
    kinds = {0x00050000: "application", 0x0005000E: "update", 0x0005000C: "dlc"}
    if high not in kinds:
        raise ValueError("Unsupported Wii U platform/title category")
    base = (0x00050000 << 32) | (title & 0xffffffff)
    meta_title = int(meta["fields"]["title_id"], 16)
    allowed = {title, base} if kinds[high] == "update" else {title}
    if meta_title not in allowed:
        raise ValueError("Wii U app.xml and meta.xml identities disagree")
    raw_version = app["fields"].get("title_version", "")
    if not re.fullmatch(r"[0-9a-fA-F]{1,4}", raw_version):
        raise ValueError("Bounded hexadecimal app title_version required")
    version = int(raw_version, 16)
    if xml.read_xml(path) != meta or xml.read_xml(app_path) != app:
        raise ValueError("Wii U metadata changed during inspection")
    return {
        "path": str(path), "stat_identity": meta["stat_identity"],
        "title_id": f"{title:016X}", "application_id": f"{base:016X}",
        "content_type": kinds[high], "internal_version": version,
        "metadata_source": "internal-app.xml-crosschecked-meta.xml",
        "metadata_evidence": {"app": app, "meta": meta},
        "payload_hashes_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
