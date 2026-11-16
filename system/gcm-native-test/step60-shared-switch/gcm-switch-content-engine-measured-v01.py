
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
    "candidates": (Path(__file__).resolve().parent / "gcm-switch-index-candidates-measured-v01.py",
        "99a1a5222c1dac8e21982d0fee7491ef21696da16fc89127429494e3c3bb1a47"),
    "package": (COMPONENTS / "citron-package-verify-v03.py",
        "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388"),
    "metadata": (DEV / "gcm-v35-auto-preview-measured-v01.py",
        "39f1a368e0ecbb5696f5d5eaad6859f29ce474406855d4e5a4bd94e7a99f9795"),
    "base": (Path(__file__).resolve().parent / "gcm-switch-base-identity-measured-v01.py",
        "99f68705f60da1217d88bb5253cd2e25f31be49fff59f30d6b3e6bd809a5579a"),
    "guard": (DEV / "gcm-v35-guarded-preview-measured-v01.py",
        "27cc7e5f5e248ddba89a8f96b8a8aafef416c22f4ef665a372200b47e9c3f979"),
}
CHECKS = (
    "preview_passed", "identity_match", "main_content_hashes_verified",
    "meta_archive_hash_verified", "base_meta_nca_sha256_verified",
    "base_source_stat_unchanged", "update_source_stat_unchanged",
    "source_stat_unchanged_during_scan",
)


# Optional measured telemetry; original content checks remain authoritative.
def _gcm_progress():
    import hashlib as _hashlib
    import importlib.util as _importlib
    from pathlib import Path as _Path
    if "_GCM_PROGRESS_MODULE" not in globals():
        _path = _Path('/userdata/system/gcm-native-test/step61-common-content/gcm-global-action-progress-measured-v01.py')
        if _path.is_symlink() or _hashlib.sha256(_path.read_bytes()).hexdigest() != 'd51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
            raise ValueError("Measured progress dependency changed")
        _spec = _importlib.spec_from_file_location("gcm_measured_progress", _path)
        _module = _importlib.module_from_spec(_spec)
        _spec.loader.exec_module(_module)
        globals()["_GCM_PROGRESS_MODULE"] = _module
    return globals()["_GCM_PROGRESS_MODULE"]

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
    packages = _gcm_progress().instrument_switch(load("package"))

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
