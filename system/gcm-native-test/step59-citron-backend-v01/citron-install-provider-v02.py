
"""Citron provider for the shared GCM installation job contract."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import shutil
import sys

SHARED = Path(
    "/userdata/system/gcm-native-test/v34-development/"
    "v35-native-development/gcm-v35-verified-install.py"
)
SHARED_SHA = "19e01da73ec4cb15e08aa4f38fa855314822f2fb32ed0148cd23c435c46f1c3a"

def shared_installer():
    if SHARED.is_symlink() or hashlib.sha256(SHARED.read_bytes()).hexdigest() != SHARED_SHA:
        raise ValueError("Shared verified installer changed")
    spec = importlib.util.spec_from_file_location("gcm_shared_verified_install", SHARED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def preview(args):
    shared = shared_installer()
    candidate = shared.load_candidate(args.rom, args.token, fresh=True)
    target = candidate["target"]
    print("INSTALL\tSTATUS\tPREVIEW")
    print("INSTALL\tGAME_TITLE_ID\t" + candidate["base_id"])
    print("INSTALL\tPATCH_TITLE_ID\t" + candidate["patch_id"])
    print("INSTALL\tSOURCE\t" + candidate["source"])
    print("INSTALL\tLOCAL\t" + str(target))
    print("INSTALL\tSOURCE_BYTES\t" + str(Path(candidate["source"]).stat().st_size))
    print("INSTALL\tFREE_BYTES\t" + str(shutil.disk_usage("/userdata").free))
    print("INSTALL\tFRESH_VERIFICATION\tYES")
    print("INSTALL\tCURRENT_LOCAL_STATE\t" +
          ("PRESENT_UNCHECKED" if target.is_file() else "NOT_COPIED"))
    print("INSTALL\tACTIVE_PRESERVED\tYES")
    print("INSTALL\tEXTERNAL_ORIGINAL_PRESERVED\tYES")
    print("STATUS\tPREVIEW ONLY - NO FILE CHANGES")


def dependency(name, expected):
    path = Path(__file__).resolve().parent / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Citron dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def checked_candidate(args, shared, fresh):
    candidate = shared.load_candidate(args.rom, args.token, fresh=fresh)
    if (
        candidate["base_id"] != "010075101EF84000"
        or candidate["patch_id"] != "010075101EF84800"
        or str(candidate["entry"]["version"]) != "131072"
    ):
        raise ValueError("Installation provider currently supports the verified Stray profile only")
    return candidate

def physical_status(shared, candidate):
    packages = dependency("citron-package-verify-v02.py",
        "24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f")
    nand = dependency("citron-nand-verify-v02.py",
        "35ca963cd6326d486de4e610773c080892c8246e3836f84d0c849457b6abab60")
    parser, reader = packages.dependencies()
    from PyQt5.QtCore import QSettings
    config_path = Path("/userdata/system/configs/yuzu/qt-config.ini")
    config_hash = shared.sha(config_path)
    config = QSettings(str(config_path), QSettings.IniFormat)
    config.setFallbacksEnabled(False)
    config.setIniCodec("UTF-8")
    nand_path = Path(str(config.value("Data Storage/nand_directory", "")))
    if not nand_path.is_absolute() or config.status() != QSettings.NoError:
        raise ValueError("Effective NAND setting unavailable")
    registered = (nand_path / "user/Contents/registered").resolve(strict=True)
    metadata = registered / "00000094/267c2e5eb5b33178b092cd0d27f89dea.nca"

    target = candidate["target"]
    if target.is_symlink() or not target.is_file():
        raise ValueError("Verified managed copy required")
    target_before = shared.source_id(target)
    source_before = shared.source_id(candidate["source"])
    report = nand.verify_record(
        registered, metadata, candidate["base_id"], "update")
    if (
        report["title_id"] != candidate["patch_id"]
        or report["internal_version"] != 131072
        or report["primary_content_hashes_verified"] is not True
    ):
        raise ValueError("Existing NAND installation does not match candidate")

    with target.open("rb") as stream:
        entries = reader.metadata_entries(stream, target.stat().st_size)
        if len(entries) != 1:
            raise ValueError("Expected one managed metadata NCA")
        name, offset, size = entries[0]
        stream.seek(offset)
        data = stream.read(size)
        if (
            len(data) != size or data != metadata.read_bytes()
            or name.lower() != hashlib.sha256(data).hexdigest()[:32] + ".cnmt.nca"
        ):
            raise ValueError("Managed and NAND update metadata differ")

    digest = shared.sha(target)
    if digest != shared.sha(candidate["source"]):
        raise ValueError("Managed copy differs from verified source")
    if (
        shared.source_id(target) != target_before
        or shared.source_id(candidate["source"]) != source_before
        or shared.sha(config_path) != config_hash
    ):
        raise ValueError("Files or configuration changed during status verification")
    if list(parser.stat_identity(metadata.stat())) != report["metadata_stat_identity"]:
        raise ValueError("NAND metadata changed")
    for record in report["verified_records"]:
        if list(parser.stat_identity(Path(record["path"]).stat())) != record["stat_identity"]:
            raise ValueError("NAND content changed")
    return digest

def installed_result(candidate, digest):
    print("INSTALL\tSTATUS\tINSTALLED")
    print("INSTALL\tLOCAL\t" + str(candidate["target"]))
    print("INSTALL\tSHA256\t" + digest)
    print("INSTALL\tREGISTRATION\tEXISTING_NAND_VERIFIED")
    print("INSTALL\tACTIVE_PRESERVED\tYES")
    print("STATUS\tEXTERNAL ORIGINAL PRESERVED")

def status(args):
    shared = shared_installer()
    candidate = checked_candidate(args, shared, fresh=False)
    installed_result(candidate, physical_status(shared, candidate))

def install(args):
    if not args.confirm:
        raise ValueError("Explicit confirmation required")
    import fcntl
    import tempfile
    root = Path(__file__).resolve().parent
    writer = dependency("citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf")
    shared = shared_installer()
    with (root / "configuration-commit.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        config_hash = writer.sha(writer.CONFIG)
        candidate = checked_candidate(args, shared, fresh=True)
        with tempfile.TemporaryDirectory(prefix="shared-install-", dir=root) as temporary:
            digest, copied = shared.copy_verified(candidate, Path(temporary))
        verified_digest = physical_status(shared, candidate)
        writer.ensure_closed()
        if digest != verified_digest or writer.sha(writer.CONFIG) != config_hash:
            raise ValueError("Installation post-check failed")
        installed_result(candidate, digest)
        print("INSTALL\tCOPIED\t" + ("YES" if copied else "NO"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "install", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.action == "preview":
        return preview(args)
    if args.action == "install":
        return install(args)
    return status(args)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:300])
        sys.exit(1)
