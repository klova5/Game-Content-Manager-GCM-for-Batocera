
"""Shared Citron configuration transaction for any game and content operation."""
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
LOCK = COMPONENTS / "configuration-commit.lock"

def load(path, expected, name):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def save(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def commit(application_id, desired, expected_hash, *,
           validate_inventory, validate_result, operation,
           config_path=CONFIG):
    if (not isinstance(application_id, str) or len(application_id) != 16
            or any(c not in "0123456789ABCDEF" for c in application_id)):
        raise ValueError("Invalid application identity")
    if operation not in ("select-update", "set-dlc"):
        raise ValueError("Unsupported configuration operation")
    if not callable(validate_inventory) or not callable(validate_result):
        raise ValueError("Inventory and result validators required")
    writer = load(COMPONENTS / "citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf",
        "shared_config_writer")
    editor = load(COMPONENTS / "citron-config-preview.py",
        "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3",
        "shared_config_editor")
    rows = load(ROOT / "citron-installed-rows-measured-v01.py",
        "08ad3056382bfb3d6cc0c511a0bed39e018252b8443a037fd9fad69a3a8317ee",
        "shared_config_reader")
    config_path = Path(config_path)
    if config_path.is_symlink():
        raise ValueError("Unsafe configuration path")
    writer.CONFIG = config_path
    rows.CONFIG = config_path
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        if writer.sha(config_path) != expected_hash:
            raise ValueError("Configuration changed before transaction")
        validate_inventory()
        old = rows.disabled_keys(application_id)
        expected, rendered = editor.render_disabled(
            config_path, int(application_id, 16), desired)
        if expected != expected_hash:
            raise ValueError("Configuration changed during planning")
        if desired == old:
            validate_result()
            if writer.sha(config_path) != expected_hash:
                raise ValueError("Configuration changed during validation")
            return {"status": "ALREADY_CONFIGURED", "transaction": None}
        original = config_path.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected_hash:
            raise ValueError("Configuration changed before backup")
        folder = ROOT / "state/configuration-transactions"
        if any(path.is_symlink() for path in (folder, *folder.parents)):
            raise ValueError("Unsafe transaction directory")
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        transaction = folder / uuid.uuid4().hex
        transaction.mkdir(mode=0o700)
        descriptor = os.open(
            transaction / "qt-config.before.ini",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as backup:
            backup.write(original)
            backup.flush()
            os.fsync(backup.fileno())
        journal = {
            "schema": "GCM_CITRON_CONFIGURATION_TRANSACTION_V1",
            "state": "prepared", "application_id": application_id,
            "operation": operation, "config_path": str(config_path),
            "before_sha256": expected_hash,
            "after_sha256": hashlib.sha256(rendered).hexdigest(),
            "before_disabled": old, "after_disabled": desired,
        }
        journal_path = transaction / "journal.json"
        save(journal_path, journal)
        try:
            writer.atomic_write(rendered, expected_hash)
            validate_inventory()
            if rows.disabled_keys(application_id) != desired:
                raise ValueError("Configuration keys failed post-check")
            validate_result()
            writer.ensure_closed()
            if writer.sha(config_path) != journal["after_sha256"]:
                raise ValueError("Configuration changed during post-check")
        except Exception:
            current = writer.sha(config_path)
            if current == journal["after_sha256"]:
                writer.atomic_write(original, current)
                journal["state"] = "rolled_back"
            elif current == expected_hash:
                journal["state"] = "not_committed"
            else:
                journal["state"] = "concurrent_change_requires_review"
            save(journal_path, journal)
            raise
        journal["state"] = "configuration_committed"
        save(journal_path, journal)
        return {"status": "COMMITTED", "transaction": str(transaction)}
