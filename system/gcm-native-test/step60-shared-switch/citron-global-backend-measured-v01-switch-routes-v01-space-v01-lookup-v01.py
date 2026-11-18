
"""Global Citron menu backend using shared Switch content services."""
import argparse
import contextlib
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "citron-global-inventory-v02-measured-v01-switch-routes-v01-space-v01.py": "574d0b2afea09d762f14e3ed36b40a171465b4406c3d8a17ab88b20d033cde9f",
    "citron-installed-rows-measured-v01.py": "08ad3056382bfb3d6cc0c511a0bed39e018252b8443a037fd9fad69a3a8317ee",
    "citron-content-plan.py": "969947f8fbd844bc07728d4a235a9e7bb30bc7592fd6681eb47054cc3de185ff",
    "citron-configuration-transaction-measured-v01.py": "ff28cb61e26de41cc0906c362a753b6337e3a643fdb1d8cbbb882d57e3de7c81",
    "citron-installed-integrity.py": "987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc",
    "gcm-switch-content-engine-measured-v01.py": "7120a24469daa2e31316235a79989060777f92701628ce3711ae2b67d9b262c4",
}

_GCM_DECLARED_CODE_NAMES = {'citron-global-inventory-v02-measured-v01-switch-routes-v01.py': 'citron-global-inventory-v02-measured-v01-switch-routes-v01-space-v01.py'}

def load(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def action(args):
    if not args.confirm:
        raise ValueError("Explicit confirmation required")
    native = load("citron-installed-rows-measured-v01.py")
    planner = load("citron-content-plan.py")
    transaction = load("citron-configuration-transaction-measured-v01.py")
    integrity = load("citron-installed-integrity.py")
    engine = load("gcm-switch-content-engine-measured-v01.py")
    kind = "update" if args.mode == "select-update" else "dlc"
    if args.type != kind:
        raise ValueError("Operation/content type mismatch")
    before_hash = hashlib.sha256(native.CONFIG.read_bytes()).hexdigest()
    base_identity = engine.identity(Path(args.rom))
    application, records = native.records(args.rom, kind)
    if args.rom_title_id and args.rom_title_id.upper() != application:
        raise ValueError("Supplied title ID differs from internal game identity")
    inventory = integrity.validate()
    old = native.disabled_keys(application)

    def check_inventory():
        if engine.identity(Path(args.rom)) != base_identity:
            raise ValueError("Selected base game changed")
        if integrity.validate() != inventory:
            raise ValueError("Verified installed inventory changed")

    if kind == "update":
        selected = [item for item in records
                    if native.update_token(item) == args.token]
        if len(selected) != 1:
            raise ValueError("Selected update not in this game's verified installed inventory")
        selected = selected[0]
        title = format(int(application, 16) + 0x800, "016X")
        title_disabled = native.disabled_keys(title)
        desired = planner.plan_update(
            records, selected, old, title_disabled, inventory_complete=True)
        def check_result():
            actual = planner.resolver().resolve(
                records, native.disabled_keys(application),
                native.disabled_keys(title), inventory_complete=True)
            if actual != selected:
                raise ValueError("Configured update selection failed post-check")
        operation = "select-update"
    else:
        if args.selected_detected:
            raise ValueError("Detected DLC must be verified and installed before enabling")
        requested = args.selected_available.split(",") if args.selected_available else []
        if len(requested) != len(set(requested)):
            raise ValueError("Duplicate selected DLC token")
        allowed = {
            "citron:dlc:" + application + ":" + item["title_id"]: item["title_id"]
            for item in records
        }
        if any(token not in allowed for token in requested):
            raise ValueError("Selected DLC not in this game's verified installed inventory")
        wanted = {allowed[token] for token in requested}
        if not records:
            return ["OK\tNO DLC CHANGES"]
        desired = planner.plan_dlc(records, wanted, old, inventory_complete=True)
        def check_result():
            disabled = set(native.disabled_keys(application))
            actual = {item["title_id"] for item in records
                      if "DLC" not in disabled
                      and planner.dlc_key(item["title_id"]) not in disabled}
            if actual != wanted:
                raise ValueError("Configured DLC state failed post-check")
        operation = "set-dlc"

    result = transaction.commit(
        application, desired, before_hash,
        validate_inventory=check_inventory, validate_result=check_result,
        operation=operation)
    if kind == "update":
        output = ["OK\tCONFIGURED_UPDATE_SELECTED"]
        if result["status"] == "ALREADY_CONFIGURED":
            output.append("STATUS\tALREADY_SELECTED")
    else:
        total = len({item["title_id"] for item in records})
        output = ["OK\tVERIFIED ENABLE={} DISABLE={} IMPORT=0".format(
            len(wanted), total - len(wanted))]
    if result["transaction"]:
        output.append("BACKUP\t" + result["transaction"])
    return output

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--emulator", choices=("citron", "citron-emu"), required=True)
    parser.add_argument("--mode",
        choices=("summary", "list", "capabilities", "select-update", "commit"),
        required=True)
    parser.add_argument("--type", choices=("update", "dlc"))
    parser.add_argument("--token")
    parser.add_argument("--rom-title-id", default="")
    parser.add_argument("--selected-available", default="")
    parser.add_argument("--selected-detected", default="")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    with contextlib.redirect_stdout(sys.stderr):
        if args.mode in ("select-update", "commit"):
            output = action(args)
        elif args.mode == "capabilities":
            output = [
                "CAPABILITY\tLIST_UPDATES\t1", "CAPABILITY\tLIST_DLC\t1",
                "CAPABILITY\tCOMMIT_AVAILABLE_DLC\t1",
                "CAPABILITY\tACTIVATE_UPDATE\t1",
                "CAPABILITY\tSCAN\t0", "CAPABILITY\tINSTALL\t0",
                "CAPABILITY\tCOMPLETE_INVENTORY\t0",
            ]
        else:
            inventory = load("citron-global-inventory-v02-measured-v01-switch-routes-v01-space-v01.py")
            if args.mode == "summary":
                output = [inventory.summary(args.rom)]
            else:
                if not args.type:
                    raise ValueError("--type required")
                output = inventory.query(args.rom, args.type)
    for line in output:
        print(line)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("ERROR\t" + message[:400])
        sys.exit(1)
