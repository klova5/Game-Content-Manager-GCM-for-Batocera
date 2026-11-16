#!/usr/bin/env python3
"""GCM v3.6 PS4 native text bridge v01 (READ-ONLY DEVELOPMENT TEST).

Translate a verified PS4 live-discovery-v02 JSON report into the existing
EmulationStation GCM SUMMARY / META / ROW / COUNT wire formats.

Does not replace the original native backends or change emulator configuration.
Does not copy, install, activate, delete, or otherwise modify game content.
The --self-test mode makes no access to Batocera game content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from collections import Counter

BASE = Path(__file__).resolve().parent
DISCOVERY = BASE / 'gcm36_ps4_live_discovery.py'
RUNTIME = BASE
V02_SHA256 = '5798689d4fe38935d84280ca6d94b037a9549d1712b0dc951741b10f5868ecf3'
TITLE_ID = re.compile(r'CUSA[0-9]{5}\Z')


class BridgeError(Exception):
    pass


def safe(value: object) -> str:
    """Keep native tab-delimited output to one logical line per field."""
    return ''.join(c if (ord(c) >= 32 and c not in '\t\r\n\x7f') else ' '
                   for c in str(value if value is not None else ''))[:240]


def validate_report(data: object, expected_type: str) -> tuple[str, list[dict]]:
    """Fail closed if v02's read-only and identity contracts change."""
    if not isinstance(data, dict):
        raise BridgeError('Discovery result is not an object')
    if data.get('mode') != 'read_only_live_header_discovery':
        raise BridgeError('Unexpected discovery mode')
    if data.get('installation_enabled') is not False:
        raise BridgeError('Installation safety contract changed')
    if data.get('selected_type') != expected_type:
        raise BridgeError('Content type selection mismatch')
    title = data.get('game_title_id')
    if not isinstance(title, str) or not TITLE_ID.fullmatch(title):
        raise BridgeError('Invalid game title ID')
    if data.get('game_metadata_category') not in ('gd', 'gp'):
        raise BridgeError('Unverified game metadata category')
    if (not isinstance(data.get('scanned_package_count'), int)
            or data['scanned_package_count'] < 0):
        raise BridgeError('Invalid package scan count')
    counts = data.get('matching_counts')
    candidates = data.get('candidates')
    if not isinstance(counts, dict) or not isinstance(candidates, list):
        raise BridgeError('Missing candidate list or counts')
    if len(candidates) > 10000:
        raise BridgeError('Unexpected number of candidates')
    for kind in ('update', 'dlc'):
        count = counts.get(kind)
        if type(count) is not int or count < 0:
            raise BridgeError('Invalid matching count')
    found = Counter()
    for row in candidates:
        if not isinstance(row, dict):
            raise BridgeError('Invalid candidate')
        kind = row.get('type_hint')
        if kind not in ('update', 'dlc'):
            raise BridgeError('Unverified package type')
        if expected_type != 'all' and kind != expected_type:
            raise BridgeError('Unexpected candidate type')
        content_id = row.get('content_id')
        if (not isinstance(content_id, str) or not content_id.strip()
                or title not in content_id):
            raise BridgeError('Candidate content ID does not match game')
        if (row.get('header_valid') is not True
                or row.get('title_id_match') is not True
                or row.get('installation_verified') is not False
                or row.get('activation_verified') is not False
                or row.get('compatibility_verified') is not False):
            raise BridgeError('Candidate verification status changed')
        found[kind] += 1
    if any(found[kind] != counts[kind] for kind in ('update', 'dlc')):
        raise BridgeError('Candidate counts do not match report')
    return title, candidates


def verify_discovery_script() -> None:
    if (DISCOVERY.is_symlink() or not DISCOVERY.is_file()
            or RUNTIME.is_symlink() or not RUNTIME.is_dir()):
        raise BridgeError('Expected isolated development runtime missing')
    with DISCOVERY.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != V02_SHA256:
        raise BridgeError('Live-discovery v02 checksum mismatch')


def get_report(rom: str, kind: str) -> dict:
    if not Path(rom).is_absolute() or len(rom) > 4096:
        raise BridgeError('Game path must be absolute and reasonable')
    verify_discovery_script()
    env = os.environ.copy()
    env.update({
        'PYTHONPATH': str(RUNTIME),
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONNOUSERSITE': '1',
    })
    cmd = [sys.executable, '-B', str(DISCOVERY), '--rom', rom,
           '--type', kind, '--json']
    try:
        child = subprocess.run(cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace',
                               stdin=subprocess.DEVNULL, timeout=25,
                               cwd=str(BASE), env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BridgeError('Live PS4 discovery timed out') from exc
    if child.returncode != 0:
        detail = safe((child.stderr or child.stdout).strip().splitlines()[-1]
                      if (child.stderr or child.stdout).strip()
                      else 'unknown error')
        raise BridgeError('Live discovery failed: ' + detail)
    try:
        report = json.loads(child.stdout)
    except (ValueError, TypeError) as exc:
        raise BridgeError('Live discovery did not return valid JSON') from exc
    validate_report(report, kind)
    return report


def summary_lines(report: dict) -> list[str]:
    validate_report(report, 'all')
    counts = report['matching_counts']
    labels = []
    for kind in ('update', 'dlc'):
        n = counts[kind]
        label = f'{n} {kind.upper()} CANDIDATE' + ('S' if n != 1 else '')
        if n:
            label += ' - INSTALL UNVERIFIED'
        labels.append(label)
    return ['SUMMARY\t' + '\t'.join(map(safe, labels))]


def row_lines(report: dict, kind: str) -> list[str]:
    title, candidates = validate_report(report, kind)
    rows = Counter((item['type_hint'], item['content_id'])
                   for item in candidates)
    result = [f'META\t{safe(title)}\tshadps4\tVERIFIED']
    for (content_type, content_id), sources in sorted(rows.items()):
        result.append('\t'.join([
            'ROW', content_type,
            content_type.upper() + ' CANDIDATE - INSTALL UNVERIFIED',
            safe(content_id), str(sources),
        ]))
    result.append('COUNT\t' + str(len(rows)))
    return result


def self_test() -> None:
    fixture = {
        'mode': 'read_only_live_header_discovery',
        'installation_enabled': False,
        'game_title_id': 'CUSA08809',
        'game_metadata_category': 'gp',
        'selected_type': 'all',
        'scanned_package_count': 2,
        'matching_counts': {'update': 1, 'dlc': 1},
        'candidates': [
            {'type_hint': kind,
             'content_id': f'EP9000-CUSA08809_00-TEST{kind.upper()}',
             'header_valid': True, 'title_id_match': True,
             'installation_verified': False,
             'activation_verified': False,
             'compatibility_verified': False}
            for kind in ('update', 'dlc')
        ],
    }
    assert summary_lines(fixture) == [
        'SUMMARY\t1 UPDATE CANDIDATE - INSTALL UNVERIFIED'
        '\t1 DLC CANDIDATE - INSTALL UNVERIFIED'
    ]
    fixture['selected_type'] = 'dlc'
    fixture['matching_counts'] = {'update': 0, 'dlc': 1}
    fixture['candidates'] = fixture['candidates'][1:]
    assert row_lines(fixture, 'dlc')[-1] == 'COUNT\t1'
    assert row_lines(fixture, 'dlc')[1].startswith('ROW\tdlc\t')
    fixture['candidates'][0]['installation_verified'] = True
    try:
        row_lines(fixture, 'dlc')
    except BridgeError:
        pass
    else:
        raise AssertionError('Unsafe verification flag accepted')
    print('PASS: Native bridge contract self-test (no game access)')


def main() -> int:
    parser = argparse.ArgumentParser(description='PS4 v3.6 read-only native text bridge')
    parser.add_argument('--mode', choices=('summary', 'rows'), default='summary')
    parser.add_argument('--emulator', choices=('shadps4',), default='shadps4')
    parser.add_argument('--rom', help='Absolute PS4 launcher or extracted game path')
    parser.add_argument('--type', choices=('update', 'dlc'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not args.rom:
        parser.error('--rom is required unless --self-test is selected')
    if args.mode == 'rows' and args.type is None:
        parser.error('--type is required for --mode rows')
    selected = 'all' if args.mode == 'summary' else args.type
    try:
        report = get_report(args.rom, selected)
        lines = summary_lines(report) if args.mode == 'summary' else row_lines(report, selected)
    except (BridgeError, OSError, ValueError, KeyError) as exc:
        print('ERROR\t' + safe(exc))
        return 2
    for line in lines:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
