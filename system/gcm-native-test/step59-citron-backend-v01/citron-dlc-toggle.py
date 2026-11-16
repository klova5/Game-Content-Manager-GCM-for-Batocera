
"""Confirmed per-DLC configuration changes for the verified development profile."""
from pathlib import Path
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
CONFIG = Path("/userdata/system/configs/yuzu/qt-config.ini")

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def ensure_closed():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = [
                os.fsdecode(x)
                for x in (proc / "cmdline").read_bytes().split(b"\0") if x
            ]
        except OSError:
            continue
        if not args:
            continue
        name = Path(args[0]).name.lower()
        if (
            name in ("citron", "eden")
            or (name.endswith(".appimage") and name.startswith(("citron", "eden")))
            or any(Path(x).name == "switchlauncher.py" for x in args[1:])
        ):
            raise ValueError("Close Citron/Eden and its launcher before applying changes")

def atomic_write(data, expected):
    fd, name = tempfile.mkstemp(prefix=".gcm-citron-", dir=CONFIG.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, CONFIG.stat().st_mode & 0o777)
        ensure_closed()
        if sha(CONFIG) != expected:
            raise ValueError("Configuration changed before commit")
        os.replace(temporary, CONFIG)
        directory = os.open(CONFIG.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)

def apply(args):
    if not args.confirm:
        raise ValueError("Explicit confirmation required")
    query = ROOT / "citron-native-query-v02.py"
    editor = ROOT / "citron-config-preview.py"
    for path, expected in (
        (query, "9f65d40421e7697ddd60ecf9d7c57cfedf867d527ee6ebd94d4a75624bb0a225"),
        (editor, "48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3"),
    ):
        if path.is_symlink() or sha(path) != expected:
            raise ValueError("Dependency changed: " + path.name)

    with (ROOT / "configuration-commit.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ensure_closed()
        initial_hash = sha(CONFIG)
        response = subprocess.run(
            [
                sys.executable, "-B", str(query),
                "--rom", args.rom, "--emulator", "citron-emu",
                "--mode", "list", "--type", "dlc",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if response.returncode:
            raise ValueError("Live DLC inventory query failed")
        matches = [
            line.split("\t") for line in response.stdout.splitlines()
            if line.startswith("ROW\t")
            and len(line.split("\t")) == 5
            and line.split("\t")[4] == args.token
        ]
        if len(matches) != 1 or matches[0][1] != "AVAILABLE":
            raise ValueError("Selected DLC is not in verified available inventory")
        key = matches[0][3]

        spec = importlib.util.spec_from_file_location("citron_editor", editor)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        game = 0x0100152000022000
        config = module.settings(CONFIG)
        count = config.beginReadArray("DisabledAddOns")
        selections = []
        for index in range(count):
            config.setArrayIndex(index)
            if int(str(config.value("title_id", "0")), 10) != game:
                continue
            size = config.beginReadArray("disabled")
            keys = []
            for item in range(size):
                config.setArrayIndex(item)
                keys.append(config.value("d", ""))
            config.endArray()
            selections.append(keys)
        config.endArray()
        del config
        if len(selections) != 1:
            raise ValueError("Game configuration is ambiguous")
        old = selections[0]
        if args.state == "enable" and "DLC" in old:
            raise ValueError("Global DLC block requires a complete-inventory plan")
        desired = (
            [item for item in old if item != key]
            if args.state == "enable"
            else old if key in old else old + [key]
        )
        expected, rendered = module.render_disabled(CONFIG, game, desired)
        if expected != initial_hash:
            raise ValueError("Configuration changed during planning")
        if desired == old:
            print("OK\tNO CHANGE")
            return

        transaction = ROOT / "transactions" / str(time.time_ns())
        transaction.mkdir(parents=True)
        original = CONFIG.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected:
            raise ValueError("Configuration changed before backup")
        (transaction / "qt-config.before.ini").write_bytes(original)
        journal = {
            "state": "prepared",
            "token": args.token,
            "before_disabled": old,
            "after_disabled": desired,
            "before_sha256": expected,
            "after_sha256": hashlib.sha256(rendered).hexdigest(),
        }
        journal_path = transaction / "journal.json"
        journal_path.write_text(json.dumps(journal, indent=2))
        atomic_write(rendered, expected)
        if sha(CONFIG) != journal["after_sha256"]:
            raise RuntimeError("Post-commit configuration differs; backup: " + str(transaction))
        journal["state"] = "configuration_committed"
        journal_path.write_text(json.dumps(journal, indent=2))
        print("OK\tCONFIGURED_" + args.state.upper() + "D")
        print("BACKUP\t" + str(transaction))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--state", choices=["enable", "disable"], required=True)
    parser.add_argument("--confirm", action="store_true")
    apply(parser.parse_args())

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:300])
        sys.exit(1)
