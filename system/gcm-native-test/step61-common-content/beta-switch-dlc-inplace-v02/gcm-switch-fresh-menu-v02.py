"""Switch menu route: refresh a stale Ryujinx/Citron snapshot (reusing unchanged proofs), then run
the unchanged pinned GCM menu. A refresh failure is left for the menu itself to report."""
import hashlib, importlib.util, runpy, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPLACE = (HERE / 'gcm-switch-dlc-inplace-v02.py', '1bde68699c69213949604e45907195500010aae4561fa95ae725baa646447f6a')
MENU = (HERE.parent / 'gcm-global-menu-existing-v01.py', 'ceded0b0b6376fa584ccc69691cbaa6e435c2de98ce4728c7e60eaa1b69fae73')


def pinned(path, expected):
    if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('ERROR\tSwitch menu route dependency changed: ' + path.name)
    return path


def argument(name):
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


if __name__ == '__main__':
    menu = pinned(*MENU)
    rom, emulator = argument('--rom'), argument('--emulator')
    if rom and emulator in ('ryujinx', 'ryujinx-emu', 'citron', 'citron-emu'):
        try:
            spec = importlib.util.spec_from_file_location('gcm_switch_inplace', pinned(*INPLACE))
            inplace = importlib.util.module_from_spec(spec); spec.loader.exec_module(inplace)
            inplace.fresh(rom, emulator, progress=lambda *a: None)
        except Exception:
            pass
    sys.argv = [str(menu)] + sys.argv[1:]
    runpy.run_path(str(menu), run_name='__main__')
