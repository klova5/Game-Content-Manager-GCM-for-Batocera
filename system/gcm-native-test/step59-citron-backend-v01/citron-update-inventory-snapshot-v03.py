
"""Cache complete update coverage; validate current paths and file identities."""
from pathlib import Path
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import sys

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "runtime-state/citron-update-inventory-snapshot.json"

def coverage_module():
    path = ROOT / "citron-update-coverage-v03.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != "6fc7337e87364ee5d808d3747b4033509e261dd7c54baed883103c1b04fcbb9e":
        raise ValueError("Coverage checker changed")
    spec = importlib.util.spec_from_file_location("coverage_v02", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def capture(coverage):
    config = coverage.QSettings(
        "/userdata/system/configs/yuzu/qt-config.ini",
        coverage.QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    nand = Path(str(config.value("Data Storage/nand_directory", "")))
    sdmc = Path(str(config.value("Data Storage/sdmc_directory", "")))
    count = int(config.value("UI/Paths/external_content_dirs/size", 0))
    if not nand.is_absolute() or not sdmc.is_absolute() or not 0 <= count <= 128:
        raise ValueError("Invalid storage settings")
    external = [
        Path(str(config.value(f"UI/Paths/external_content_dirs/{i}/path", "")))
        for i in range(1, count + 1)
    ]
    if any(not p.is_absolute() for p in external):
        raise ValueError("Invalid external root")
    if config.status() != coverage.QSettings.NoError:
        raise ValueError("Unreadable storage settings")
    locations = [
        ((nand / "user/Contents/registered").resolve(strict=True), 4),
        ((sdmc / "Nintendo/Contents/registered").resolve(strict=True), 4),
        *[(p, 15) for p in external],
    ]
    files = {}
    for folder, depth in locations:
        for path in coverage.files_under(folder, depth):
            files[str(path)] = coverage.identity(path)
    return {
        "roots": [[str(folder), str(folder.resolve()), depth]
                  for folder, depth in locations],
        "files": files,
    }

def boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

def refresh():
    coverage = coverage_module()
    config_path = Path("/userdata/system/configs/yuzu/qt-config.ini")
    with (ROOT / "configuration-commit.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        before = capture(coverage)
        result = coverage.check("0100152000022000", progress=True)
        after = capture(coverage)
        if (
            before != after
            or result["configuration_sha256"] != before_hash
            or hashlib.sha256(config_path.read_bytes()).hexdigest() != before_hash
            or result.get("update_source_coverage_complete") is not True
        ):
            raise ValueError("Inventory changed during snapshot creation")
        data = {
            "schema": "GCM_CITRON_VERIFIED_UPDATE_COVERAGE_V1",
            "boot_id": boot(),
            "inventory": after,
            "coverage": result,
        }
        temporary = STORE.with_suffix(".json.tmp")
        with temporary.open("w") as output:
            json.dump(data, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, STORE)
    return validate()

def validate():
    if STORE.is_symlink():
        raise ValueError("Unsafe inventory snapshot")
    data = json.loads(STORE.read_text())
    if (
        data.get("schema") != "GCM_CITRON_VERIFIED_UPDATE_COVERAGE_V1"
        or data.get("boot_id") != boot()
        or data.get("coverage", {}).get("update_source_coverage_complete") is not True
    ):
        raise ValueError("Fresh complete inventory snapshot required")
    coverage = coverage_module()
    if capture(coverage) != data["inventory"]:
        raise ValueError("Registered content or storage paths changed; refresh inventory")
    return data["coverage"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("refresh", "validate"))
    args = parser.parse_args()
    result = refresh() if args.action == "refresh" else validate()
    print("PASS: Complete update inventory snapshot valid")
    print("NAND FILES:", result["nand_files_checked"])
    print("EXTERNAL UPDATES:", result["external_update_candidates"])

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:300])
        sys.exit(1)
