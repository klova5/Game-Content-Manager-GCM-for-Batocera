
"""Global Citron registration metadata catalog; no game allowlists."""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from PyQt5.QtCore import QSettings

ROOT = Path(__file__).resolve().parent
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")
STORE = ROOT / "state/citron-metadata-catalog.json"

def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def dependencies():
    path = COMPONENTS / "citron-package-verify-v03.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388":
        raise ValueError("Shared package verifier changed")
    spec = importlib.util.spec_from_file_location("catalog_packages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, *module.dependencies()

def storage():
    config = QSettings(str(CONFIG), QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    nand = Path(str(config.value("Data Storage/nand_directory", "")))
    sdmc = Path(str(config.value("Data Storage/sdmc_directory", "")))
    count = int(config.value("UI/Paths/external_content_dirs/size", 0))
    if not nand.is_absolute() or not sdmc.is_absolute() or not 0 <= count <= 128:
        raise ValueError("Explicit valid Citron storage locations required")
    roots = [
        {"origin": "UserNAND", "path": str((nand / "user/Contents/registered").resolve()), "depth": 4},
        {"origin": "SDMC", "path": str((sdmc / "Nintendo/Contents/registered").resolve()), "depth": 4},
    ]
    for index in range(1, count + 1):
        path = Path(str(config.value(f"UI/Paths/external_content_dirs/{index}/path", "")))
        if not path.is_absolute():
            raise ValueError("Invalid external registration root")
        roots.append({"origin": "External", "path": str(path.resolve()), "depth": 15})
    if config.status() != QSettings.NoError:
        raise ValueError("Unreadable Citron storage settings")
    return roots

def files_under(root, maximum):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Registration directory unavailable: " + str(root))
    result = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        depth = len(Path(folder).relative_to(root).parts)
        for name in dirs:
            if (Path(folder) / name).is_symlink():
                raise ValueError("Symlink inside registration tree")
        if dirs and depth >= maximum:
            raise ValueError("Registration tree exceeds depth limit")
        for name in files:
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Non-regular registered file")
            result.append(path)
    return sorted(result)

def capture():
    roots = storage()
    files = {}
    for item in roots:
        for path in files_under(item["path"], item["depth"]):
            files[str(path)] = identity(path)
    return {"roots": roots, "files": files}

def refresh():
    packages, parser, reader = dependencies()
    before_config = hashlib.sha256(CONFIG.read_bytes()).hexdigest()
    before = capture()
    records = []
    seen = set()
    for location in before["roots"]:
        origin = location["origin"]
        paths = files_under(location["path"], location["depth"])
        for number, path in enumerate(paths, 1):
            if str(path) in seen:
                continue
            seen.add(str(path))
            if origin == "External":
                if path.suffix.lower() in (".xci", ".xcz", ".nsz", ".nca", ".dnsp", ".dxci"):
                    raise ValueError("External container reader required: " + str(path))
                if path.suffix.lower() != ".nsp":
                    continue
                item = reader.identify(path)
                if item["content_type"] in ("update", "dlc"):
                    records.append({
                        **item, "origin": origin, "path": str(path),
                        "registered_root": location["path"],
                        "stat_identity": identity(path),
                        "primary_content_hashes_verified": False,
                    })
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{32}(?:\.cnmt)?\.nca", path.name):
                raise ValueError("Unhandled NAND registration file: " + str(path))
            result = subprocess.run(
                [str(reader.NSTOOL), "-k", str(reader.KEYSET), "-t", "nca", str(path)],
                capture_output=True, text=True, timeout=30)
            kinds = re.findall(r"^\s*Content Type:\s*([A-Za-z0-9_]+)\s*$",
                               result.stdout, re.MULTILINE)
            if result.returncode or len(kinds) != 1:
                raise ValueError("NAND header inspection failed: " + str(path))
            if kinds[0] == "Meta":
                if not 0 < path.stat().st_size <= reader.MAX_META_NCA:
                    raise ValueError("Metadata size outside bounds")
                digest = packages.digest(path)
                if digest[:32] != path.name.split(".")[0].lower():
                    raise ValueError("NAND metadata content ID/hash mismatch")
                with tempfile.TemporaryDirectory(prefix="citron-catalog-") as temporary:
                    output = Path(temporary)
                    extracted = subprocess.run(
                        [str(reader.NSTOOL), "-k", str(reader.KEYSET),
                         "-t", "nca", "-x", "/0", str(output), str(path)],
                        capture_output=True, timeout=90)
                    found = list(output.rglob("*.cnmt"))
                    if extracted.returncode or len(found) != 1:
                        raise ValueError("NAND metadata extraction failed")
                    cnmt = parser.parse_cnmt(found[0])
                if cnmt["type"] in (0x81, 0x82):
                    records.append({
                        "application_id": cnmt["related_id"],
                        "title_id": cnmt["title_id"],
                        "content_type": "update" if cnmt["type"] == 0x81 else "dlc",
                        "internal_version": cnmt["version"],
                        "origin": origin, "path": str(path),
                        "registered_root": location["path"],
                        "stat_identity": identity(path),
                        "metadata_sha256": digest,
                        "primary_content_hashes_verified": False,
                    })
            if number % 25 == 0 or number == len(paths):
                print(origin, "headers checked:", str(number) + "/" + str(len(paths)), flush=True)
    if capture() != before or hashlib.sha256(CONFIG.read_bytes()).hexdigest() != before_config:
        raise ValueError("Registrations or configuration changed during catalog creation")
    result = {
        "schema": "GCM_CITRON_GLOBAL_METADATA_CATALOG_V1",
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "inventory": before, "records": records,
        "content_integrity_complete": False,
    }
    if STORE.parent.is_symlink() or STORE.is_symlink():
        raise ValueError("Unsafe catalog state path")
    STORE.parent.mkdir(mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".catalog-", dir=STORE.parent)
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

if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["refresh"]:
            raise ValueError("Usage: refresh")
        result = refresh()
        print("REGISTERED CONTENT RECORDS:", len(result["records"]))
        print("DISTINCT GAMES:", len({r["application_id"] for r in result["records"]}))
        print("Metadata catalog complete; content integrity verification remains next")
        print("No packages, index or emulator settings changed")
    except Exception as exc:
        print("ERROR:", str(exc), flush=True)
        sys.exit(1)
