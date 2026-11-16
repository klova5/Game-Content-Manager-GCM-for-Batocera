
"""Read-only Citron native query: initial verified Mario Kart update profile."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sys
from PyQt5.QtCore import QSettings

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Query dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    st = Path(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def query(rom):
    packages = load(
        "citron-package-verify.py",
        "b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042",
    )
    protocol = load(
        "citron-native-protocol.py",
        "a2d71559c17f516187184d9891c4c63829a7e93a7ab462c214ad8085a39bd054",
    )
    parser, reader = packages.dependencies()
    game = reader.identify(rom)
    if (
        game["content_type"] != "application"
        or game["application_id"] != "0100152000022000"
    ):
        raise ValueError("Game is outside the verified development profile")

    evidence = json.loads(
        (ROOT / "step59BB-mario-update-content-evidence.json").read_text()
    )
    row = json.loads((ROOT / "step59BD-mario-update-row.json").read_text())

    if (
        evidence["application_id"] != game["application_id"]
        or row["application_id"] != game["application_id"]
        or row["title_id"] != evidence["title_id"]
        or row["internal_version"] != evidence["internal_version"]
        or evidence.get("non_delta_content_hashes_verified") is not True
    ):
        raise ValueError("Update evidence does not match selected game")

    metadata = Path(evidence["metadata_path"])
    if identity(metadata) != evidence["metadata_stat_identity"]:
        raise ValueError("NAND metadata changed; verification required")
    if packages.digest(metadata)[:32] != metadata.name.split(".")[0]:
        raise ValueError("NAND metadata content ID mismatch")
    for item in evidence["verified_records"]:
        if identity(item["path"]) != item["stat_identity"]:
            raise ValueError("NAND content changed; verification required")

    config_path = Path("/userdata/system/configs/yuzu/qt-config.ini")
    before = hashlib.sha256(config_path.read_bytes()).hexdigest()
    config = QSettings(str(config_path), QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    count = config.beginReadArray("DisabledAddOns")
    matches = []
    for index in range(count):
        config.setArrayIndex(index)
        if int(str(config.value("title_id", "0")), 10) != int(game["application_id"], 16):
            continue
        size = config.beginReadArray("disabled")
        disabled = []
        for item in range(size):
            config.setArrayIndex(item)
            disabled.append(config.value("d", ""))
        config.endArray()
        matches.append(disabled)
    config.endArray()
    if len(matches) != 1:
        raise ValueError("Game settings missing or ambiguous")

    row["configured_enabled"] = not any(
        key in matches[0]
        for key in ("Update", "NAND Files/Update", row["native_disable_key"])
    )
    row["active_status"] = "UNKNOWN"

    if hashlib.sha256(config_path.read_bytes()).hexdigest() != before:
        raise ValueError("Configuration changed during query")
    if identity(metadata) != evidence["metadata_stat_identity"]:
        raise ValueError("Metadata changed during query")
    for item in evidence["verified_records"]:
        if identity(item["path"]) != item["stat_identity"]:
            raise ValueError("Content changed during query")

    return protocol.format_update(row)


def query_dlc(rom):
    packages = load(
        "citron-package-verify.py",
        "b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042",
    )
    nand = load(
        "citron-nand-verify.py",
        "a7e809d029dccb59a0385a4a9484ffd623800dd5cea3d1ccd11b3dac681bc611",
    )
    model = load(
        "citron-dlc-inventory-model.py",
        "914ac9ee5e12b5ea0103af1a160dfee92f6b1727d58f24e92added5720f4e78b",
    )
    protocol = load(
        "citron-native-protocol.py",
        "a2d71559c17f516187184d9891c4c63829a7e93a7ab462c214ad8085a39bd054",
    )
    parser, reader = packages.dependencies()
    game = reader.identify(rom)
    application = game["application_id"]
    if game["content_type"] != "application" or application != "0100152000022000":
        raise ValueError("Game is outside the verified development profile")

    catalog = json.loads((ROOT / "step59AZ-nand-cnmt-catalog.json").read_text())
    if catalog["problems"]:
        raise ValueError("NAND metadata catalog is incomplete")
    for item in catalog["records"]:
        if identity(item["metadata_path"]) != item["stat_identity"]:
            raise ValueError("NAND metadata changed; catalog refresh required")

    config_path = Path("/userdata/system/configs/yuzu/qt-config.ini")
    before = hashlib.sha256(config_path.read_bytes()).hexdigest()
    config = QSettings(str(config_path), QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    count = config.beginReadArray("DisabledAddOns")
    selections = []
    for index in range(count):
        config.setArrayIndex(index)
        if int(str(config.value("title_id", "0")), 10) != int(application, 16):
            continue
        size = config.beginReadArray("disabled")
        keys = []
        for item in range(size):
            config.setArrayIndex(item)
            keys.append(config.value("d", ""))
        config.endArray()
        selections.append(keys)
    config.endArray()
    if len(selections) != 1:
        raise ValueError("Game settings missing or ambiguous")

    count = int(config.value("UI/Paths/external_content_dirs/size", 0))
    directories = [
        Path(str(config.value(f"UI/Paths/external_content_dirs/{i}/path", "")))
        for i in range(1, count + 1)
    ]
    publication_root = Path("/userdata/system/game-content/citron-registered")
    publication_configured = any(
        path.is_absolute() and path.resolve() == publication_root.resolve()
        for path in directories
    )

    evidence = []
    for item in catalog["records"]:
        cnmt = item["cnmt"]
        if cnmt["type"] == 0x82 and cnmt["related_id"] == application:
            evidence.append(nand.verify_record(
                catalog["registered_root"], item["metadata_path"],
                application, "dlc",
            ))

    folder = Path("/userdata/system/game-content/switch") / application / "dlc"
    if folder.is_dir():
        for package in sorted(folder.glob("*.nsp")):
            report = packages.verify(package, application, "dlc")
            record = {
                "title_id": report["title_id"],
                "application_id": application,
                "content_type": "dlc",
                "internal_version": report["internal_version"],
                "content_hashes_verified": report["cnmt_content_hashes_verified"],
                "origin": "GCM_MANAGED",
                "path": str(package),
            }
            evidence.append(record)
            published = publication_root / application / "dlc" / package.name
            if publication_configured and published.is_file():
                if published.is_symlink() or not published.samefile(package):
                    raise ValueError("Published file differs from managed copy")
                evidence.append({
                    **record,
                    "origin": "GCM_PUBLISHED",
                    "path": str(published),
                })

    rows = model.build_dlc_rows(application, evidence, selections[0])
    output = [protocol.format_dlc(row) for row in rows]

    if hashlib.sha256(config_path.read_bytes()).hexdigest() != before:
        raise ValueError("Configuration changed during query")
    for item in catalog["records"]:
        if identity(item["metadata_path"]) != item["stat_identity"]:
            raise ValueError("NAND metadata changed during query")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--emulator", choices=["citron", "citron-emu"], required=True)
    parser.add_argument("--mode", choices=["list"], required=True)
    parser.add_argument("--type", choices=["update", "dlc"], required=True)
    args = parser.parse_args()
    output = query(args.rom) if args.type == "update" else query_dlc(args.rom)
    print(output)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:240])
        sys.exit(1)
