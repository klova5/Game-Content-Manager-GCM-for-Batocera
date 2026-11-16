
"""Ryujinx operations using common Switch proofs and existing native writers."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Ryujinx backend dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def dependencies():
    inventory = load(ROOT / "ryujinx-global-inventory-measured-v01-switch-routes-v01-space-v01.py",
        "ded601f7fcbd583833bcd929d5cd615c424fa66d99fb57f62130697527ffc28e")
    adapter = load(ROOT / "ryujinx-native-adapter-measured-v01.py",
        "155db518541f0196773ef338995f9864c1225f26a813a71ea3459edabb3a0301")
    writer = load(Path(
        "/userdata/system/gcm-native-test/v34-development/"
        "v35-native-development/gcm-v35-verified-install-measured-v01-space-v01.py"),
        "5c4dafd5826105920f17f0b5a90b0aefdbee66157ea934f18c25f26b47e0ae0a")
    return inventory, adapter, writer

def ensure_closed():
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            name = path.read_text().strip().casefold()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if name.startswith("ryujinx"):
            raise ValueError("Close Ryujinx before changing content")

def raw(path):
    return path.read_bytes() if path.exists() else None

def mutate(args):
    if args.confirm is not True:
        raise ValueError("Explicit confirmation required")
    if args.mode in ("apply", "select-update") and args.type != "update":
        raise ValueError("Update selection requires update type")
    if args.mode == "commit" and args.type != "dlc":
        raise ValueError("DLC commit requires DLC type")
    inventory, adapter, writer = dependencies()
    state = ROOT / "state"
    lockpath = state / "ryujinx-configuration.lock"
    if state.is_symlink() or lockpath.is_symlink():
        raise ValueError("Unsafe Ryujinx transaction lock")
    state.mkdir(mode=0o700, exist_ok=True)
    with lockpath.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ensure_closed()
        reader = inventory.integrity()
        game = reader.validate(args.rom)
        native, app, _, _ = adapter.controller(args.rom)
        if args.rom_title_id and args.rom_title_id.upper() != app:
            raise ValueError("Supplied title ID differs from internal ROM identity")
        snapshot = game["native_snapshot"]
        configurations = snapshot["configuration"]
        expected = json.loads(json.dumps({
            key: value["document"] for key, value in configurations.items()
        }))
        update = args.mode in ("apply", "select-update")
        kind = "update" if update else "dlc"
        records = [item for item in game["records"] if item["content_type"] == kind]
        known = {inventory.token(item["registered_path"]): item for item in records}
        operations = []

        if update:
            selected = known.get(args.token)
            if selected is None:
                raise ValueError("Selected update lacks matching registered content proof")
            expected["updates"]["selected"] = selected["registered_path"]
            operations.append(("update", [selected["registered_path"]], True))
        else:
            if args.selected_detected:
                raise ValueError("Verify and install detected DLC before enabling it")
            requested = args.selected_available.split(",") if args.selected_available else []
            if len(requested) != len(set(requested)) or any(token not in known for token in requested):
                raise ValueError("Invalid registered DLC selection")
            if any(item["content_type"] == "dlc" for item in game["problems"]):
                raise ValueError("Resolve unverified DLC registrations before changing DLC")
            wanted = {os.path.realpath(known[token]["registered_path"])
                      for token in requested}
            enabled, disabled = [], []
            for entry in expected["dlc"]:
                path = entry["path"]
                desired = os.path.realpath(path) in wanted
                (enabled if desired else disabled).append(path)
                for item in entry.get("dlc_nca_list", []):
                    item["is_enabled"] = desired
            if enabled:
                operations.append(("dlc", enabled, True))
            if disabled:
                operations.append(("dlc", disabled, False))

        target_name = "updates" if update else "dlc"
        path = Path(configurations[target_name]["path"])
        original = raw(path)
        other_name = "dlc" if update else "updates"
        other_path = Path(configurations[other_name]["path"])
        other_before = raw(other_path)
        before_doc = configurations[target_name]["document"]
        desired_doc = expected[target_name]

        # Avoid native writes when the requested state is already configured.
        def equivalent(document):
            if update:
                return document == desired_doc
            normalized = json.loads(json.dumps(document))
            for entry in normalized:
                for item in entry.get("dlc_nca_list", []):
                    item.setdefault("is_enabled", True)
            return normalized == desired_doc

        if equivalent(before_doc):
            if reader.validate(args.rom) != game:
                raise ValueError("Ryujinx state changed during planning")
            return ("OK\tCONFIGURED_UPDATE_SELECTED\nSTATUS\tALREADY_SELECTED"
                    if update else
                    "OK\tVERIFIED ENABLE={} DISABLE={} IMPORT=0".format(
                        len(requested), len(known) - len(requested)))

        if original is None:
            raise ValueError("Registered content configuration is missing")
        if reader.validate(args.rom) != game:
            raise ValueError("Ryujinx state changed before transaction")
        transaction = state / "ryujinx-configuration-transactions" / uuid.uuid4().hex
        transaction.mkdir(mode=0o700, parents=True)
        backup = transaction / path.name
        backup.write_bytes(original)
        os.chmod(backup, 0o600)
        journal = {"state": "prepared", "application_id": app,
                   "config": str(path), "before_sha256": hashlib.sha256(original).hexdigest(),
                   "before": before_doc, "after": desired_doc}

        def save():
            writer.atomic_bytes(transaction / "journal.json",
                (json.dumps(journal, indent=2) + "\n").encode())
        save()
        allowed = [before_doc]
        current_bytes = original
        try:
            for operation, paths, enabled in operations:
                ensure_closed()
                if raw(path) != current_bytes or raw(other_path) != other_before:
                    raise ValueError("Ryujinx configuration changed before native write")
                next_doc = json.loads(json.dumps(allowed[-1]))
                if operation == "update":
                    next_doc["selected"] = paths[0]
                else:
                    targets = {os.path.realpath(item) for item in paths}
                    for entry in next_doc:
                        if os.path.realpath(entry["path"]) in targets:
                            for item in entry.get("dlc_nca_list", []):
                                if bool(item.get("is_enabled", True)) != enabled:
                                    item["is_enabled"] = enabled
                allowed.append(next_doc)
                if operation == "update":
                    native.ryujinx_apply_registered_update(paths[0], args.rom, confirmed=True)
                else:
                    native.ryujinx_set_dlc_enabled(
                        paths, args.rom, enabled, confirmed=True)
                current_bytes = raw(path)
                if json.loads(current_bytes) != next_doc:
                    raise ValueError("Native writer post-check failed")

            final = reader.validate(args.rom)
            final_doc = final["native_snapshot"]["configuration"][target_name]["document"]
            if not equivalent(final_doc) or raw(other_path) != other_before:
                raise ValueError("Final Ryujinx state differs from requested selection")
            ensure_closed()
            if raw(path) != current_bytes:
                raise ValueError("Ryujinx configuration changed before completion")
            journal["state"] = "committed"
            save()
        except BaseException as exc:
            journal["error"] = str(exc)[:400]
            try:
                ensure_closed()
                current = raw(path)
                if current != original:
                    if current is None or json.loads(current) not in allowed:
                        raise RuntimeError("Concurrent configuration change; refusing overwrite")
                    writer.atomic_bytes(path, original)
                journal["state"] = "rolled_back"
                save()
            except BaseException as rollback:
                journal.update(state="rollback_requires_review",
                               rollback_error=str(rollback)[:400])
                save()
                raise RuntimeError("Inspect Ryujinx transaction: " + str(transaction)) from exc
            raise

        message = ("OK\tCONFIGURED_UPDATE_SELECTED" if update else
            "OK\tVERIFIED ENABLE={} DISABLE={} IMPORT=0".format(
                len(requested), len(known) - len(requested)))
        return message + "\nBACKUP\t" + str(transaction)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", choices=("ryujinx", "ryujinx-emu"), required=True)
    parser.add_argument("--mode",
        choices=("summary", "list", "capabilities", "apply", "select-update", "commit"),
        required=True)
    parser.add_argument("--type", choices=("update", "dlc"))
    parser.add_argument("--token", default="")
    parser.add_argument("--rom-title-id", default="")
    parser.add_argument("--selected-available", default="")
    parser.add_argument("--selected-detected", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        if args.mode == "capabilities":
            output = [
                "CAPABILITY\t{}\t{}".format(name, value)
                for name, value in (
                    ("LIST_UPDATES", 1), ("LIST_DLC", 1),
                    ("COMMIT_AVAILABLE_DLC", 1), ("ACTIVATE_UPDATE", 1),
                    ("INSTALL", 0), ("SCAN", 0), ("COMPLETE_INVENTORY", 0))]
        elif args.mode in ("summary", "list"):
            inventory, _, _ = dependencies()
            output = [inventory.summary(args.rom)] if args.mode == "summary" else inventory.query(args.rom, args.type)
        else:
            output = [mutate(args)]
    print("\n".join(output))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:400])
        sys.exit(1)
