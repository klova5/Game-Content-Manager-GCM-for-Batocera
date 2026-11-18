
"""Plan disabled registration without changing existing content selection."""
import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def resolution():
    path = ROOT / "citron-update-resolution.py"
    expected = "397a7693210fde8d1dbe2e3e6f1dff77b953ebff63c4c518841e05ce0a9721c2"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Update resolver changed")
    spec = importlib.util.spec_from_file_location("registration_resolution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(item):
    if item is None:
        return None
    return (item["application_id"], item["title_id"],
            item["internal_version"], item["origin"], item["path"])

def dlc_key(title):
    return "DLC " + format(int(title, 16) & 0xFFF, "04X")

def plan(records, candidate, disabled, update_title_disabled, *,
         inventory_complete):
    if inventory_complete is not True:
        raise ValueError("Complete registered inventory required")
    app = candidate.get("application_id", "")
    title = candidate.get("title_id", "")
    kind = candidate.get("content_type")
    if (
        not re.fullmatch(r"[0-9A-F]{16}", app)
        or not re.fullmatch(r"[0-9A-F]{16}", title)
        or kind not in ("update", "dlc")
        or candidate.get("origin") != "External"
        or type(candidate.get("internal_version")) is not int
        or candidate["internal_version"] < 0
        or candidate.get("primary_content_hashes_verified") is not True
        or candidate.get("present_content_hashes_verified") is not True
        or not Path(candidate.get("path", "")).is_absolute()
    ):
        raise ValueError("Verified external registration identity required")
    if any(item.get("application_id") != app
           or item.get("content_type") not in ("update", "dlc")
           for item in records):
        raise ValueError("Registered inventory belongs to another game/type")
    for values in (disabled, update_title_disabled):
        if (type(values) is not list
            or any(type(value) is not str or not value for value in values)
            or len(set(values)) != len(values)):
            raise ValueError("Invalid disabled settings")
    if any(item["title_id"] == title
           and item["internal_version"] == candidate["internal_version"]
           and item["content_type"] == kind for item in records):
        raise ValueError("Content version already registered; verify/reuse it")

    updates = [item for item in records if item["content_type"] == "update"]
    dlc = [item for item in records if item["content_type"] == "dlc"]
    engine = resolution()
    before = engine.resolve(
        updates, disabled, update_title_disabled, inventory_complete=True)
    desired = list(disabled)

    if kind == "update":
        if int(title, 16) != int(app, 16) + 0x800:
            raise ValueError("Update title does not match application")
        display = candidate.get("display_version")
        if (not isinstance(display, str) or not display
            or len(display) > 64
            or any(ord(c) < 32 or ord(c) == 127 for c in display)):
            raise ValueError("Verified update display version required")
        key = "External Files/Update v" + display
        after_updates = updates + [candidate]
    else:
        if any(item["title_id"] == title for item in dlc):
            raise ValueError("DLC title already registered; revision handling required")
        key = dlc_key(title)
        if any(dlc_key(item["title_id"]) == key for item in dlc):
            raise ValueError("Native DLC setting key is ambiguous")
        after_updates = updates

    if key not in desired:
        desired.append(key)
    after = engine.resolve(
        after_updates, desired, update_title_disabled, inventory_complete=True)
    if identity(before) != identity(after):
        raise ValueError("Registration would change configured update selection")

    def enabled(items, keys):
        return {
            item["title_id"] for item in items
            if "DLC" not in keys and dlc_key(item["title_id"]) not in keys
        }

    after_dlc = dlc + ([candidate] if kind == "dlc" else [])
    if enabled(dlc, disabled) != enabled(after_dlc, desired):
        raise ValueError("Registration would change enabled DLC")
    return {
        "application_id": app,
        "content_type": kind,
        "before_disabled": list(disabled),
        "after_disabled": desired,
        "preserved_update": identity(before),
        "new_content_disabled": True,
    }
