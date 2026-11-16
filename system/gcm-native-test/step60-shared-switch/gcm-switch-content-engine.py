
"""Shared Switch discovery and verification; no emulator registration writes."""
import argparse
import contextlib
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

DEV = Path("/userdata/system/gcm-native-test/v34-development")
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
PINS = {
    "candidates": (Path(__file__).resolve().parent / "gcm-switch-index-candidates.py",
        "5803ddbdf1b605b9943456c3dbe71db6352c839d1682a025a701175d43de669a"),
    "package": (COMPONENTS / "citron-package-verify-v03.py",
        "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388"),
    "metadata": (DEV / "gcm-v35-auto-preview.py",
        "2f372c00799df5ce3531d237e3e634e86b0590860ff9b8b1c190cd1617e525d5"),
    "base": (Path(__file__).resolve().parent / "gcm-switch-base-identity.py",
        "4a4d0be60991eeb9ae35e2479d563c5aec5c18aae4d6648f7f7ad4aad75bf3f1"),
    "guard": (DEV / "gcm-v35-guarded-preview.py",
        "0fac479891f22558364124033250f6ec4dc77262339eb0c94a4ffaf3dad4df52"),
}
CHECKS = (
    "preview_passed", "identity_match", "main_content_hashes_verified",
    "meta_archive_hash_verified", "base_meta_nca_sha256_verified",
    "base_source_stat_unchanged", "update_source_stat_unchanged",
    "source_stat_unchanged_during_scan",
)

def load(name):
    path, expected = PINS[name]
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location("shared_switch_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    s = Path(path).stat()
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

def inputs(rom, package):
    base = Path(rom).resolve(strict=True)
    source = Path(package).resolve(strict=True)
    if not base.is_file() or not source.is_file() or base.samefile(source):
        raise ValueError("Distinct base game and content files required")
    if base.suffix.lower() not in (".nsp", ".xci") or source.suffix.lower() != ".nsp":
        raise ValueError("NSP/XCI base and NSP content required")
    return base, source

def candidates(rom, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    return load("candidates").candidates(rom, content_type)

def verify(rom, package, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    base, source = inputs(rom, package)
    before = [identity(base), identity(source)]
    metadata = load("metadata")
    packages = load("package")

    with tempfile.TemporaryDirectory(prefix="gcm-shared-switch-") as temporary:
        folder = Path(temporary)
        base_folder = folder / "base"
        base_folder.mkdir()
        verification_base = base
        if base.suffix.lower() == ".xci":
            verification_base, base_report = load("base").xci_application(
                base, folder / "xci")
        else:
            base_report = metadata.inspect(base, base_folder)
        base_cnmt = base_report["cnmt"]
        if base_cnmt["type"] != 0x80:
            raise ValueError("Selected ROM is not a base application")
        application_id = base_cnmt["title_id"]
        report = packages.verify(source, application_id, content_type)
        if content_type == "update":
            if base_cnmt["related_id"] != report["title_id"]:
                raise ValueError("Base and update identities are not reciprocal")
            guard = load("guard")
            # The existing guard restricts reports to its own results directory.
            with tempfile.TemporaryDirectory(
                    prefix="shared-switch-", dir=guard.RESULTS) as guarded:
                output = Path(guarded) / "report.json"
                result = subprocess.run([
                    sys.executable, "-B", str(PINS["guard"][0]),
                    "--base", str(verification_base), "--update", str(source),
                    "--output", str(output),
                ], capture_output=True, text=True, timeout=1900)
                if result.returncode or not output.is_file():
                    raise ValueError("Shared guarded update verification failed")
                proof = json.loads(output.read_text())
                if (
                    proof.get("status") != "PASS"
                    or not all(proof.get(field) is True for field in CHECKS)
                    or proof.get("import_authorized") is not False
                    or proof.get("changes_performed") is not False
                    or proof.get("base_title_id") != application_id
                    or proof.get("patch_title_id") != report["title_id"]
                    or str(proof.get("patch_version")) != str(report["internal_version"])
                ):
                    raise ValueError("Guarded update proof mismatch")

    after = [identity(base), identity(source)]
    if before != after:
        raise ValueError("Base game or content changed during verification")
    if (report.get("primary_content_hashes_verified") is not True
            or report.get("present_content_hashes_verified") is not True):
        raise ValueError("Required content hash checks did not pass")
    return {
        "schema": "GCM_SHARED_SWITCH_CONTENT_PROOF_V1",
        **report,
        "status": "PASS",
        "base": str(base),
        "source": str(source),
        "source_stats": after,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "base_metadata_verified": True,
        "identity_match": True,
        "import_authorized": False,
        "changes_performed": False,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("candidates", "verify", "check-dependencies"))
    parser.add_argument("--rom")
    parser.add_argument("--source")
    parser.add_argument("--type", choices=("update", "dlc"))
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        if args.action == "check-dependencies":
            for name in PINS:
                load(name)
            result = {"status": "PASS", "game_allowlist": False,
                      "content_types": ["update", "dlc"]}
        else:
            if not args.rom or not args.type:
                raise ValueError("--rom and --type required")
            if args.action == "verify":
                if not args.source:
                    raise ValueError("--source required")
                result = verify(args.rom, args.source, args.type)
            else:
                result = candidates(args.rom, args.type)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        sys.exit(1)
