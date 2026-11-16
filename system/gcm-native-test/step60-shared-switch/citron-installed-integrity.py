
"""Global verified Citron installed-content snapshot."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
STORE = ROOT / "state/citron-verified-inventory.json"

def load(path, expected, name):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def catalog_module():
    return load(ROOT / "citron-content-catalog.py",
        "6dec4983cb4c7f6707d4604db6f11c5cca01d0d1a9e2f20169e5df84d1e79cfa",
        "global_citron_catalog")

def control_display(report, packages, reader):
    controls = [item for item in report["verified_records"]
                if str(item["type"]).casefold() == "control"]
    if not controls:
        return {"display_version": None, "display_version_available": False}
    if len(controls) != 1:
        raise ValueError("Ambiguous registered Control metadata")
    control = controls[0]
    path = Path(control["path"])
    if packages.digest(path) != control["sha256"]:
        raise ValueError("Control content changed")
    with tempfile.TemporaryDirectory(prefix="citron-control-") as temporary:
        output = Path(temporary)
        result = subprocess.run([
            str(reader.NSTOOL), "-k", str(reader.KEYSET),
            "-t", "nca", "-x", "/0", str(output), str(path),
        ], capture_output=True, timeout=90)
        found = list(output.rglob("control.nacp"))
        if result.returncode or len(found) != 1:
            raise ValueError("Registered Control NACP extraction failed")
        with found[0].open("rb") as stream:
            stream.seek(0x3060)
            raw = stream.read(16)
        if len(raw) != 16:
            raise ValueError("Truncated registered display version")
        version = raw.split(bytes([0]), 1)[0].decode("utf-8")
        if (not version or version != version.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in version)):
            raise ValueError("Invalid registered display version")
    return {"display_version": version, "display_version_available": True,
            "control_sha256": control["sha256"]}

def refresh():
    catalog = catalog_module()
    metadata = json.loads(catalog.STORE.read_text())
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if (metadata.get("schema") != "GCM_CITRON_GLOBAL_METADATA_CATALOG_V1"
            or metadata.get("boot_id") != boot
            or catalog.capture() != metadata["inventory"]):
        raise ValueError("Fresh unchanged registration metadata catalog required")
    before_config = catalog.CONFIG.read_bytes()
    packages, _, reader = catalog.dependencies()
    nand = load(COMPONENTS / "citron-nand-verify-v02.py",
        "35ca963cd6326d486de4e610773c080892c8246e3836f84d0c849457b6abab60",
        "shared_nand_integrity")
    records = []
    for index, item in enumerate(metadata["records"], 1):
        print("VERIFY REGISTERED:", index, "/", len(metadata["records"]),
              item["application_id"], item["content_type"],
              item["title_id"], item["origin"], flush=True)
        path = Path(item["path"])
        if catalog.identity(path) != item["stat_identity"]:
            raise ValueError("Registration changed before verification")
        if item["origin"] == "External":
            proof = packages.verify(path, item["application_id"], item["content_type"])
        else:
            proof = nand.verify_record(
                item["registered_root"], path,
                item["application_id"], item["content_type"])
            if item["content_type"] == "update":
                proof.update(control_display(proof, packages, reader))
        if (
            proof["application_id"] != item["application_id"]
            or proof["title_id"] != item["title_id"]
            or proof["internal_version"] != item["internal_version"]
            or proof.get("primary_content_hashes_verified") is not True
            or proof.get("present_content_hashes_verified") is not True
            or catalog.identity(path) != item["stat_identity"]
        ):
            raise ValueError("Installed content proof mismatch")
        records.append({
            **item, **proof, "origin": item["origin"],
            "registered_root": item["registered_root"],
            "path": str(path), "stat_identity": catalog.identity(path),
        })
    if (catalog.capture() != metadata["inventory"]
            or catalog.CONFIG.read_bytes() != before_config):
        raise ValueError("Content or configuration changed during integrity verification")
    result = {
        "schema": "GCM_CITRON_GLOBAL_VERIFIED_INVENTORY_V1",
        "boot_id": boot, "inventory": metadata["inventory"],
        "records": records, "content_integrity_complete": True,
    }
    if STORE.is_symlink() or STORE.parent.is_symlink():
        raise ValueError("Unsafe verified inventory path")
    fd, temporary = tempfile.mkstemp(prefix=".installed-", dir=STORE.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, STORE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return result

def validate():
    catalog = catalog_module()
    if STORE.is_symlink() or STORE.parent.is_symlink():
        raise ValueError("Unsafe verified inventory")
    result = json.loads(STORE.read_text())
    if (
        result.get("schema") != "GCM_CITRON_GLOBAL_VERIFIED_INVENTORY_V1"
        or result.get("boot_id") != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        or result.get("content_integrity_complete") is not True
        or catalog.capture() != result["inventory"]
    ):
        raise ValueError("Fresh installed-content integrity snapshot required")
    return result

if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["refresh"]:
            result = refresh()
        elif sys.argv[1:] == ["validate"]:
            result = validate()
        else:
            raise ValueError("Usage: refresh | validate")
        print("VERIFIED REGISTERED RECORDS:", len(result["records"]), flush=True)
        print("DISTINCT GAMES:", len({r["application_id"] for r in result["records"]}), flush=True)
        print("PASS: Global installed-content integrity snapshot valid", flush=True)
        print("No content, index or emulator settings changed", flush=True)
    except Exception as exc:
        print("ERROR:", str(exc), flush=True)
        sys.exit(1)
