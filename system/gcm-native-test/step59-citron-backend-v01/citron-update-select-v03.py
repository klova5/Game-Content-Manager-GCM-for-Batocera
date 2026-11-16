
"""Confirmed selection of the verified installed Mario Kart update."""
from pathlib import Path
import argparse
import fcntl
import hashlib
import importlib.util
import json
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent

def load(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def keys(editor, config_path, game):
    config = editor.settings(config_path)
    count = config.beginReadArray("DisabledAddOns")
    matches = []
    for index in range(count):
        config.setArrayIndex(index)
        if int(str(config.value("title_id", "0")), 10) != game:
            continue
        size = config.beginReadArray("disabled")
        disabled = []
        for item in range(size):
            config.setArrayIndex(item)
            disabled.append(config.value("d", ""))
        config.endArray()
        matches.append(disabled)
    config.endArray()
    if len(matches) != 1:
        raise ValueError("Game configuration missing or ambiguous")
    return matches[0]

def apply(args):
    if not args.confirm:
        raise ValueError("Explicit confirmation required")
    if args.token not in (
        "citron:update:0100152000022800:1441792:UserNAND",
        "citron:update:0100152000022800:1310720:External",
    ):
        raise ValueError("Update is outside verified development profile")

    writer = load(
        "citron-dlc-toggle.py",
        "28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf",
    )
    coverage = load(
        "citron-update-coverage-v02.py",
        "83d53e303c1c4234c2bac4ea03361e505eae17fcd6a48f8b79feeec72b89cfe3",
    )
    planner = load(
        "citron-update-selection-plan.py",
        "13acbd30301f0c48dc1c8b9de5958d55087e739981017f53a508fe7afa07f86b",
    )
    editor = load(
        "citron-config-preview.py",
        "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3",
    )
    query = ROOT / "citron-native-query-v02.py"
    if writer.sha(query) != "9f65d40421e7697ddd60ecf9d7c57cfedf867d527ee6ebd94d4a75624bb0a225":
        raise ValueError("Live query changed")

    with (ROOT / "configuration-commit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        writer.ensure_closed()
        initial_hash = writer.sha(writer.CONFIG)

        def validate_inventory():
            result = subprocess.run(
                [
                    sys.executable, "-B", str(query),
                    "--rom", args.rom, "--emulator", "citron-emu",
                    "--mode", "list", "--type", "update",
                ],
                capture_output=True, text=True, timeout=150,
            )
            expected = (
                "ROW\tAVAILABLE\tINSTALLED\tv4.0.0\t"
                "citron:update:0100152000022800\n"
            )
            if result.returncode or result.stdout != expected:
                raise ValueError("Selected update content evidence is unavailable or changed")

        validate_inventory()
        covered = coverage.check("0100152000022000")
        if covered["configuration_sha256"] != initial_hash:
            raise ValueError("Configuration changed during verification")

        external = covered.get("verified_external_updates", [])
        if (
            covered.get("external_update_candidates") != 1
            or len(external) != 1
            or external[0]["internal_version"] != 1310720
            or external[0]["display_version"] != "3.0.4"
        ):
            raise ValueError("Update inventory differs from verified two-version profile")

        def validate_publication():
            item = external[0]
            current = coverage.verified_publication(
                Path(item["path"]),
                {
                    "application_id": item["application_id"],
                    "title_id": item["title_id"],
                    "content_type": "update",
                    "internal_version": item["internal_version"],
                },
            )
            if current != item:
                raise ValueError("Registered update changed during selection")

        validate_publication()
        old = keys(editor, writer.CONFIG, 0x0100152000022000)
        candidate = {
            "application_id": "0100152000022000",
            "title_id": "0100152000022800",
            "internal_version": 1441792,
            "content_verified": True,
            "token": "citron:update:0100152000022800:1441792:UserNAND",
            # Installed Citron can derive this NAND label from external Control metadata.
            # Both labels belong to the same NAND candidate in this verified profile.
            "disable_keys": [
                "NAND Files/Update v4.0.0",
                "NAND Files/Update v3.0.4",
            ],
        }
        candidates = [candidate, {
            "application_id": "0100152000022000",
            "title_id": "0100152000022800",
            "internal_version": 1310720,
            "content_verified": True,
            "token": "citron:update:0100152000022800:1310720:External",
            "disable_keys": [external[0]["native_display_disable_key"]],
        }]
        desired = planner.plan_selection(
            "0100152000022000", candidates, args.token, old,
            inventory_complete=covered["update_source_coverage_complete"],
        )
        expected_hash, rendered = editor.render_disabled(
            writer.CONFIG, 0x0100152000022000, desired
        )
        if expected_hash != initial_hash:
            raise ValueError("Configuration changed during planning")
        validate_inventory()
        validate_publication()
        if writer.sha(writer.CONFIG) != initial_hash:
            raise ValueError("Configuration changed before commit")

        if desired == old:
            print("OK\tCONFIGURED_UPDATE_SELECTED")
            print("STATUS\tALREADY_SELECTED")
            return

        transaction = ROOT / "transactions" / ("update-" + str(time.time_ns()))
        transaction.mkdir(parents=True)
        original = writer.CONFIG.read_bytes()
        if hashlib.sha256(original).hexdigest() != initial_hash:
            raise ValueError("Configuration changed before backup")
        (transaction / "qt-config.before.ini").write_bytes(original)
        journal = {
            "state": "prepared",
            "token": args.token,
            "before_disabled": old,
            "after_disabled": desired,
            "before_sha256": initial_hash,
            "after_sha256": hashlib.sha256(rendered).hexdigest(),
        }
        journal_path = transaction / "journal.json"
        journal_path.write_text(json.dumps(journal, indent=2))
        try:
            writer.atomic_write(rendered, initial_hash)
            if (
                writer.sha(writer.CONFIG) != journal["after_sha256"]
                or keys(editor, writer.CONFIG, 0x0100152000022000) != desired
            ):
                raise RuntimeError("Update selection post-check failed")
        except Exception:
            current = writer.sha(writer.CONFIG)
            if current == journal["after_sha256"]:
                writer.atomic_write(original, current)
                journal["state"] = "rolled_back"
            elif current == initial_hash:
                journal["state"] = "not_committed"
            else:
                journal["state"] = "concurrent_change_requires_review"
            journal_path.write_text(json.dumps(journal, indent=2))
            raise

        journal["state"] = "configuration_committed"
        journal_path.write_text(json.dumps(journal, indent=2))
        print("OK\tCONFIGURED_UPDATE_SELECTED")
        print("BACKUP\t" + str(transaction))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--confirm", action="store_true")
    apply(parser.parse_args())

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:300])
        sys.exit(1)
