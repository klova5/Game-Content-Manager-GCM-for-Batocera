
"""Game-specific candidates from the existing shared Switch content index."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sqlite3
import sys

ROOT = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
BACKEND = Path(
    "/userdata/system/gcm-native-test/v34-development/"
    "v35-native-development/gcm-v35-backend.py"
)
BACKEND_SHA = "7f1ce0ac49c07a081f03c45b1dd06088a67e540cc7ef92818223907d4e65ec71"

def load(path, expected, name):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def candidates(rom, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    packages = load(
        ROOT / "citron-package-verify-v02.py",
        "24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f",
        "shared_package_reader",
    )
    parser, reader = packages.dependencies()
    rom = Path(rom).absolute()
    before = parser.stat_identity(rom.stat())
    base_reader = load(
        Path(__file__).resolve().parent / "gcm-switch-base-identity.py",
        "4a4d0be60991eeb9ae35e2479d563c5aec5c18aae4d6648f7f7ad4aad75bf3f1", "shared_base_identity")
    game = base_reader.identify(rom)
    if game["content_type"] != "application":
        raise ValueError("Base application required")
    application = game["application_id"]
    backend = load(BACKEND, BACKEND_SHA, "shared_index_backend")
    database = Path(backend.DB_PATH).resolve(strict=True)
    backend.db = lambda: sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    indexed = backend.list_packages(system="switch")
    selected = []
    seen = set()
    problems = []
    for row in indexed:
        tid = str(row.get("title_id") or "").upper()
        app = str(row.get("application_id") or "").upper()
        if app != application and not backend.switch_related(application, tid):
            continue
        path = Path(row["path"])
        if not path.is_file() or path.suffix.lower() != ".nsp":
            continue
        absolute = str(path.absolute())
        if absolute in seen:
            continue
        seen.add(absolute)
        try:
            initial = parser.stat_identity(path.stat())
            identity = reader.identify(path)
            if (
                identity["application_id"] != application
                or identity["content_type"] != content_type
            ):
                continue
            if parser.stat_identity(path.stat()) != initial:
                raise ValueError("Package changed during identity query")
            selected.append({
                **row,
                "path": absolute,
                "application_id": application,
                "title_id": identity["title_id"],
                "content_type": content_type,
                "version": str(identity["internal_version"]),
                "size": identity["source_size"],
                "metadata_source": "internal-cnmt",
                "stat_identity": list(initial),
                "token": absolute.encode("utf-8").hex(),
            })
        except Exception as exc:
            problems.append({"path": absolute, "error": str(exc)[:240]})

    groups = []
    for key, sources in backend._group_packages(selected, application).items():
        sources.sort(key=lambda item: item["path"])
        groups.append({
            "application_id": application,
            "title_id": sources[0]["title_id"],
            "content_type": content_type,
            "internal_version": int(sources[0]["version"]),
            "sources": [{
                "path": item["path"],
                "token": item["token"],
                "index_state": item["state"],
                "stat_identity": item["stat_identity"],
            } for item in sources],
            "content_hashes_verified_by_this_query": False,
            "installed_status": "NOT_ASSESSED",
        })
    groups.sort(key=lambda item: (item["title_id"], item["internal_version"]))
    for item in selected:
        if list(parser.stat_identity(Path(item["path"]).stat())) != item["stat_identity"]:
            raise ValueError("Candidate changed before query completed")
    if parser.stat_identity(rom.stat()) != before:
        raise ValueError("Base changed during query")
    return {
        "application_id": application,
        "content_type": content_type,
        "query_scope": "EXISTING_SHARED_INDEX",
        "complete_storage_inventory": False,
        "groups": groups,
        "problems": problems,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    args = parser.parse_args()
    print(json.dumps(candidates(args.rom, args.type), indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:300])
        sys.exit(1)
