
"""Ryujinx menu inventory backed by common Switch proofs and discovery."""
import argparse
import contextlib
import hashlib
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Ryujinx inventory dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def integrity():
    return load("ryujinx-installed-integrity-v02-measured-v01.py",
        "144796758dcdd3934b2b7a8655509c676f147d0ba6891c83f8591977225ce694")

def clean(value, maximum=120):
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")[:maximum]

def token(path):
    return str(path).encode("utf-8").hex()

def installed_rows(game, content_type):
    result = []
    native = game["native_snapshot"]["registered"]
    configured = {
        (item["content_type"], os.path.realpath(item["path"])): item
        for kind in ("updates", "dlc") for item in native[kind]
    }
    for item in game["records"]:
        if item["content_type"] != content_type:
            continue
        if content_type == "update":
            display = item.get("display_version")
            label = "v" + display if display else "Version " + str(item["internal_version"])
            state = "ACTIVE" if item["active"] else "INSTALLED"
        else:
            entry = configured[("dlc", os.path.realpath(item["registered_path"]))]
            entries = entry.get("dlc_nca_list", [])
            if not entries or any(
                int(nca["title_id"]) != int(item["title_id"], 16)
                for nca in entries):
                raise ValueError("Registered DLC NCA identity differs from verified package")
            label = Path(item["registered_path"]).stem.replace(".", " ")
            state = "ENABLED" if item["active"] else "DISABLED"
        result.append("\t".join((
            "ROW", "AVAILABLE", state, clean(label),
            token(item["registered_path"]))))
    for problem in game["problems"]:
        if problem["content_type"] != content_type:
            continue
        result.append("\t".join((
            "ROW", "AVAILABLE", "REGISTERED_UNVERIFIED",
            "REGISTERED CONTENT - UNVERIFIED", token(problem["path"]))))
    return result

def query(rom, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Content type required")
    reader = integrity()
    game = reader.validate(rom)
    output = installed_rows(game, content_type)
    registered = {
        (item["title_id"], item["internal_version"])
        for item in game["records"] if item["content_type"] == content_type
    }
    shared = load("gcm-switch-candidate-cache-measured-v01-switch-routes-v01.py",
        "4fa31deba8be0707f700876716b409c6b540baa55b0ed39f0aa1a1455bd66dfb")
    candidates = shared.candidates(rom, content_type)
    if candidates["application_id"] != game["native_snapshot"]["application_id"]:
        raise ValueError("Native and shared game identities disagree")
    for group in candidates["groups"]:
        if (group["title_id"], group["internal_version"]) in registered:
            continue
        sources = [item for item in group["sources"]
                   if item["index_state"] == "external"]
        if not sources:
            continue
        sources.sort(key=lambda item: (
            item["verification_state"] != "VERIFIED_EXTERNAL", item["path"]))
        chosen = sources[0]
        verified = chosen["verification_state"] == "VERIFIED_EXTERNAL"
        if content_type == "update":
            display = chosen.get("display_version")
            label = "v" + display if display else "Version " + str(group["internal_version"])
        else:
            label = Path(chosen["path"]).stem.replace(".", " ")
        output.append("\t".join((
            "ROW", "AVAILABLE" if verified else "DETECTED",
            "VERIFIED_EXTERNAL" if verified else "UNVERIFIED",
            clean(label), chosen["token"])))
    tokens = [line.split("\t")[4] for line in output]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate Ryujinx menu tokens")
    if reader.validate(rom) != game:
        raise ValueError("Ryujinx state changed during inventory query")
    return output

def summary(rom):
    reader = integrity()
    game = reader.validate(rom)
    updates = [line.split("\t") for line in installed_rows(game, "update")]
    dlc = [line.split("\t") for line in installed_rows(game, "dlc")]
    active = [item for item in updates if item[2] == "ACTIVE"]
    if len(active) > 1:
        raise ValueError("Ambiguous active Ryujinx update")
    raw_active = any(item.get("active") for item in
                     game["native_snapshot"]["registered"]["updates"])
    version = (
        active[0][3] + " ACTIVE" if active else
        "REGISTERED CONTENT - UNVERIFIED" if raw_active else "NONE ACTIVE")
    enabled = sum(item[2] == "ENABLED" for item in dlc)
    if reader.validate(rom) != game:
        raise ValueError("Ryujinx state changed during summary")
    return "SUMMARY\t" + version + "\t" + str(enabled) + " ENABLED"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", choices=("ryujinx", "ryujinx-emu"), required=True)
    parser.add_argument("--mode", choices=("list", "summary"), required=True)
    parser.add_argument("--type", choices=("update", "dlc"))
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        output = [summary(args.rom)] if args.mode == "summary" else query(args.rom, args.type)
    for line in output:
        print(line)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + clean(exc, 400))
        sys.exit(1)
