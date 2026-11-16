"""Platform adapter for the existing global Updates and DLC menus."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-platform-native-bridge-measured-v01-space-v01-lookup-v01.py":
        "c46ab52944215160653a823c8349ebe65c38ca022c26f4fd9196108cd7cd9102",
    "gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01-space-v01-lookup-v01.py":
        "45e805ea7560332bbde57876439d56f7e00a21d1c253aa71415e5d6c367d19e2",
}

_GCM_DECLARED_CODE_NAMES = {'gcm-platform-native-bridge-measured-v01-space-v01.py': 'gcm-platform-native-bridge-measured-v01-space-v01-lookup-v01.py', 'gcm-platform-native-bridge-measured-v01.py': 'gcm-platform-native-bridge-measured-v01-space-v01-lookup-v01.py', 'gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01-space-v01.py': 'gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01-space-v01-lookup-v01.py', 'gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01.py': 'gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01-space-v01-lookup-v01.py'}

def checked(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Global backend dependency changed: " + name)
    return path

def invoke(name, arguments):
    result = subprocess.run(
        [sys.executable, "-B", str(checked(name)), *arguments],
        capture_output=True, text=True, timeout=45)
    lines = result.stdout.splitlines()
    errors = [line for line in lines if line.startswith("ERROR\t")]
    if result.returncode or errors:
        raise ValueError(
            errors[-1].split("\t", 1)[1] if errors else
            "Platform content request failed")
    return lines

def original_inventory(rom, emulator, kind):
    lines = invoke("gcm-platform-native-bridge-measured-v01-space-v01-lookup-v01.py", [
        "--rom", rom, "--emulator", emulator, "--type", kind])
    mapping = {
        ("AVAILABLE", "VERIFIED"): ("AVAILABLE", "VERIFIED_EXTERNAL"),
        ("AVAILABLE", "FILES_VERIFIED"): ("AVAILABLE", "REGISTERED_UNVERIFIED"),
        ("DETECTED", "UNVERIFIED"): ("DETECTED", "UNVERIFIED"),
    }
    output, seen = [], set()
    for line in lines:
        if not line.startswith("ROW\t"):
            continue
        fields = line.split("\t")
        if len(fields) != 5 or not fields[3] or not fields[4]:
            raise ValueError("Invalid platform inventory row")
        state = mapping.get((fields[1], fields[2]))
        if state is None or fields[4] in seen:
            raise ValueError("Unsupported or duplicate platform inventory row")
        seen.add(fields[4])
        output.append("\t".join(("ROW", *state, fields[3], fields[4])))
    return output

def installation(action, rom, emulator, kind, token):
    if emulator != "shadps4" or kind not in ("update", "dlc"):
        raise ValueError("Installation adapter unavailable for this scope")
    lines = invoke("gcm-platform-native-install-v03-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-lifecycle-v01-space-v01-lookup-v01.py", [
        action, "--rom", rom, "--emulator", emulator,
        "--type", kind, "--token", token])
    output = []
    for line in lines:
        if line == "INSTALL\tSTATUS\tINSTALLED_FILES_VERIFIED":
            line = "INSTALL\tSTATUS\tINSTALLED"
        if line.startswith(("INSTALL\t", "STATUS\t")):
            output.append(line)
    if not any(line.startswith("INSTALL\tSTATUS\t") for line in output):
        raise ValueError("Installation status missing")
    return output


def component(name, expected):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Selection dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def versions(rom):
    store = component(
        "gcm-ps4-update-version-store-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py",
        "3a01d64f8d0d814d5e5d77f9e1729104ff99d7f94c0ae128c5023beab7a34c99")
    identity = store.load("gcm-ps4-base-identity.py").identify(rom)
    title = identity["application_id"]
    directory = store.MANAGED / title / "installed-updates"
    store.safe(directory)
    records = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            store.safe(path)
            record = json.loads(path.read_text())
            if path.name != record.get("package_sha256", "") + ".json":
                raise ValueError("Installed-version record filename disagrees")
            store.verify(rom, record)
            source = store.safe(record["source"]).resolve(strict=True)
            if source.parent != store.MANAGED / title / "updates":
                raise ValueError("Managed update source required")
            records.append(record)
    return store, identity, records

def selected_digest(store, identity, records):
    title = identity["application_id"]
    overlay = Path("/userdata/roms/ps4") / (title + "-UPDATE")
    alternate = overlay.with_name(title + "-patch")
    store.safe(overlay)
    store.safe(alternate)
    receipt = ROOT / "state/ps4-update-selection" / (title + ".json")
    store.safe(receipt)
    if alternate.exists():
        raise ValueError("Alternate patch directory requires reconciliation")
    if not overlay.exists():
        if receipt.exists():
            raise ValueError("Selected overlay is missing")
        return None
    if not receipt.is_file():
        raise ValueError("Existing overlay has no selection receipt")
    data = json.loads(receipt.read_text())
    if (
        data.get("status") != "SELECTED_FILES_VERIFIED"
        or data.get("overlay") != str(overlay)
        or data.get("application_id") != title
    ):
        raise ValueError("Selection receipt disagrees with game")
    record = data.get("installed_record")
    if record not in records:
        raise ValueError("Selected version has no verified installation record")
    transaction = store.safe(data["transaction"]).resolve(strict=True)
    if transaction.parent != receipt.parent.resolve(strict=True):
        raise ValueError("Selection transaction location disagrees")
    journal = json.loads(transaction.read_text())
    if (
        journal.get("status") != "COMPLETE"
        or journal.get("overlay") != str(overlay)
        or journal.get("installed_record") != record
    ):
        raise ValueError("Selection transaction is incomplete or inconsistent")
    proof = store.load("gcm-platform-content-service-v03-measured-v01-menu-evidence-v01.py").verify_installed(
        identity["metadata_path"], record["source"], overlay,
        "ps4", "update", allow_unrelated=False)
    if (
        proof["installed_version"] != record["version"]
        or proof["installed_evidence"]["source_manifest"]["package_sha256"]
        != record["package_sha256"]
    ):
        raise ValueError("Selected overlay evidence disagrees")
    return record["package_sha256"]

def file_digest(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if opened != before:
            raise ValueError("Candidate changed before hashing")
        while True:
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
        if os.fstat(stream.fileno()) != opened:
            raise ValueError("Candidate changed during hashing")
    if path.stat() != before:
        raise ValueError("Candidate changed after hashing")
    return digest.hexdigest()

def update_inventory(rom, emulator, kind):
    rows = original_inventory(rom, emulator, kind)
    if emulator != "shadps4" or kind != "update":
        return rows
    store, identity, records = versions(rom)
    active = selected_digest(store, identity, records)
    output = []
    for row in rows:
        fields = row.split("\t")
        candidate = Path(bytes.fromhex(fields[4]).decode("utf-8"))
        possible = [
            record for record in records
            if Path(record["source"]).name == candidate.name
        ]
        if possible:
            store.safe(candidate)
            digest = file_digest(candidate)
            if any(record["package_sha256"] == digest for record in possible):
                continue
        output.append(row)
    for record in records:
        state = "ACTIVE" if record["package_sha256"] == active else "INSTALLED"
        label = "Update " + record["version"]
        token = record["source"].encode("utf-8").hex()
        output.append("\t".join(("ROW", "AVAILABLE", state, label, token)))
    tokens = [row.split("\t")[4] for row in output]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate global inventory token")
    return output

def select_update(rom, emulator, kind, token, confirm):
    if emulator != "shadps4" or kind != "update":
        raise ValueError("Update selection unavailable for this scope")
    if not confirm or not token:
        raise ValueError("Confirmed update selection required")
    store, identity, records = versions(rom)
    matches = [
        record for record in records
        if record["source"].encode("utf-8").hex() == token
    ]
    if len(matches) != 1:
        raise ValueError("Select a verified installed update")
    state = ROOT / "state/ps4-update-selection"
    if state.exists():
        store.safe(state)
        for path in state.glob("transaction-*.json"):
            store.safe(path)
            if json.loads(path.read_text()).get("status") != "COMPLETE":
                raise ValueError("Unfinished selection requires recovery assessment")
    selector = component(
        "gcm-ps4-update-selection-measured-v01-menu-evidence-v01-space-v01-lookup-v01.py",
        "3bfd77b2c44aa7e52ea99b193dfbe9da4a87cf9929d1be52cc4428f77929a697")
    selector.select(rom, matches[0])
    if selected_digest(store, identity, records) != matches[0]["package_sha256"]:
        raise ValueError("Selected update failed final verification")
    return ["OK\tCONFIGURED_UPDATE_SELECTED\t" + matches[0]["version"]]



def dlc_context(rom):
    identity = component(
        "gcm-ps4-base-identity.py",
        "f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438"
    ).identify(rom)
    selector = component(
        "gcm-ps4-dlc-selection-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-space-v01-lookup-v01.py",
        "55ef2ed4a7cc6b53019c25bb716ebf2698e3d1939721d88e2e9de6c8a1bc1884")
    base = Path(identity["metadata_path"])
    store, title, records = selector.catalog(base)
    selector.configuration(store)
    game = store.safe(selector.ADDONS / title)
    states = {}
    for label, record in records.items():
        installed = store.safe(game / label)
        if installed.exists():
            store.verify(base, record["source"], installed)
            states[label] = "ENABLED"
        else:
            states[label] = "DISABLED"
    return selector, base, store, title, records, states

def inventory(rom, emulator, kind):
    if emulator != "shadps4" or kind != "dlc":
        return update_inventory(rom, emulator, kind)
    selector, base, store, title, records, states = dlc_context(rom)
    output = []
    for row in original_inventory(rom, emulator, kind):
        fields = row.split("\t")
        candidate = Path(bytes.fromhex(fields[4]).decode("utf-8"))
        possible = [
            record for record in records.values()
            if Path(record["source"]).name == candidate.name
        ]
        if possible:
            store.safe(candidate)
            digest = file_digest(candidate)
            if any(record["package_sha256"] == digest for record in possible):
                continue
        output.append(row)
    for label, record in sorted(records.items()):
        token = record["source"].encode("utf-8").hex()
        output.append("\t".join((
            "ROW", "AVAILABLE", states[label],
            Path(record["source"]).stem, token)))
    tokens = [row.split("\t")[4] for row in output]
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate DLC inventory token")
    return output

def commit_dlc(args):
    if args.emulator != "shadps4" or args.type != "dlc" or not args.confirm:
        raise ValueError("Confirmed PS4 DLC Apply Changes required")
    if args.selected_detected:
        raise ValueError("Verify and install detected DLC before applying selection")
    tokens = args.selected_available.split(",") if args.selected_available else []
    if len(tokens) != len(set(tokens)):
        raise ValueError("Duplicate DLC selection")
    selector, base, store, title, records, states = dlc_context(args.rom)
    allowed = {
        record["source"].encode("utf-8").hex(): label
        for label, record in records.items()
    }
    if any(token not in allowed for token in tokens):
        raise ValueError("Selected DLC is not a verified installed candidate")
    selected = sorted(allowed[token] for token in tokens)
    result = selector.apply(base, selected)
    selector, base, store, title, records, final = dlc_context(args.rom)
    if sorted(label for label, state in final.items() if state == "ENABLED") != selected:
        raise ValueError("Final DLC selection disagrees")
    return ["OK\tVERIFIED ENABLE={} DISABLE={}".format(
        len(selected), len(records) - len(selected))]



def summary(rom, emulator):
    if emulator != "shadps4":
        raise ValueError("Shared selected-content summary unavailable for this scope")
    store, identity, records = versions(rom)
    selected = selected_digest(store, identity, records)
    if selected is not None:
        matches = [
            record for record in records
            if record["package_sha256"] == selected
        ]
        if len(matches) != 1:
            raise ValueError("Selected update version is ambiguous")
        update = matches[0]["version"] + " ACTIVE"
    else:
        reader = component(
            "gcm-sfo-metadata.py",
            "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41")
        metadata = reader.read(identity["metadata_path"])
        version = metadata["fields"].get("APP_VER")
        update = (version + " (BASE)") if isinstance(version, str) else "UNASSESSED"
    selector, base, dlc_store, title, dlc_records, states = dlc_context(rom)
    enabled = sum(state == "ENABLED" for state in states.values())
    game = dlc_store.safe(selector.ADDONS / title)
    unknown = 0
    if game.exists():
        for path in game.iterdir():
            dlc_store.safe(path)
            if path.is_dir() and path.name not in dlc_records:
                unknown += 1
    dlc = str(enabled) + " ENABLED"
    if unknown:
        dlc += " / " + str(unknown) + " UNASSESSED"
    return ["SUMMARY\t" + update + "\t" + dlc]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", required=True, choices=("shadps4", "rpcs3"))
    parser.add_argument("--type", choices=("update", "dlc"), default="update")
    parser.add_argument("--action", choices=("start", "status"))
    parser.add_argument("--token")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--rom-title-id")
    parser.add_argument("--selected-available")
    parser.add_argument("--selected-detected")
    parser.add_argument("--state")
    args = parser.parse_args()
    if args.mode == "summary":
        output = summary(args.rom, args.emulator)
    elif args.mode == "list":
        output = inventory(args.rom, args.emulator, args.type)
    elif args.mode == "capabilities":
        output = [
            "CAPABILITY\tCOMMIT_AVAILABLE_DLC\t" + ("1" if args.emulator == "shadps4" else "0"),
            "CAPABILITY\tSELECT_UPDATE\t" + ("1" if args.emulator == "shadps4" else "0"),
        ]
    elif args.mode == "install-job":
        if not args.action or not args.token:
            raise ValueError("Installation action and token required")
        output = installation(
            args.action, args.rom, args.emulator, args.type, args.token)
    elif args.mode == "select-update":
        output = select_update(
            args.rom, args.emulator, args.type, args.token, args.confirm)
    elif args.mode == "commit":
        output = commit_dlc(args)
    elif args.mode in ("apply", "toggle", "bulk"):
        raise ValueError("Platform content selection adapter is not connected yet")
    else:
        raise ValueError("Unsupported global content operation")
    for line in output:
        print(line)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERROR\t" + str(exc).replace("\n", " ").replace("\t", " ")[:1200])
        sys.exit(1)
