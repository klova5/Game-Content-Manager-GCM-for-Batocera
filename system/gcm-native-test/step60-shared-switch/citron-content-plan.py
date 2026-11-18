
"""Game-independent Citron update and DLC configuration plans."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def resolver():
    path = ROOT / "citron-update-resolution.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "397a7693210fde8d1dbe2e3e6f1dff77b953ebff63c4c518841e05ce0a9721c2":
        raise ValueError("Global update resolver changed")
    spec = importlib.util.spec_from_file_location("global_resolution", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def plan_update(records, selected, disabled, update_title_disabled, *,
                inventory_complete):
    resolution = resolver()
    if sum(item == selected for item in records) != 1:
        raise ValueError("Selected update missing or ambiguous")
    displays = {resolution.display(item) for item in records}
    nand_keys = {"NAND Files/Update v" + value for value in displays}
    external_keys = {"External Files/Update v" + value for value in displays}
    known = nand_keys | external_keys | resolution.GLOBALS
    desired = [key for key in disabled if key not in known]
    blocked = set()
    for item in records:
        if item == selected:
            continue
        if item["origin"] == "External":
            blocked.add(resolution.external_key(item))
        elif item["origin"] == "UserNAND":
            if selected["origin"] == "External":
                blocked.update(nand_keys)
            else:
                blocked.add("NAND Files/Update v" + resolution.display(item))
        else:
            raise ValueError("Unsupported native selection origin")
    desired.extend(sorted(blocked - set(desired)))
    actual = resolution.resolve(
        records, desired, update_title_disabled,
        inventory_complete=inventory_complete)
    if actual != selected:
        raise ValueError("Native settings cannot uniquely select this update")
    return desired

def dlc_key(title_id):
    return "DLC " + format(int(title_id, 16) & 0xFFF, "04X")

def plan_dlc(records, enabled_titles, disabled, *, inventory_complete):
    if inventory_complete is not True:
        raise ValueError("Complete verified DLC inventory required")
    if len({item["application_id"] for item in records}) > 1:
        raise ValueError("DLC inventory mixes games")
    for item in records:
        if (item["content_type"] != "dlc"
                or item.get("primary_content_hashes_verified") is not True
                or item.get("present_content_hashes_verified") is not True):
            raise ValueError("Unverified installed DLC")
    titles = {item["title_id"] for item in records}
    enabled = set(enabled_titles)
    if not enabled.issubset(titles):
        raise ValueError("Selected DLC not in this game's installed inventory")
    labels = {dlc_key(title) for title in titles}
    if len(labels) != len(titles):
        raise ValueError("Ambiguous native DLC keys")
    desired = [key for key in disabled if key != "DLC" and key not in labels]
    if not enabled:
        desired.append("DLC")
    else:
        desired.extend(sorted(dlc_key(title) for title in titles - enabled))
    return desired
