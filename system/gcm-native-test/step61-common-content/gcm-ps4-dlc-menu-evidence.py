"""Reuse unchanged DLC verification evidence for menu reads only."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "state/ps4-dlc-menu-evidence"

def stat_identity(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns]

def context(rom, component, full_context):
    identity = component(
        "gcm-ps4-base-identity.py",
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438"
    ).identify(rom)
    selector = component(
        "gcm-ps4-dlc-selection.py",
        "b7db3388b8bf030b9e458b8891bf2acdb6165c513c2d662ceaa54a46808a626b")
    store = selector.store()
    base = Path(identity["metadata_path"])
    reader = store.load("gcm-sfo-metadata.py")

    def snapshot():
        metadata = reader.read(base)
        title = metadata["fields"].get("TITLE_ID", "")
        if not re.fullmatch(r"CUSA[0-9]{5}", title):
            raise ValueError("Internal PS4 game identity required")
        directory = store.safe(store.MANAGED / title / "installed-dlc")
        game = store.safe(selector.ADDONS / title)
        records, states, evidence = {}, {}, {}

        def remember(path):
            path = store.safe(path)
            evidence[str(path)] = stat_identity(path)

        def tree(path):
            path = store.safe(path)
            if not path.is_dir():
                raise ValueError("Installed DLC directory required")
            remember(path)
            count = 0
            for parent, dirs, files in os.walk(path, followlinks=False):
                for name in dirs + files:
                    item = store.safe(Path(parent) / name)
                    if not item.is_dir() and not item.is_file():
                        raise ValueError("Unsupported DLC filesystem entry")
                    remember(item)
                    count += 1
                    if count > 200000:
                        raise ValueError("DLC menu tree exceeds bounds")

        if directory.exists():
            if not directory.is_dir():
                raise ValueError("Installed DLC catalog directory required")
            remember(directory)
            for path in sorted(directory.glob("*.json")):
                store.safe(path)
                before = stat_identity(path)
                raw = path.read_bytes()
                if stat_identity(path) != before:
                    raise ValueError("DLC record changed during reading")
                record = json.loads(raw)
                digest = record.get("package_sha256", "")
                label = record.get("entitlement_label", "")
                if (
                    record.get("schema") != "GCM_PS4_INSTALLED_DLC_V1"
                    or record.get("application_id") != title
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or not re.fullmatch(r"[A-Z0-9_]{16}", label)
                    or path.name != digest + ".json"
                    or Path(record["installed_root"]) != directory / digest
                    or Path(record["source"]).parent != store.MANAGED / title / "dlc"
                    or label in records
                ):
                    raise ValueError("Invalid DLC catalog identity or location")
                evidence[str(path)] = [
                    before, hashlib.sha256(raw).hexdigest()]
                source = store.safe(record["source"])
                if not source.is_file():
                    raise ValueError("DLC source package unavailable")
                remember(source)
                tree(record["installed_root"])
                active = store.safe(game / label)
                states[label] = "DISABLED"
                if active.exists():
                    tree(active)
                    states[label] = "ENABLED"
                records[label] = record

        if game.exists():
            if not game.is_dir():
                raise ValueError("DLC game directory required")
            remember(game)
        if reader.read(base) != metadata:
            raise ValueError("Base metadata changed during menu query")
        document = {
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "base": metadata,
            "configuration": selector.configuration(store),
            "records": records,
            "states": states,
            "filesystem": evidence,
        }
        fingerprint = hashlib.sha256(json.dumps(
            document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return title, records, states, fingerprint

    title, records, states, fingerprint = snapshot()
    cache_name = hashlib.sha256(str(base).encode()).hexdigest() + ".json"
    cache = store.safe(CACHE / cache_name)
    receipt = None
    if cache.is_file():
        try:
            receipt = json.loads(cache.read_text())
        except (OSError, ValueError):
            pass

    valid = (
        isinstance(receipt, dict)
        and receipt.get("schema") == "GCM_PS4_DLC_MENU_EVIDENCE_V1"
        and receipt.get("application_id") == title
        and receipt.get("fingerprint") == fingerprint
    )
    if not valid:
        # Only the original full verifier may establish reusable evidence.
        verified = full_context(rom)
        after = snapshot()
        if (
            after != (title, records, states, fingerprint)
            or verified[3] != title
            or verified[4] != records
            or verified[5] != states
        ):
            raise ValueError("DLC changed during full menu assessment")
        store.safe(CACHE)
        CACHE.mkdir(parents=True, mode=0o700, exist_ok=True)
        store.safe(CACHE)
        receipt = {
            "schema": "GCM_PS4_DLC_MENU_EVIDENCE_V1",
            "application_id": title,
            "fingerprint": fingerprint,
            "installation_authorized": False,
        }
        fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=CACHE)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(receipt, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            store.safe(cache)
            os.replace(temporary, cache)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif snapshot() != (title, records, states, fingerprint):
        raise ValueError("DLC changed during cached menu assessment")

    return selector, base, store, title, records, states
