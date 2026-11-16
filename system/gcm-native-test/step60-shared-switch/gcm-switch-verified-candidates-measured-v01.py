
"""Shared update/DLC verification overlay; installed state belongs to adapters."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def candidates(rom, content_type):
    engine = load("gcm-switch-content-engine-measured-v01.py",
        "7120a24469daa2e31316235a79989060777f92701628ce3711ae2b67d9b262c4")
    store = load("gcm-switch-verification-store.py",
        "07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195")
    result = engine.candidates(rom, content_type)
    for group in result["groups"]:
        verified = []
        external = []
        for item in group["sources"]:
            item["verification_state"] = "MANAGED_UNASSESSED"
            if item["index_state"] != "external":
                continue
            external.append(item)
            proof = store.lookup(rom, item["path"], content_type)
            item["verification_state"] = "DETECTED"
            if proof is None:
                continue
            if (
                proof["application_id"] != group["application_id"]
                or proof["title_id"] != group["title_id"]
                or proof["internal_version"] != group["internal_version"]
            ):
                raise ValueError("Verification proof and candidate identity disagree")
            item["verification_state"] = "VERIFIED_EXTERNAL"
            item["sha256"] = proof["sha256"]
            if proof.get("display_version"):
                item["display_version"] = proof["display_version"]
            verified.append(item)
        group["verification_state"] = (
            "VERIFIED_EXTERNAL" if verified else
            "DETECTED" if external else "MANAGED_UNASSESSED")
        group["selected_source_token"] = (
            min(verified, key=lambda item: item["path"])["token"]
            if verified else None)
        group["verified_external_sources"] = len(verified)
        group["installed_status"] = "NOT_ASSESSED"
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        result = candidates(args.rom, args.type)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        sys.exit(1)
