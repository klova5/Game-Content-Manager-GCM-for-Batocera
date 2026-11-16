
"""Mario update provider for the existing shared installation job engine."""
import argparse
import hashlib
import importlib.util
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = "0100152000022000"
TITLE = "0100152000022800"

DEPENDENCIES = {
    "citron-install-provider-v02.py": "6ff3c49b9ffb3196f0da58697f90e26628d17e48c59a4467d3d4e561871d8ae4",
    "citron-package-verify-v03.py": "9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388",
    "citron-update-registration-transaction-v03.py": "a037e70af2a3faf45f12c353c50eaa2805826fc69ab2301349be309581bb8285",
    "citron-update-inventory-snapshot-v03.py": "f20025169c3291677c0be5e9c4b7c9c4643f45f41594b39db7834320b61d0428",
    "citron-dlc-toggle.py": "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf",
    "citron-config-preview.py": "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3",
    "citron-update-select-v03.py": "2b5ba9e79d7e212be64606c2fb2a03a8743cba09de5dc82fd3aeca0a0342408e",
}

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != DEPENDENCIES[name]:
        raise ValueError("Dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def candidate(args, fresh):
    shared = load("citron-install-provider-v02.py").shared_installer()
    item = shared.load_candidate(args.rom, args.token, fresh=fresh)
    if (item["base_id"] != APP or item["patch_id"] != TITLE
            or str(item["entry"]["version"]) != "1310720"):
        raise ValueError("Provider supports the verified Mario v3.0.4 profile only")
    return shared, item

def destination(item):
    return (Path("/userdata/system/game-content/citron-registered")
            / APP / "updates" / item["target"].name)

def identified():
    return {"application_id": APP, "title_id": TITLE,
            "content_type": "update", "internal_version": 1310720}

def plan(item, snapshot, writer):
    snapshot.validate()
    published = destination(item)
    if published.exists() or published.is_symlink():
        evidence = snapshot.coverage_module().verified_publication(
            published, identified())
        if evidence["managed_path"] != str(item["target"]):
            raise ValueError("Existing publication uses a different managed path")
        return "REUSE_VERIFIED_PUBLICATION"
    transaction = load("citron-update-registration-transaction-v03.py")
    editor = load("citron-config-preview.py")
    selector = load("citron-update-select-v03.py")
    old = selector.keys(editor, writer.CONFIG, int(APP, 16))
    desired = list(old)
    key = "External Files/Update v3.0.4"
    if key not in desired:
        desired.append(key)
    transaction.require_preserved_selection(
        old, desired, transaction.update_title_keys(editor, writer.CONFIG))
    return "PUBLISH_DISABLED"

def physical(shared, item, snapshot, writer):
    before_config = writer.sha(writer.CONFIG)
    source_before = shared.source_id(item["source"])
    target_before = shared.source_id(item["target"])
    report = load("citron-package-verify-v03.py").verify(
        item["target"], APP, "update")
    if (report["title_id"] != TITLE or report["internal_version"] != 1310720
            or report["display_version"] != "3.0.4"):
        raise ValueError("Managed package identity/display mismatch")
    evidence = snapshot.coverage_module().verified_publication(
        destination(item), identified())
    if (evidence["managed_path"] != str(item["target"])
            or evidence["sha256"] != report["sha256"]
            or shared.sha(item["source"]) != report["sha256"]):
        raise ValueError("Source, managed copy and publication disagree")
    snapshot.validate()
    if (shared.source_id(item["source"]) != source_before
            or shared.source_id(item["target"]) != target_before
            or writer.sha(writer.CONFIG) != before_config):
        raise ValueError("Content or configuration changed during verification")
    return report["sha256"]

def emit(item, digest):
    print("INSTALL\tSTATUS\tINSTALLED")
    print("INSTALL\tLOCAL\t" + str(item["target"]))
    print("INSTALL\tSHA256\t" + digest)
    print("INSTALL\tREGISTRATION\tVERIFIED_EXTERNAL_PUBLICATION")
    print("INSTALL\tACTIVE_PRESERVED\tYES")
    print("STATUS\tEXTERNAL ORIGINAL PRESERVED")

def run(args):
    if args.action == "install" and not args.confirm:
        raise ValueError("Explicit confirmation required")
    shared, item = candidate(args, fresh=args.action != "status")
    snapshot = load("citron-update-inventory-snapshot-v03.py")
    writer = load("citron-dlc-toggle.py")

    if args.action == "status":
        emit(item, physical(shared, item, snapshot, writer))
        return

    writer.ensure_closed()
    config_hash = writer.sha(writer.CONFIG)
    action = plan(item, snapshot, writer)
    if writer.sha(writer.CONFIG) != config_hash:
        raise ValueError("Configuration changed during planning")
    if args.action == "preview":
        print("INSTALL\tSTATUS\tPREVIEW")
        print("INSTALL\tLOCAL\t" + str(item["target"]))
        print("INSTALL\tFRESH_VERIFICATION\tYES")
        print("INSTALL\tACTIVE_PRESERVED\tYES")
        print("INSTALL\tEXTERNAL_ORIGINAL_PRESERVED\tYES")
        print("INSTALL\tREGISTRATION_ACTION\t" + action)
        print("STATUS\tPREVIEW ONLY - NO FILE CHANGES")
        return

    source_before = shared.source_id(item["source"])
    with tempfile.TemporaryDirectory(prefix="shared-install-", dir=ROOT) as folder:
        digest, copied = shared.copy_verified(item, Path(folder))
    if action == "PUBLISH_DISABLED":
        report = load("citron-package-verify-v03.py").verify(
            item["target"], APP, "update")
        if (report["sha256"] != digest
                or shared.source_id(item["source"]) != source_before):
            raise ValueError("Source or managed proof changed before registration")
        writer.ensure_closed()
        transaction = ROOT / "transactions" / ("update-register-" + uuid.uuid4().hex)
        load("citron-update-registration-transaction-v03.py").register(
            item["target"], APP, report["display_version"], digest,
            config_hash, transaction, verified_report=report)
    elif writer.sha(writer.CONFIG) != config_hash:
        raise ValueError("Configuration changed during installation")
    snapshot.refresh()
    verified = physical(shared, item, snapshot, writer)
    writer.ensure_closed()
    if verified != digest or shared.source_id(item["source"]) != source_before:
        raise ValueError("Installation post-check failed")
    emit(item, verified)
    print("INSTALL\tCOPIED\t" + ("YES" if copied else "NO"))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preview", "install", "status"))
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--confirm", action="store_true")
    run(parser.parse_args())

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:300])
        sys.exit(1)
