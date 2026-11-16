
"""Transactional publication of an update with a per-version disable key."""
from pathlib import Path
import fcntl
import hashlib
import importlib.util
import json
import os

ROOT = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")
PUBLICATION_ROOT = Path("/userdata/system/game-content/citron-registered")

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Registration dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_preserved_selection(old, desired, update_disabled):
    resolver = load("citron-update-effective-selection-v02.py",
        "c69272852f6ecf84a7bf0cdc9f40a60082eb5980d1917544c85a9c5496440b5d")
    before = resolver.resolve(
        old, update_disabled, inventory_complete=True,
        external_registered=False)
    after = resolver.resolve(
        desired, update_disabled, inventory_complete=True,
        external_registered=True)
    before_token = before["token"] if before else None
    after_token = after["token"] if after else None
    if before_token != after_token:
        raise ValueError("Registration would change the configured update selection")
    return before_token

def update_title_keys(editor, config_path):
    config = editor.settings(config_path)
    matches = []
    count = config.beginReadArray("DisabledAddOns")
    for index in range(count):
        config.setArrayIndex(index)
        if int(str(config.value("title_id", "0")), 10) != 0x0100152000022800:
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
        raise ValueError("Ambiguous update-title configuration")
    return matches[0] if matches else []

def register(managed, application_id, display_version, expected_sha256,
             expected_config_sha256, transaction, *,
             verified_report, config_path=CONFIG, publication_root=PUBLICATION_ROOT):
    writer = load("citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf")
    editor = load("citron-config-preview.py",
        "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3")
    selection = load("citron-update-select-v03.py",
        "2b5ba9e79d7e212be64606c2fb2a03a8743cba09de5dc82fd3aeca0a0342408e")
    publisher = load("citron-publish-managed-update.py",
        "e02f596a40437020bdcfee4b4ec3ebfd76421685ca977b95b9ad5463d924dcf1")
    directories = publisher.dependencies()
    managed = Path(managed)
    config_path = Path(config_path)
    transaction = Path(transaction)
    if (
        len(application_id) != 16
        or any(c not in "0123456789ABCDEF" for c in application_id)
        or not display_version or len(display_version) > 64
        or any(ord(c) < 32 or ord(c) == 127 for c in display_version)
    ):
        raise ValueError("Invalid registration identity/display version")
    if application_id != "0100152000022000" or display_version != "3.0.4":
        raise ValueError("Registration selection guard supports the verified Mario profile only")
    verified_report = json.loads(json.dumps(verified_report))
    if (
        verified_report.get("application_id") != application_id
        or verified_report.get("title_id") != "0100152000022800"
        or verified_report.get("content_type") != "update"
        or verified_report.get("internal_version") != 1310720
        or verified_report.get("display_version") != display_version
        or verified_report.get("sha256") != expected_sha256
        or expected_sha256 != "da6c8a07659cb9d69bc185ed5682a7c3538ed95b035bcfde4181960e9458e2b4"
        or verified_report.get("control_sha256") !=
            "ff6dbdb835d8a406eba78e43d20c0fae5a9d613ee63d8e4287512c700acfe9c7"
        or verified_report.get("primary_content_hashes_verified") is not True
        or verified_report.get("present_content_hashes_verified") is not True
        or verified_report.get("source_size") != managed.stat().st_size
    ):
        raise ValueError("Verified package report does not match registration")
    destination = Path(publication_root) / application_id / "updates" / managed.name
    # Each imported writer module has its own globals; redirect only this instance.
    writer.CONFIG = config_path

    with (ROOT / "configuration-commit.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        if writer.sha(config_path) != expected_config_sha256:
            raise ValueError("Configuration changed before registration")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Publication already exists; verify/reuse it without changing selection")
        old = selection.keys(editor, config_path, int(application_id, 16))
        key = "External Files/Update v" + display_version
        desired = list(old)
        if key not in desired:
            desired.append(key)
        title_disabled = update_title_keys(editor, config_path)
        require_preserved_selection(old, desired, title_disabled)
        expected, rendered = editor.render_disabled(config_path, int(application_id, 16), desired)
        if expected != expected_config_sha256:
            raise ValueError("Configuration changed during planning")
        original = config_path.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected:
            raise ValueError("Configuration changed before backup")
        transaction.mkdir(mode=0o700, parents=True, exist_ok=False)
        (transaction / "qt-config.before.ini").write_bytes(original)
        journal = {
            "state": "prepared",
            "managed_path": str(managed),
            "destination": str(destination),
            "before_sha256": expected,
            "after_sha256": hashlib.sha256(rendered).hexdigest(),
            "before_disabled": old,
            "after_disabled": desired,
        }
        def save():
            path = transaction / "journal.json"
            temporary = transaction / "journal.json.tmp"
            with temporary.open("w") as output:
                json.dump(journal, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        save()
        created = False
        try:
            writer.atomic_write(rendered, expected)
            journal["state"] = "disabled_before_publication"
            save()
            writer.ensure_closed()
            created = publisher.publish(
                managed, destination, expected_sha256,
                publication_root=publication_root)
            writer.ensure_closed()
            if (
                writer.sha(config_path) != journal["after_sha256"]
                or selection.keys(editor, config_path, int(application_id, 16)) != desired
                or not destination.samefile(managed)
            ):
                raise ValueError("Registration post-check failed")
            require_preserved_selection(
                old, selection.keys(editor, config_path, int(application_id, 16)),
                update_title_keys(editor, config_path))
            def file_identity(path):
                item = path.stat()
                return [item.st_dev, item.st_ino, item.st_size,
                        item.st_mtime_ns, item.st_ctime_ns]
            journal.update(
                schema="GCM_CITRON_UPDATE_REGISTRATION_V1",
                state="published_disabled", sha256=expected_sha256,
                application_id=application_id,
                title_id=verified_report["title_id"],
                internal_version=verified_report["internal_version"],
                display_version=display_version,
                verified_report=verified_report,
                managed_stat_identity=file_identity(managed),
                destination_stat_identity=file_identity(destination))
            save()
            return {"destination": str(destination), "created": created,
                    "transaction": str(transaction)}
        except BaseException as exc:
            journal["error"] = str(exc)
            try:
                if created:
                    if destination.is_symlink() or not destination.samefile(managed):
                        raise RuntimeError("Publication changed concurrently")
                    destination.unlink()
                    directories.sync_directory(destination.parent)
                current = writer.sha(config_path)
                if current == journal["after_sha256"]:
                    writer.atomic_write(original, current)
                elif current != expected:
                    raise RuntimeError("Configuration changed concurrently")
                journal["state"] = "rolled_back"
            except BaseException as rollback:
                journal.update(state="rollback_requires_review", rollback_error=str(rollback))
                save()
                raise RuntimeError("Registration failed; inspect " + str(transaction)) from exc
            save()
            raise
