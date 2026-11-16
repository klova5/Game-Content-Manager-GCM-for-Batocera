"""Candidate inventory with optional source-bound PS4 update file evidence."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-candidate-inventory-v02-measured-v01.py":
        "36e0131f1dccb998f260f377f626a931a1b311593d1f25ddd3baa1399307c193",
    "gcm-platform-installed-evidence-v02-measured-v01-menu-evidence-v01.py":
        "f9e92c448b7bad85de220903569726f0e8e1d186487f142b6016b3cfb73a18cd",
    "gcm-sfo-metadata.py":
        "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Inventory dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def query(base, sources, system, content_type,
          installed_root=None, allow_unrelated=False):
    if type(allow_unrelated) is not bool:
        raise ValueError("Explicit unrelated-file policy required")
    previous = load("gcm-platform-candidate-inventory-v02-measured-v01.py")
    if system != "ps4" or installed_root is None:
        return previous.query(
            base, sources, system, content_type,
            installed_root=installed_root, allow_unrelated=allow_unrelated)
    if content_type != "update":
        raise ValueError("PS4 installed-evidence adapter unavailable for this scope")

    sfo = load("gcm-sfo-metadata.py")
    service = load("gcm-platform-installed-evidence-v02-measured-v01-menu-evidence-v01.py")
    before = sfo.read(base)
    report = previous.query(base, sources, system, content_type)
    original_rows = [dict(row) for row in report["rows"]]
    for row in report["rows"]:
        row["installed_status"] = "NOT_ASSESSED"
        row["installed_version"] = None
        row["installed_evidence"] = None
        row["in_game_availability"] = "UNVERIFIED"
        try:
            proof = service.verify(
                base, row["path"], installed_root, system, content_type,
                allow_unrelated=allow_unrelated)
        except (OSError, ValueError) as exc:
            row["installed_status"] = "UNRESOLVED"
            row["installed_error"] = str(exc)[:240]
        else:
            if (
                proof["application_id"] != row["application_id"]
                or proof["content_type"] != row["content_type"]
                or proof["installed_version"] != row["version"]
                or proof["relationship_evidence"]["package_stat_identity"]
                    != row["stat_identity"]
                or proof["installation_authorized"] is not False
            ):
                raise ValueError("Installed evidence and inventory candidate disagree")
            row["installed_status"] = proof["status"]
            row["installed_version"] = proof["installed_version"]
            row["installed_evidence"] = proof
    current = previous.query(
        base, [row["path"] for row in original_rows], system, content_type)
    if current["rows"] != original_rows or sfo.read(base) != before:
        raise ValueError("Candidate inventory changed during installed assessment")
    return report
