"""GCM v3.6 experimental per-game shadPS4 candidate discovery, read-only.

Only title-ID/header matching; no installation or activation determination.
"""
import argparse
import json
import os
import tomllib
from pathlib import Path

from gcm36_ps4_launcher_resolver import resolve_ps4_game_path
from shadps4_metadata_matcher import identity, parse_sfo
from shadps4_package_probe import inspect_header

CONFIG = Path('/userdata/system/configs/shadps4/user/config.toml')
SIDE_DIRS = ('updates', 'update', 'patches', 'DLC', 'dlc')


def configured_locations():
    """Read only installDirs and addonInstallDir from the emulator config."""
    with CONFIG.open('rb') as stream:
        document = tomllib.load(stream)
    games, addons = set(), set()

    def visit(value):
        if not isinstance(value, dict):
            return
        dirs = value.get('installDirs')
        if isinstance(dirs, list):
            flags = value.get('installDirsEnabled', [])
            for i, entry in enumerate(dirs):
                enabled = flags[i] if isinstance(flags, list) and i < len(flags) else True
                if enabled is True and isinstance(entry, str) and entry:
                    games.add(Path(entry))
        addon = value.get('addonInstallDir')
        if isinstance(addon, str) and addon:
            addons.add(Path(addon))
        for child in value.values():
            visit(child)

    visit(document)
    if not games:
        raise ValueError('No enabled shadPS4 game installation directory in config')
    for path in games | addons:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError('Unsafe or non-absolute configured path: ' + str(path))
    return sorted(games), sorted(addons)


def packages_under(folder):
    """Only regular PKG files; never follow directory or file symlinks."""
    if folder.is_symlink() or not folder.is_dir():
        return
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(current) / d).is_symlink()]
        for name in files:
            path = Path(current) / name
            if name.lower().endswith('.pkg') and not path.is_symlink() and path.is_file():
                yield path


def main():
    ap = argparse.ArgumentParser(description='Read-only PS4 per-game package discovery')
    ap.add_argument('--rom', required=True, help='Absolute game launcher or CUSA directory')
    ap.add_argument('--type', choices=('update', 'dlc', 'all'), default='all')
    ap.add_argument('--json', action='store_true', help='Structured read-only report')
    args = ap.parse_args()

    game = resolve_ps4_game_path(args.rom)
    sfo = game / 'sce_sys' / 'param.sfo'
    info = identity(parse_sfo(sfo.read_bytes()))
    title = info['title_id']
    if info['identity_conflict'] or title != game.name.upper():
        raise ValueError('Game title identity conflict')

    installed_dirs, addon_dirs = configured_locations()
    search_dirs = set()
    for root in installed_dirs:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            for name in SIDE_DIRS:
                candidate = child / name
                if candidate.is_dir() and not candidate.is_symlink():
                    search_dirs.add(candidate)
    search_dirs.update(p for p in addon_dirs if p.is_dir() and not p.is_symlink())

    paths = set()
    for folder in sorted(search_dirs):
        paths.update(packages_under(folder))

    candidates, excluded, unverified = [], 0, 0
    for path in sorted(paths):
        header = inspect_header(path)
        kind = header.get('directory_content_hint')
        embedded = header.get('embedded_title_id')
        if not header.get('header_valid') or header.get('filename_conflict') or not embedded:
            unverified += 1
        elif embedded != title:
            excluded += 1
        elif kind not in ('update', 'dlc'):
            unverified += 1
        elif args.type in ('all', kind):
            candidates.append({
                'source': str(path),
                'content_id': header.get('content_id'),
                'type_hint': kind,
                'header_valid': True,
                'title_id_match': True,
                'installation_verified': False,
                'activation_verified': False,
                'compatibility_verified': False,
            })

    counts = {kind: sum(row['type_hint'] == kind for row in candidates)
              for kind in ('update', 'dlc')}
    report = {
        'mode': 'read_only_live_header_discovery',
        'installation_enabled': False,
        'game_title_id': title,
        'game_title': info.get('title'),
        'game_metadata_category': info.get('category'),
        'game_metadata_version': info.get('version'),
        'selected_type': args.type,
        'configured_game_dirs': [str(p) for p in installed_dirs],
        'configured_addon_dirs': [str(p) for p in addon_dirs],
        'scanned_package_count': len(paths),
        'matching_counts': counts,
        'excluded_different_title': excluded,
        'unverified_headers': unverified,
        'candidates': candidates,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print('===== GCM v3.6 PS4 PER-GAME DISCOVERY V02 =====')
        print('GAME:', title, '|', info.get('title'))
        print('GAME METADATA:', info.get('category'), info.get('version'))
        print('CONFIGURED GAME DIRECTORIES:', ', '.join(map(str, installed_dirs)))
        print('CONFIGURED DLC DIRECTORY:', ', '.join(map(str, addon_dirs)))
        print('PACKAGES EXAMINED:', len(paths))
        print('MATCHING UPDATE CANDIDATES:', counts['update'])
        print('MATCHING DLC CANDIDATES:', counts['dlc'])
        print('EXCLUDED DIFFERENT TITLE:', excluded)
        print('UNVERIFIED HEADERS:', unverified)
        for row in candidates:
            print(' ', row['type_hint'].upper(), '|', row['content_id'], '|', row['source'])
        print('INSTALLATION, ACTIVATION, COMPATIBILITY: UNVERIFIED')
        print('NO FILES MODIFIED')


if __name__ == '__main__':
    main()
