
"""Ryujinx native configuration adapter for the common Switch content engine."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTROLLER = Path("/userdata/system/add-ons/game-content-manager/gamecontentctl-measured-v01.py")

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Ryujinx adapter dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    st = Path(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def controller(rom):
    rom = Path(rom).resolve(strict=True)
    before = identity(rom)
    reader = load(ROOT / "gcm-switch-base-identity-measured-v01.py",
        "99f68705f60da1217d88bb5253cd2e25f31be49fff59f30d6b3e6bd809a5579a")
    game = reader.identify(rom)
    if game["content_type"] != "application":
        raise ValueError("Base application required")
    native = load(CONTROLLER,
        "c9c2658d6490f78735a08dbeb42577c38fb0c7cdcbc4cc6c52677e73d2c8fca4")
    app = game["application_id"]
    def verified_title_id(candidate):
        if Path(candidate).resolve(strict=True) != rom:
            raise ValueError("Native operation received a different ROM")
        if identity(rom) != before:
            raise ValueError("Base ROM changed during native operation")
        return app
    # Redirect only this imported instance; installed controller stays unchanged.
    native.rom_title_id = verified_title_id
    return native, app, rom, before

def read_configuration(path, default):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Symlink in Ryujinx configuration path")
    if not path.exists():
        return None, default
    if not path.is_file():
        raise ValueError("Ryujinx configuration is not a regular file")
    raw = path.read_bytes()
    return raw, json.loads(raw)

def snapshot(rom):
    native, app, rom, before_rom = controller(rom)
    directory = native.ryujinx_game_dir(app)
    update_path = directory / "updates.json"
    dlc_path = directory / "dlc.json"
    updates_raw, updates = read_configuration(
        update_path, {"selected": None, "paths": []})
    dlc_raw, dlc = read_configuration(dlc_path, [])

    if not isinstance(updates, dict) or not isinstance(updates.get("paths", []), list):
        raise ValueError("Invalid Ryujinx update configuration")
    paths = updates.get("paths", [])
    if any(not isinstance(item, str) or not Path(item).is_absolute()
           for item in paths):
        raise ValueError("Invalid registered update path")
    canonical = [os.path.realpath(item) for item in paths]
    if len(canonical) != len(set(canonical)):
        raise ValueError("Duplicate registered update paths")
    selected = updates.get("selected")
    if selected is not None and not isinstance(selected, str):
        raise ValueError("Invalid selected update")
    if selected and os.path.realpath(selected) not in canonical:
        raise ValueError("Selected update is outside registered update paths")

    if not isinstance(dlc, list):
        raise ValueError("Invalid Ryujinx DLC configuration")
    seen = set()
    for entry in dlc:
        if (not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not Path(entry["path"]).is_absolute()
            or not isinstance(entry.get("dlc_nca_list", []), list)):
            raise ValueError("Invalid registered DLC entry")
        path = os.path.realpath(entry["path"])
        if path in seen:
            raise ValueError("Duplicate registered DLC package")
        seen.add(path)
        for item in entry.get("dlc_nca_list", []):
            if not isinstance(item, dict):
                raise ValueError("Invalid DLC NCA entry")
            if "is_enabled" in item and type(item["is_enabled"]) is not bool:
                raise ValueError("Invalid DLC enabled state")

    registered = native.ryujinx_installed_state(app)
    for path, raw in ((update_path, updates_raw), (dlc_path, dlc_raw)):
        current, _ = read_configuration(path, {})
        if current != raw:
            raise ValueError("Ryujinx configuration changed during read")
    if identity(rom) != before_rom:
        raise ValueError("Base ROM changed during configuration read")
    return {
        "schema": "GCM_RYUJINX_NATIVE_STATE_V1",
        "application_id": app,
        "rom": str(rom),
        "base_stat_identity": before_rom,
        "registered": registered,
        "configuration": {
            "updates": {
                "path": str(update_path), "exists": updates_raw is not None,
                "sha256": hashlib.sha256(updates_raw).hexdigest()
                          if updates_raw is not None else None,
                "document": updates,
            },
            "dlc": {
                "path": str(dlc_path), "exists": dlc_raw is not None,
                "sha256": hashlib.sha256(dlc_raw).hexdigest()
                          if dlc_raw is not None else None,
                "document": dlc,
            },
        },
        "content_integrity_verified_by_this_query": False,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = snapshot(args.rom)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:400])
        sys.exit(1)
