
"""Fresh update-source coverage guard for the Mario Kart development profile."""
from pathlib import Path
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from PyQt5.QtCore import QSettings

ROOT = Path(__file__).resolve().parent

def identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def files_under(root, depth_limit):
    root = Path(root)
    if not root.is_dir():
        raise ValueError("Content directory unavailable: " + str(root))
    result = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        depth = len(Path(directory).relative_to(root).parts)
        for name in dirs:
            if (Path(directory) / name).is_symlink() or depth >= depth_limit:
                raise ValueError("Unresolved directory coverage")
        for name in files:
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Unresolved file coverage")
            result.append(path)
    return sorted(result)


def verified_publication(package, identified):
    journal = json.loads(
        (ROOT / "step59EZ-update-registration/journal.json").read_text()
    )
    copy = json.loads(
        (ROOT / "step59EW-mario-shared-copy/report.json").read_text()
    )
    display = json.loads(
        (ROOT / "step59EX-mario-older-update-display.json").read_text()
    )
    managed = Path(copy["destination"])
    expected = (
        Path("/userdata/system/game-content/citron-registered")
        / "0100152000022000" / "updates" / managed.name
    )
    if (
        package != expected
        or journal.get("destination") != str(expected)
        or journal.get("state") != "published_disabled"
        or identified["application_id"] != "0100152000022000"
        or identified["title_id"] != "0100152000022800"
        or identified["content_type"] != "update"
        or identified["internal_version"] != 1310720
        or copy.get("application_id") != identified["application_id"]
        or copy.get("title_id") != identified["title_id"]
        or copy.get("internal_version") != identified["internal_version"]
        or copy.get("state") != "READY"
        or copy.get("sha256") != journal.get("sha256")
        or copy.get("sha256") != "da6c8a07659cb9d69bc185ed5682a7c3538ed95b035bcfde4181960e9458e2b4"
        or display.get("managed_path") != str(managed)
        or display.get("internal_version") != 1310720
        or display.get("display_version") != "3.0.4"
    ):
        raise ValueError("Additional external update requires verification")
    if (
        managed.is_symlink() or package.is_symlink()
        or not package.samefile(managed)
        or identity(managed) != journal.get("managed_stat_identity")
        or identity(package) != journal.get("destination_stat_identity")
    ):
        raise ValueError("Verified publication changed; verify again")
    return {
        "application_id": identified["application_id"],
        "title_id": identified["title_id"],
        "internal_version": identified["internal_version"],
        "display_version": display["display_version"],
        "origin": "External",
        "path": str(package),
        "managed_path": str(managed),
        "sha256": journal["sha256"],
        "stat_identity": identity(package),
        "native_display_disable_key": "External Files/Update v3.0.4",
    }

def check(application_id, progress=False):
    if application_id != "0100152000022000":
        raise ValueError("Game outside verified development profile")

    dependency = ROOT / "citron-package-verify.py"
    if hashlib.sha256(dependency.read_bytes()).hexdigest() != (
        "b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042"
    ):
        raise ValueError("Verifier dependency changed")
    spec = importlib.util.spec_from_file_location("citron_verify", dependency)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser, reader = module.dependencies()

    config_path = Path("/userdata/system/configs/yuzu/qt-config.ini")
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
    config = QSettings(str(config_path), QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    nand = Path(str(config.value("Data Storage/nand_directory", "")))
    sdmc = Path(str(config.value("Data Storage/sdmc_directory", "")))
    if not nand.is_absolute() or not sdmc.is_absolute():
        raise ValueError("Explicit NAND and SDMC locations required")
    registered = (nand / "user/Contents/registered").resolve(strict=True)
    sdmc_registered = (sdmc / "Nintendo/Contents/registered").resolve(strict=True)

    if files_under(sdmc_registered, 4):
        raise ValueError("SDMC content requires inventory verification")

    catalog = json.loads((ROOT / "step59AZ-nand-cnmt-catalog.json").read_text())
    if catalog["problems"] or Path(catalog["registered_root"]).resolve() != registered:
        raise ValueError("NAND catalog unavailable or mismatched")
    cached = {item["metadata_path"]: item for item in catalog["records"]}
    if len(cached) != len(catalog["records"]):
        raise ValueError("Duplicate catalog metadata paths")

    files = files_under(registered, 4)
    snapshots = {str(path): identity(path) for path in files}
    metadata = set()

    for number, path in enumerate(files, 1):
        if not re.fullmatch(r"[0-9a-fA-F]{32}(?:\.cnmt)?\.nca", path.name):
            raise ValueError("Unhandled NAND registration layout")
        result = subprocess.run(
            [str(reader.NSTOOL), "-k", str(reader.KEYSET), "-t", "nca", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=15,
        )
        kinds = re.findall(
            r"^\s*Content Type:\s*([A-Za-z0-9_]+)\s*$",
            result.stdout, re.MULTILINE,
        )
        if result.returncode or len(kinds) != 1:
            raise ValueError("NAND header classification failed")
        if kinds[0] == "Meta":
            metadata.add(str(path))
        if progress and (number % 25 == 0 or number == len(files)):
            print(f"NAND headers checked: {number}/{len(files)}", flush=True)

    if metadata != set(cached):
        raise ValueError("NAND metadata set changed; refresh inventory")
    for name in metadata:
        if identity(Path(name)) != cached[name]["stat_identity"]:
            raise ValueError("NAND metadata changed; refresh inventory")

    updates = [
        item for item in catalog["records"]
        if item["cnmt"]["type"] == 0x81
        and item["cnmt"]["related_id"] == application_id
    ]
    if len(updates) != 1 or updates[0]["cnmt"]["version"] != 1441792:
        raise ValueError("Update candidates differ from verified profile")

    count = int(config.value("UI/Paths/external_content_dirs/size", 0))
    if not 0 <= count <= 128:
        raise ValueError("Invalid external-directory count")
    external_snapshots = {}
    verified_external = {}
    external_roots = []
    for index in range(1, count + 1):
        folder = Path(str(config.value(
            f"UI/Paths/external_content_dirs/{index}/path", ""
        )))
        if not folder.is_absolute():
            raise ValueError("Invalid external content path")
        external_roots.append(folder)
        for package in files_under(folder, 15):
            extension = package.suffix.lower()
            if extension not in (".nsp", ".dnsp", ".xci", ".dxci"):
                continue
            if extension != ".nsp":
                raise ValueError("External container requires additional verification")
            before = identity(package)
            identified = reader.identify(package)
            if (
                identified["application_id"] == application_id
                and identified["content_type"] == "update"
            ):
                verified_external[str(package)] = verified_publication(package, identified)
            external_snapshots[str(package)] = before
            if identity(package) != before:
                raise ValueError("External package changed during coverage check")

    if files_under(registered, 4) != files or files_under(sdmc_registered, 4):
        raise ValueError("Registered content changed during coverage check")
    for name, before in snapshots.items():
        if identity(Path(name)) != before:
            raise ValueError("NAND content changed during coverage check")
    current_packages = {
        str(path) for folder in external_roots
        for path in files_under(folder, 15)
        if path.suffix.lower() in (".nsp", ".dnsp", ".xci", ".dxci")
    }
    if current_packages != set(external_snapshots):
        raise ValueError("External package set changed during coverage check")
    for name, before in external_snapshots.items():
        if identity(Path(name)) != before:
            raise ValueError("External package changed during coverage check")
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != config_hash:
        raise ValueError("Configuration changed during coverage check")

    return {
        "application_id": application_id,
        "update_source_coverage_complete": True,
        "verified_profile_internal_version": 1441792,
        "nand_files_checked": len(files),
        "sdmc_registered_files": 0,
        "external_update_candidates": len(verified_external),
        "verified_external_updates": list(verified_external.values()),
        "configuration_sha256": config_hash,
    }
