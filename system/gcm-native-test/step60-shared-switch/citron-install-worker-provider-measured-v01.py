
"""Expose global update/DLC installation through the existing shared job protocol."""
import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def provider():
    path = ROOT / "citron-install-provider-v03-measured-v01.py"
    expected = "7a23950119e9f22539105d8b8a4fe8590ce4b190f3a2f22111e6351795d6c047"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Global install provider changed")
    spec = importlib.util.spec_from_file_location("global_install_provider", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def physical_status(module, rom, token, content_type):
    prepared = module.prepare(rom, token, content_type)
    if prepared["matches"]:
        return {
            "status": "INSTALLED",
            "local": prepared["matches"][0]["path"],
        }
    managed = prepared["managed"]
    if not managed.exists():
        return {"status": "NOT_INSTALLED"}
    _, _, _, _, packages = module.dependencies()
    proof = packages.verify(
        managed, prepared["proof"]["application_id"], content_type)
    if any(proof.get(key) != prepared["proof"].get(key) for key in (
        "application_id", "title_id", "content_type", "internal_version", "sha256"
    )):
        raise ValueError("Managed content differs from verified source")
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
            args.rom, args.token, args.type, confirm=args.confirm)
        local = result.get("managed_path") or result.get("registered_paths", [""])[0]
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
