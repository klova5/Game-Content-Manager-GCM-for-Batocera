
"""Register verified managed Switch content while preserving configured selection."""
import fcntl
import hashlib
import importlib.util
import json
import os
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPONENTS = Path("/userdata/system/gcm-native-test/step59-citron-backend-v01")
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")
PUBLICATION_ROOT = Path("/userdata/system/game-content/citron-registered")

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Registration dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def file_identity(path):
    st = Path(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def keys(editor, config_path, application_id):
    config = editor.settings(config_path)
    matches = []
    count = config.beginReadArray("DisabledAddOns")
    for index in range(count):
        config.setArrayIndex(index)
        if int(str(config.value("title_id", "0")), 10) != int(application_id, 16):
            continue
        size = config.beginReadArray("disabled")
        disabled = []
        for item in range(size):
            config.setArrayIndex(item)
            disabled.append(str(config.value("d", "")))
        config.endArray()
        matches.append(disabled)
    config.endArray()
    if len(matches) > 1:
        raise ValueError("Ambiguous game configuration")
    return matches[0] if matches else []

def register(managed, verified_report, expected_config_sha256, *,
             read_inventory, refresh_inventory,
             config_path=CONFIG, publication_root=PUBLICATION_ROOT):
    if not callable(read_inventory) or not callable(refresh_inventory):
        raise ValueError("Verified inventory callbacks required")
    planner = load(ROOT / "citron-registration-plan.py",
        "c78ccd7a9a76f448fc33085aa44374ddef1b1e7d2bba9532094512b901763024")
    publisher = load(ROOT / "citron-content-publication.py",
        "b850734f6f1ed6df2fd361779e253ab4374e97da96149ddbd5e6a409af1abe37")
    writer = load(COMPONENTS / "citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf")
    editor = load(COMPONENTS / "citron-config-preview.py",
        "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3")
    directories = publisher.dependencies()
    managed, config_path = Path(managed), Path(config_path)
    if managed.is_symlink() or not managed.is_absolute() or not managed.is_file():
        raise ValueError("Regular absolute managed package required")
    proof = json.loads(json.dumps(verified_report))
    app = proof.get("application_id", "")
    kind = proof.get("content_type")
    if kind not in ("update", "dlc"):
        raise ValueError("Verified update or DLC required")
    if proof.get("source_size") != managed.stat().st_size:
        raise ValueError("Managed size differs from proof")
    digest = proof.get("sha256", "")
    folder = "updates" if kind == "update" else "dlc"
    destination = Path(publication_root) / app / folder / managed.name
    candidate = {
        **proof, "origin": "External", "path": str(destination),
    }
    writer.CONFIG = config_path

    with (COMPONENTS / "configuration-commit.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        if sha(config_path) != expected_config_sha256:
            raise ValueError("Configuration changed before registration")
        snapshot = read_inventory()
        if snapshot.get("content_integrity_complete") is not True:
            raise ValueError("Verified complete registered inventory required")
        roots = [
            Path(item["path"]) for item in snapshot["inventory"]["roots"]
            if item["origin"] == "External"
        ]
        if not any(destination.is_relative_to(path) for path in roots):
            raise ValueError("Publication is outside configured external roots")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Publication exists; verify/reuse it")
        records = [item for item in snapshot["records"]
                   if item["application_id"] == app]
        old = keys(editor, config_path, app)
        patch_id = f"{int(app, 16) + 0x800:016X}"
        title_keys = keys(editor, config_path, patch_id)
        plan = planner.plan(
            records, candidate, old, title_keys, inventory_complete=True)
        desired = plan["after_disabled"]
        expected, rendered = editor.render_disabled(
            config_path, int(app, 16), desired)
        if expected != expected_config_sha256 or read_inventory() != snapshot:
            raise ValueError("Configuration or inventory changed during planning")
        original = config_path.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected:
            raise ValueError("Configuration changed before backup")

        transaction = ROOT / "state/registration-transactions" / uuid.uuid4().hex
        transaction.mkdir(mode=0o700, parents=True, exist_ok=False)
        backup = transaction / "qt-config.before.ini"
        with backup.open("xb") as output:
            os.chmod(backup, 0o600)
            output.write(original)
            output.flush()
            os.fsync(output.fileno())
        journal = {
            "schema": "GCM_CITRON_CONTENT_REGISTRATION_V1",
            "state": "prepared",
            "application_id": app, "content_type": kind,
            "managed_path": str(managed), "destination": str(destination),
            "verified_report": proof,
            "before_sha256": expected,
            "after_sha256": hashlib.sha256(rendered).hexdigest(),
            "before_disabled": old, "after_disabled": desired,
        }

        def save():
            fd, name = tempfile.mkstemp(prefix=".journal-", dir=transaction)
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump(journal, output, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, transaction / "journal.json")
                directories.sync_directory(transaction)
            finally:
                Path(name).unlink(missing_ok=True)

        save()
        created = False
        published_identity = None
        try:
            writer.atomic_write(rendered, expected)
            journal["state"] = "disabled_before_publication"
            save()
            writer.ensure_closed()
            created = publisher.publish(
                managed, destination, digest, content_type=kind,
                publication_root=publication_root)
            published_identity = file_identity(destination)[:2]
            journal["state"] = "published_pending_inventory"
            save()
            refresh_inventory()
            fresh = read_inventory()
            matches = [
                item for item in fresh["records"]
                if item.get("path") == str(destination)
                and item.get("application_id") == app
                and item.get("title_id") == proof["title_id"]
                and item.get("content_type") == kind
                and item.get("internal_version") == proof["internal_version"]
                and item.get("sha256") == digest
                and item.get("primary_content_hashes_verified") is True
                and item.get("present_content_hashes_verified") is True
            ]
            if len(matches) != 1:
                raise ValueError("Fresh inventory did not verify new publication")
            fresh_records = [item for item in fresh["records"]
                             if item["application_id"] == app]
            if keys(editor, config_path, app) != desired:
                raise ValueError("Disabled registration settings changed")
            engine = planner.resolution()
            active = engine.resolve(
                [item for item in fresh_records if item["content_type"] == "update"],
                desired, keys(editor, config_path, patch_id),
                inventory_complete=True)
            if planner.identity(active) != plan["preserved_update"]:
                raise ValueError("Registered update selection changed")
            def enabled(items, disabled):
                return {item["title_id"] for item in items
                        if item["content_type"] == "dlc"
                        and "DLC" not in disabled
                        and planner.dlc_key(item["title_id"]) not in disabled}
            if enabled(records, old) != enabled(fresh_records, desired):
                raise ValueError("Registered DLC selection changed")
            writer.ensure_closed()
            if (sha(config_path) != journal["after_sha256"]
                or not destination.samefile(managed)
                or file_identity(destination)[:2] != published_identity):
                raise ValueError("Registration changed before final commit")
            journal.update(
                state="registered_disabled",
                managed_stat_identity=file_identity(managed),
                destination_stat_identity=file_identity(destination))
            save()
            return {"state": "INSTALLED", "active_preserved": True,
                    "destination": str(destination),
                    "transaction": str(transaction)}
        except BaseException as exc:
            journal["error"] = str(exc)[:500]
            try:
                writer.ensure_closed()
                if created:
                    if (destination.is_symlink()
                        or file_identity(destination)[:2] != published_identity
                        or not destination.samefile(managed)):
                        raise RuntimeError("Publication changed concurrently")
                    destination.unlink()
                    directories.sync_directory(destination.parent)
                current = sha(config_path)
                if current == journal["after_sha256"]:
                    writer.atomic_write(original, current)
                elif current != expected:
                    raise RuntimeError("Configuration changed concurrently")
                refresh_inventory()
                read_inventory()
                journal["state"] = "rolled_back"
                save()
            except BaseException as rollback:
                journal.update(state="rollback_requires_review",
                               rollback_error=str(rollback)[:500])
                save()
                raise RuntimeError("Registration recovery requires review: "
                                   + str(transaction)) from exc
            raise
