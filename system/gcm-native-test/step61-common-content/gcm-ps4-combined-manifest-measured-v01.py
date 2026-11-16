"""Combine freshly decoded PS4 output manifests without extracting files."""
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-ps4-pfs-manifest-v02-measured-v01.py":
        "9d0dec20230e208bf18af049bbe4fbdaa014cf08fc41bf3636d60b6886a2297d",
    "gcm-ps4-system-manifest-v02.py":
        "0cb0f67c0be9a2d29e9aca89e6070f6d957366c562af891fb0e51759ed4b3f98",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Combined-manifest dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def merge(pfs, system):
    if (
        pfs.get("schema") != "GCM_PS4_PFS_MANIFEST_V1"
        or system.get("schema") != "GCM_PS4_SYSTEM_MANIFEST_V1"
        or pfs.get("installation_authorized") is not False
        or system.get("installation_authorized") is not False
        or pfs["package_sha256"] != system["package_sha256"]
        or pfs["stat_identity"] != system["stat_identity"]
    ):
        raise ValueError("Source manifests disagree")
    outputs = {}

    def add(item):
        relative = item["path"]
        if not isinstance(relative, str):
            raise ValueError("Invalid output path")
        parts = relative.split("/")
        if (
            any(part in ("", ".", "..") for part in parts)
            or any(c in relative for c in ("\\", ":", chr(0)))
            or any(ord(c) < 32 or ord(c) == 127 for c in relative)
        ):
            raise ValueError("Unsafe combined output path")
        if item["kind"] not in ("directory", "file"):
            raise ValueError("Unsupported output kind")
        key = relative.casefold()
        previous = outputs.get(key)
        if previous is not None:
            if (
                previous["path"] == relative
                and previous["kind"] == item["kind"] == "directory"
            ):
                return
            raise ValueError("Conflicting combined output paths")
        outputs[key] = dict(item)

    for origin, manifest in (("PFS", pfs), ("SYSTEM_ENTRY", system)):
        for record in manifest["entries"]:
            item = dict(record)
            item["origin"] = origin
            add(item)

    for item in list(outputs.values()):
        parts = item["path"].split("/")
        for count in range(1, len(parts)):
            parent = "/".join(parts[:count])
            existing = outputs.get(parent.casefold())
            if existing is None:
                add({"path": parent, "kind": "directory", "origin": "IMPLIED"})
            elif existing["path"] != parent or existing["kind"] != "directory":
                raise ValueError("Conflicting output ancestry")

    entries = sorted(outputs.values(), key=lambda item: item["path"])
    return {
        "schema": "GCM_PS4_COMBINED_MANIFEST_V1",
        "status": "SOURCE_BOUND_OUTPUT_MANIFEST",
        "package_sha256": pfs["package_sha256"],
        "stat_identity": pfs["stat_identity"],
        "entries": entries,
        "file_count": sum(item["kind"] == "file" for item in entries),
        "file_bytes": sum(
            item["size"] for item in entries if item["kind"] == "file"),
        "installation_authorized": False,
        "in_game_availability": "UNVERIFIED",
    }

def manifest(reader):
    pfs = load("gcm-ps4-pfs-manifest-v02-measured-v01.py").manifest(reader)
    system = load("gcm-ps4-system-manifest-v02.py").manifest(reader)
    result = merge(pfs, system)
    reader.unchanged()
    return result
