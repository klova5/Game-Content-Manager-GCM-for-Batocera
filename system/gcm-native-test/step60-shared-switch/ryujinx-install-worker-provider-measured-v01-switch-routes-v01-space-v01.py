
"""Expose global update/DLC installation through the existing shared job protocol."""
import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def provider():
    path = ROOT / "ryujinx-install-provider-v02-measured-v01-switch-routes-v01-space-v01.py"
    expected = "c2da802cef617aa8cef41c42f940c6bdc5d104db35933e48c9c8a8d5a6a250fa"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Global install provider changed")
    spec = importlib.util.spec_from_file_location("global_install_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def physical_status(module, rom, token, content_type):
    prepared = module.prepare(rom, token, content_type)
    if prepared["local_matches"]:
        return {
            "status": "INSTALLED",
            "local": prepared["local_matches"][0]["registered_path"],
        }
    managed = prepared["managed"]
    if not managed.exists():
        return {"status": "NOT_INSTALLED"}
    copier, _, _, _, _ = module.dependencies()
    legacy, store, _ = copier.dependencies()
    if managed.is_symlink() or not managed.is_file():
        raise ValueError("Regular managed content required")
    before = legacy.source_id(managed)
    if (legacy.sha(managed) != prepared["proof"]["sha256"]
        or legacy.source_id(managed) != before
        or store.lookup(rom, prepared["source"], content_type) != prepared["proof"]):
        raise ValueError("Managed content differs from current verified source")
    return {"status": "READY", "local": str(managed)}

def safe(value):
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")[:1000]

def execute(args):
    module = provider()
    if args.action == "preview":
        result = module.preview(args.rom, args.token, args.type)
        return {
            "STATUS": "PREVIEW",
            "LOCAL": result["managed_path"],
            "FRESH_VERIFICATION": "YES",
            "ACTIVE_PRESERVED": "YES",
            "EXTERNAL_ORIGINAL_PRESERVED": "YES",
            "REGISTRATION_ACTION": result["registration_action"],
        }
    if args.action == "install":
        result = module.install(
            args.rom, args.token, args.type, confirmed=args.confirm)
        local = result["local"]
        return {
            "STATUS": result["state"], "LOCAL": local,
            "ACTIVE_PRESERVED": "YES",
            "EXTERNAL_ORIGINAL_PRESERVED": "YES",
            "REGISTRATION_ACTION": result["registration_action"],
        }
    result = physical_status(module, args.rom, args.token, args.type)
    return {"STATUS": result["status"], "LOCAL": result.get("local", "")}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "install", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--type", choices=("update", "dlc"), required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        fields = execute(args)
    for name, value in fields.items():
        if value != "":
            print("INSTALL\t" + name + "\t" + safe(value))
    print("STATUS\tEXTERNAL ORIGINAL PRESERVED")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + safe(exc)[:400])
        sys.exit(1)
