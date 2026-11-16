
"""Verify Ryujinx registrations through the common Switch content engine."""
import hashlib
import importlib.util
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "state/ryujinx-verified-inventory.json"

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Ryujinx integrity dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def dependencies():
    adapter = load(ROOT / "ryujinx-native-adapter.py",
        "dbf3498d04fb0d38a2c0ed09b5fc34bd6943a7bfec80b8ede2e78351085f1141")
    engine = load(ROOT / "gcm-switch-content-engine.py",
        "3e14cc15a650e1d1e92f39ca687ccd681242ad7f4eb7f0310acdb9b1b4e35c02")
    return adapter, engine

def identity(path):
    path = Path(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return [os.path.realpath(path), st.st_dev, st.st_ino, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns]

def capture(snapshot):
    return {
        item["path"]: identity(item["path"])
        for kind in ("updates", "dlc")
        for item in snapshot["registered"][kind]
    }

def refresh(roms):
    adapter, engine = dependencies()
    games = {}
    for rom in sorted({str(Path(item).resolve(strict=True)) for item in roms}):
        snapshot = adapter.snapshot(rom)
        files = capture(snapshot)
        records = []
        problems = []
        rows = snapshot["registered"]["updates"] + snapshot["registered"]["dlc"]
        for index, item in enumerate(rows, 1):
            source = Path(item["path"])
            kind = item["content_type"]
            print("VERIFY RYUJINX:", snapshot["application_id"],
                  index, "/", len(rows), kind, source.name, flush=True)
            try:
                if not source.is_file():
                    raise ValueError("Registered package is unavailable")
                if source.resolve(strict=True) == Path(rom):
                    raise ValueError(
                        "Registered base-game container requires bundled-content inspection")
                if source.suffix.lower() != ".nsp":
                    raise ValueError("Registered container type requires additional support")
                proof = engine.verify(rom, source, kind)
                if (proof["application_id"] != snapshot["application_id"]
                    or proof["content_type"] != kind
                    or proof.get("status") != "PASS"
                    or proof.get("primary_content_hashes_verified") is not True
                    or proof.get("present_content_hashes_verified") is not True):
                    raise ValueError("Registered package proof mismatch")
                records.append({
                    **proof,
                    "registered_path": str(source),
                    "origin": "Ryujinx",
                    "active": bool(item.get("active")),
                })
            except Exception as exc:
                problems.append({
                    "path": str(source), "content_type": kind,
                    "error": str(exc).replace("\n", " ")[:400],
                })
        if adapter.snapshot(rom) != snapshot or capture(snapshot) != files:
            raise ValueError("Ryujinx configuration or registered files changed")
        games[rom] = {
            "native_snapshot": snapshot,
            "file_identities": files,
            "records": records,
            "problems": problems,
            "all_registered_content_verified": not problems,
        }
    result = {
        "schema": "GCM_RYUJINX_SHARED_VERIFIED_INVENTORY_V1",
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "games": games,
    }
    # Recheck every game's inputs before publishing the combined snapshot.
    for rom, game in games.items():
        if (adapter.snapshot(rom) != game["native_snapshot"]
            or capture(game["native_snapshot"]) != game["file_identities"]):
            raise ValueError("Library changed before snapshot completion")
    if STORE.is_symlink() or STORE.parent.is_symlink():
        raise ValueError("Unsafe Ryujinx integrity state path")
    STORE.parent.mkdir(mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ryujinx-integrity-", dir=STORE.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STORE)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return result

def registration_identity(snapshot):
    result = json.loads(json.dumps(snapshot["configuration"]))
    result["updates"].pop("sha256", None)
    result["dlc"].pop("sha256", None)
    result["updates"]["document"].pop("selected", None)
    for entry in result["dlc"]["document"]:
        for item in entry.get("dlc_nca_list", []):
            item.pop("is_enabled", None)
    return {
        "application_id": snapshot["application_id"],
        "rom": snapshot["rom"],
        "base_stat_identity": snapshot["base_stat_identity"],
        "configuration": result,
    }

def validate(rom):
    adapter, _ = dependencies()
    if STORE.is_symlink() or STORE.parent.is_symlink():
        raise ValueError("Unsafe Ryujinx integrity state")
    result = json.loads(STORE.read_text())
    if (result.get("schema") != "GCM_RYUJINX_SHARED_VERIFIED_INVENTORY_V1"
        or result.get("boot_id") !=
            Path("/proc/sys/kernel/random/boot_id").read_text().strip()):
        raise ValueError("Fresh Ryujinx integrity snapshot required")
    rom = str(Path(rom).resolve(strict=True))
    game = result.get("games", {}).get(rom)
    if not isinstance(game, dict):
        raise ValueError("Selected game requires installed-content verification")
    current = adapter.snapshot(rom)
    if (registration_identity(current) !=
            registration_identity(game["native_snapshot"])
        or capture(current) != game["file_identities"]):
        raise ValueError("Ryujinx registrations or verified content changed")
    active = {
        (item["content_type"], os.path.realpath(item["path"])): bool(item.get("active"))
        for kind in ("updates", "dlc")
        for item in current["registered"][kind]
    }
    game = json.loads(json.dumps(game))
    game["native_snapshot"] = current
    for item in game["records"]:
        item["active"] = active[
            (item["content_type"], os.path.realpath(item["registered_path"]))]
    return game
