
"""Citron installed rows for any internally identified Switch game."""
import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path
from PyQt5.QtCore import QSettings

ROOT = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def disabled_keys(application_id):
    settings = QSettings(str(CONFIG), QSettings.IniFormat)
    settings.setFallbacksEnabled(False)
    settings.setIniCodec("UTF-8")
    count = settings.beginReadArray("DisabledAddOns")
    matches = []
    for index in range(count):
        settings.setArrayIndex(index)
        title = int(str(settings.value("title_id", "0")), 10)
        if title != int(application_id, 16):
            continue
        size = settings.beginReadArray("disabled")
        disabled = []
        for item in range(size):
            settings.setArrayIndex(item)
            disabled.append(str(settings.value("d", "")))
        settings.endArray()
        matches.append(disabled)
    settings.endArray()
    if settings.status() != QSettings.NoError or len(matches) > 1:
        raise ValueError("Unreadable or ambiguous game add-on settings")
    return matches[0] if matches else []

def records(rom, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    base_reader = load("gcm-switch-base-identity-measured-v01.py",
        "99f68705f60da1217d88bb5253cd2e25f31be49fff59f30d6b3e6bd809a5579a")
    integrity = load("citron-installed-integrity.py",
        "987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc")
    application = base_reader.identify(rom)["application_id"]
    inventory = integrity.validate()
    selected = [
        item for item in inventory["records"]
        if item["application_id"] == application
        and item["content_type"] == content_type
    ]
    return application, selected

def update_token(item):
    suffix = hashlib.sha256(item["path"].encode()).hexdigest()[:16]
    return ("citron:update:" + item["title_id"] + ":"
            + str(item["internal_version"]) + ":" + item["origin"] + ":" + suffix)

def rows(rom, content_type):
    before = CONFIG.read_bytes()
    application, installed = records(rom, content_type)
    disabled = set(disabled_keys(application))
    output = []
    if content_type == "dlc":
        grouped = {}
        for item in installed:
            grouped.setdefault(item["title_id"], []).append(item)
        for title_id in sorted(grouped):
            label = "DLC " + format(int(title_id, 16) & 0xFFF, "04X")
            enabled = "DLC" not in disabled and label not in disabled
            token = "citron:dlc:" + application + ":" + title_id
            output.append("ROW\tAVAILABLE\t" +
                ("ENABLED" if enabled else "DISABLED") + "\t" + label + "\t" + token)
    else:
        resolver = load("citron-update-resolution.py",
            "397a7693210fde8d1dbe2e3e6f1dff77b953ebff63c4c518841e05ce0a9721c2")
        update_title = format(int(application, 16) + 0x800, "016X")
        active = resolver.resolve(
            installed, disabled, disabled_keys(update_title),
            inventory_complete=True)
        for item in sorted(installed, key=lambda r: (
                -r["internal_version"], r["origin"], r["path"])):
            display = item.get("display_version")
            label = "v" + display if display else "Version " + str(item["internal_version"])
            token = update_token(item)
            state = "ACTIVE" if item is active else "INSTALLED"
            output.append("ROW\tAVAILABLE\t" + state + "\t" + label + "\t" + token)
    if CONFIG.read_bytes() != before:
        raise ValueError("Configuration changed during installed inventory query")
    return output

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = rows(args.rom, args.type)
    for line in result:
        print(line)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:400])
        sys.exit(1)
