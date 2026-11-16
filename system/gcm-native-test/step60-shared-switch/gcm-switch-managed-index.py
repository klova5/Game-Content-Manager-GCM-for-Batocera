
"""Record verified common managed copies using the existing index transaction."""
import hashlib
import importlib.util
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = Path("/userdata/system/add-ons/game-content-manager/index.sqlite")
LEGACY = Path("/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-install.py")

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Managed-index dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def record(copy_report, proof):
    legacy = load(LEGACY,
        "19e01da73ec4cb15e08aa4f38fa855314822f2fb32ed0148cd23c435c46f1c3a")
    store = load(ROOT / "gcm-switch-verification-store.py",
        "07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195")
    kind = proof.get("content_type")
    if kind not in ("update", "dlc") or copy_report.get("state") != "READY":
        raise ValueError("Verified READY update/DLC copy required")
    for key in ("application_id", "title_id", "content_type",
                "internal_version", "sha256"):
        if copy_report.get(key) != proof.get(key):
            raise ValueError("Managed copy and proof disagree: " + key)
    source = Path(proof["source"])
    target = Path(copy_report["destination"])
    if any(path.is_symlink() for path in (target, *target.parents)):
        raise ValueError("Symlink in managed index destination")
    folder = "updates" if kind == "update" else "dlc"
    expected = legacy.ROOT / proof["application_id"] / folder / source.name
    if target != expected or not target.is_file():
        raise ValueError("Unexpected managed index destination")
    if store.lookup(proof["base"], source, kind) != proof:
        raise ValueError("Fresh matching source proof required")
    before = legacy.source_id(target)
    if (list(before) != list(copy_report["managed_stat_identity"])
        or legacy.sha(target) != proof["sha256"]
        or legacy.source_id(target) != before):
        raise ValueError("Managed content changed before index registration")
    if store.lookup(proof["base"], source, kind) != proof:
        raise ValueError("Source changed during managed index check")
    _insert_managed({
        "target": target,
        "base_id": proof["application_id"],
        "patch_id": proof["title_id"],
        "content_type": kind,
        "entry": {"version": str(proof["internal_version"])},
        "source": str(source),
    }, proof["sha256"])
    return {"state": "managed", "path": str(target),
            "application_id": proof["application_id"], "content_type": kind}

def _insert_managed(c, digest):
    target = c['target']
    s = target.stat()
    with sqlite3.connect(DB, timeout=30) as con:
        con.execute('BEGIN IMMEDIATE')
        existing = con.execute('SELECT application_id, content_type, state, sha256 FROM packages WHERE path=?',
                               (str(target),)).fetchone()
        if existing and not (
            (existing[0] or '').upper() == c['base_id']
            and existing[1] == c['content_type'] and existing[2] == 'managed'
            and existing[3] == digest
        ):
            raise ValueError('Conflicting managed-index row; refusing overwrite')
        if not existing:
            con.execute('''INSERT INTO packages
                (path, system, title_id, application_id, content_type, version,
                 size, mtime, sha256, state, source_path, metadata_source, last_seen)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (str(target), 'switch', c['patch_id'], c['base_id'], c['content_type'],
                 c['entry'].get('version', ''), s.st_size, s.st_mtime, digest,
                 'managed', c['source'], 'internal-cnmt', time.time()))
