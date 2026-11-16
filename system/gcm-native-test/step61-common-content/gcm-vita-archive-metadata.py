"""Vita archive identity from embedded SFO; no extraction or installation."""
import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def read(path):
    dependency = ROOT / "gcm-xml-archive-metadata.py"
    expected = "9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2"
    if dependency.is_symlink() or hashlib.sha256(dependency.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared archive reader changed")
    spec = importlib.util.spec_from_file_location("vita_shared_archive", dependency)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.read_zip_sfo(path)
    fields = report["fields"]
    title = fields["TITLE_ID"]
    content_id = fields.get("CONTENT_ID")
    if content_id is not None:
        if (not isinstance(content_id, str)
                or not re.fullmatch(r"[A-Z]{2}[0-9]{4}-[A-Z0-9]{9}_[0-9]{2}-[A-Z0-9]{16}", content_id)
                or content_id[7:16] != title):
            raise ValueError("Vita content ID and title ID disagree")
    category = fields.get("CATEGORY")
    kind = "update" if category == "gp" else "UNRESOLVED"
    version = fields.get("APP_VER", "")
    if kind == "update" and (
        not isinstance(version, str) or not re.fullmatch(r"[0-9]{2}\.[0-9]{2}", version)
    ):
        raise ValueError("Internal update APP_VER required")
    if module.read_zip_sfo(path) != report:
        raise ValueError("Vita archive metadata changed during inspection")
    return {
        "path": report["path"], "stat_identity": report["stat_identity"],
        "title_id": title, "content_type": kind, "version": version,
        "metadata_source": "internal-archive-sfo",
        "metadata_evidence": report,
        "payload_hashes_verified": False,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }
