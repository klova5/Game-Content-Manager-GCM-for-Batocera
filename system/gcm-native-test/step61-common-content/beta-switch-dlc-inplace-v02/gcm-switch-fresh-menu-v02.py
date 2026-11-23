"""Switch menu route: refresh a stale Ryujinx/Citron snapshot (reusing unchanged proofs), then run
the unchanged pinned GCM menu. A refresh failure is left for the menu itself to report."""
import hashlib, importlib.util, runpy, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPLACE = (HERE / 'gcm-switch-dlc-inplace-v02.py', '3561294a91c2b7740c72aa1b3ed03d0fa87fedffeebf74e8f73e389976b8d072')
MENU = (HERE.parent / 'gcm-global-menu-existing-v01.py', '6f0840ec42e4e2445804df30e4f6e145f807560cb992d2894a9696db2c411a47')


def pinned(path, expected):
    if any(p.is_symlink() for p in (path, *path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit('ERROR\tSwitch menu route dependency changed: ' + path.name)
    return path


def argument(name):
    args = sys.argv[1:]
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


SWITCH_UPDATE = Path('/userdata/roms/switch_update')


def bind_backup_drive(media=Path('/media'), target=SWITCH_UPDATE, run=None, mounts='/proc/mounts'):
    """61TO: a connected drive with roms/switch_update (e.g. the GAMES backup HDD) is shown at
    /userdata/roms/switch_update, so its updates and DLC are detected; when it is gone the internal
    folder is used again. Returns a short note, or None when nothing changed."""
    import os, subprocess
    run = run or (lambda *cmd: subprocess.run(cmd, capture_output=True).returncode == 0)
    if target.is_symlink() or not target.is_dir():
        return None
    mounted = {}
    with open(mounts) as f:
        for line in f:
            parts = line.split()
            if len(parts) > 1:
                mounted[parts[1].replace('\\040', ' ')] = parts[0]
    source_ok = lambda p: p.is_dir() and not p.is_symlink()
    if str(target) in mounted:
        try:
            if source_ok(target / 'dlc') or source_ok(target / 'update') or any(target.iterdir()):
                return None
        except OSError:
            pass  # the drive behind the folder was unplugged
        run('umount', '-l', str(target))
        return 'backup drive removed: using the internal switch_update folder'
    try:
        drives = sorted(d for d in media.iterdir() if d.is_dir() and not d.is_symlink() and str(d) in mounted)
    except OSError:
        return None
    for d in drives:
        source = d / 'roms' / 'switch_update'
        if source_ok(source) and str(source) != str(target):
            if run('mount', '--bind', str(source), str(target)):
                return 'backup drive %s: its switch_update folder is now detected' % d.name
    return None


CITRON_CONFIG = Path('/userdata/system/configs/yuzu/qt-config.ini')


def ensure_content_folders(config=CITRON_CONFIG, home=Path('/userdata')):
    """61TO: Citron's listed content folders must exist for GCM to read them. A missing folder on the
    internal drive (e.g. the launcher's switch_update/dlc) is created empty; one elsewhere (a drive
    that is not connected) cannot be, and is named. Returns the folders that are still missing."""
    import re
    try:
        text = config.read_text(errors='replace')
    except OSError:
        return []
    missing = []
    for m in re.finditer(r'^Paths\\external_content_dirs\\\d+\\path=(.*)$', text, re.M):
        p = Path(m.group(1).strip())
        if not p.is_absolute() or p.is_dir():
            continue
        chain = [p, *p.parents]
        inside = home in chain and not any(q.is_symlink() for q in chain if q != Path('/'))
        if inside:
            try:
                p.mkdir(parents=True, exist_ok=True)
                continue
            except OSError:
                pass
        missing.append(str(p))
    return missing


if __name__ == '__main__':
    try:
        note = bind_backup_drive()
        if note:
            print('NOTE\t' + note, file=sys.stderr, flush=True)
        for folder in ensure_content_folders():
            print('WARNING\tCitron content folder not available: ' + folder, file=sys.stderr, flush=True)
    except Exception:
        pass
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
