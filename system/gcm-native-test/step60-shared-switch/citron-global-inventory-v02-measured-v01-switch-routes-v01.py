
"""Global Citron inventory combining native registrations and shared candidates."""
import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def query(rom, content_type):
    native = load("citron-installed-rows-measured-v01.py",
        "08ad3056382bfb3d6cc0c511a0bed39e018252b8443a037fd9fad69a3a8317ee")
    shared = load("gcm-switch-candidate-cache-measured-v01-switch-routes-v01.py",
        "dd7b27816fd35943e8bb0ad5ae82c8a3cd063866df7d9ce9a14bd7ccdedf0644")
    before = native.CONFIG.read_bytes()
    application, installed = native.records(rom, content_type)
    output = native.rows(rom, content_type)
    registered = {(item["title_id"], item["internal_version"]) for item in installed}
    candidates = shared.candidates(rom, content_type)
    if candidates["application_id"] != application:
        raise ValueError("Native and shared inventories disagree on game identity")
    for group in candidates["groups"]:
        if (group["title_id"], group["internal_version"]) in registered:
            continue
        external = [item for item in group["sources"]
                    if item["index_state"] == "external"]
        if not external:
            continue
        external.sort(key=lambda item: (
            item["verification_state"] != "VERIFIED_EXTERNAL", item["path"]))
        chosen = external[0]
        verified = chosen["verification_state"] == "VERIFIED_EXTERNAL"
        if content_type == "update":
            display = chosen.get("display_version")
            label = "v" + display if display else "Version " + str(group["internal_version"])
        else:
            label = "DLC " + format(int(group["title_id"], 16) & 0xFFF, "04X")
        fields = [
            "ROW", "AVAILABLE" if verified else "DETECTED",
            "VERIFIED_EXTERNAL" if verified else "UNVERIFIED",
            label, chosen["token"],
        ]
        if any("\t" in field or "\r" in field or "\n" in field for field in fields):
            raise ValueError("Unsafe inventory field")
        output.append("\t".join(fields))
    tokens = [line.split("\t")[4] for line in output]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate inventory tokens")
    if native.CONFIG.read_bytes() != before:
        raise ValueError("Configuration changed during inventory query")
    return output

def summary(rom):
    native = load("citron-installed-rows-measured-v01.py",
        "08ad3056382bfb3d6cc0c511a0bed39e018252b8443a037fd9fad69a3a8317ee")
    before = native.CONFIG.read_bytes()
    updates = [line.split("\t") for line in native.rows(rom, "update")]
    dlc = [line.split("\t") for line in native.rows(rom, "dlc")]
    active = [row for row in updates if row[2] == "ACTIVE"]
    if len(active) > 1:
        raise ValueError("Ambiguous active update")
    version = active[0][3] + " ACTIVE" if active else "NONE ACTIVE"
    enabled = sum(row[:3] == ["ROW", "AVAILABLE", "ENABLED"] for row in dlc)
    if native.CONFIG.read_bytes() != before:
        raise ValueError("Configuration changed during summary")
    return "SUMMARY\t" + version + "\t" + str(enabled) + " ENABLED"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", choices=("citron", "citron-emu"), default="citron-emu")
    parser.add_argument("--mode", choices=("list", "summary"), required=True)
    parser.add_argument("--type", choices=("update", "dlc"))
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        if args.mode == "summary":
            output = [summary(args.rom)]
        else:
            if not args.type:
                raise ValueError("--type required")
            output = query(args.rom, args.type)
    for line in output:
        print(line)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:400])
        sys.exit(1)
