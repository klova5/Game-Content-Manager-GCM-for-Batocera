#!/usr/bin/env python3
"""61TE: what Uninstall leaves behind, and (later) its permanent cleanup.

  python3 gcm-uninstall-cleanup-v01.py plan            short preview; nothing is changed (full list in the log folder)
  python3 gcm-uninstall-cleanup-v01.py plan --full     full preview
  python3 gcm-uninstall-cleanup-v01.py apply --confirm permanently delete what uninstalls set aside and remove their records

Uninstall tools (updates, Switch DLC, PS4 DLC) move removed content into hidden .gcm-uninstalled
folders and keep a record so it can be restored; those records are the Retained/Restore entries.
The cleanup permanently deletes what was set aside and removes the record. It never deletes an
original package (anything on an external drive, any package file outside GCM storage, or any path
an uninstall recorded as preserved), and it keeps a shared Switch copy while the other emulator
still uses it.
"""
import json, os, sys
from pathlib import Path

COMMON = Path(os.environ.get('GCM_COMMON', '/userdata/system/gcm-native-test/step61-common-content'))
GAME_CONTENT = Path(os.environ.get('GCM_GAME_CONTENT', '/userdata/system/game-content'))
JOURNALS = {'update': 'state/global-update-uninstall/transactions', 'switch-dlc': 'state/switch-dlc-uninstall-transactions',
            'ps4-dlc': 'state/dlc-uninstall-transactions'}
PACKAGES = ('.pkg', '.nsp', '.nsz', '.xci', '.xcz', '.cia', '.iso', '.rar', '.zip', '.7z')
SWITCH_STORE = (GAME_CONTENT / 'switch' / 'updates', GAME_CONTENT / 'switch' / 'dlc')
EXTERNAL = ('/media/',)


def size(p):
    p = Path(p)
    try:
        if p.is_symlink() or p.is_file():
            return p.lstat().st_size
        return sum(f.lstat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())
    except OSError:
        return 0


def files_in(p):
    p = Path(p)
    if p.is_file() or p.is_symlink():
        return [p]
    return [f for f in p.rglob('*') if f.is_file() or f.is_symlink()] if p.is_dir() else []


def preserved_paths(doc):
    meta = doc.get('metadata', {})
    out = set(meta.get('packages_preserved') or [])
    return {str(x) for x in out}


def classify(move, preserved):
    src, q = str(move.get('source', '')), str(move.get('quarantine', ''))
    if not q or not os.path.lexists(q):
        return 'GONE', 'already gone'
    if src.startswith(EXTERNAL) or q.startswith(EXTERNAL):
        return 'KEEP', 'on an external drive (original)'
    for f in files_in(q):
        rel = os.path.relpath(f, q)
        original = src if rel == '.' else os.path.join(src, rel)
        if original in preserved or str(f) in preserved:
            return 'KEEP', 'recorded as a preserved original package'
        inside_gcm = Path(original).is_relative_to(GAME_CONTENT)
        if f.suffix.lower() in PACKAGES and not inside_gcm:
            return 'KEEP', 'package file outside GCM storage (original)'
    if any(Path(src).is_relative_to(s) for s in SWITCH_STORE):
        return 'SHARED', 'shared Switch copy (kept while the other emulator uses it)'
    return 'DELETE', 'set aside by uninstall'


def gcm_copies(move):
    """GCM package copies named by records that the uninstall set aside (PS4 installed records)."""
    out = []
    for f in files_in(move.get('quarantine', '')):
        if f.suffix != '.json' or f.stat().st_size > (4 << 20):
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        source = doc.get('source') if isinstance(doc, dict) else None
        if isinstance(source, str) and source not in LIVE and Path(source).is_relative_to(GAME_CONTENT) and os.path.isfile(source) \
                and Path(source).suffix.lower() in PACKAGES and not any(Path(source).is_relative_to(s) for s in SWITCH_STORE):
            out.append(source)
    return out


LIVE = set()


def scan():
    LIVE.clear(); LIVE.update(live_sources())
    plan = []
    for kind, rel in JOURNALS.items():
        folder = COMMON / rel
        for journal in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            try:
                doc = json.loads(journal.read_text())
            except (OSError, ValueError) as exc:
                plan.append({'kind': kind, 'journal': str(journal), 'status': 'UNREADABLE', 'items': [], 'error': str(exc)})
                continue
            meta = doc.get('metadata', {})
            entry = {'kind': kind, 'journal': str(journal), 'status': doc.get('status'), 'emulator': meta.get('emulator'),
                     'game': Path(str(meta.get('rom') or meta.get('application_id') or '')).name, 'labels': meta.get('labels'), 'items': []}
            if doc.get('status') == 'COMPLETE':
                preserved = preserved_paths(doc)
                for move in doc.get('moves', []):
                    action, why = classify(move, preserved)
                    entry['items'].append({'action': action, 'why': why, 'held': move.get('quarantine'), 'from': move.get('source'),
                                           'bytes': size(move.get('quarantine', '')) if action != 'GONE' else 0})
                    for copy in gcm_copies(move) if action == 'DELETE' else []:
                        entry['items'].append({'action': 'DELETE', 'why': 'GCM package copy of the uninstalled content', 'held': copy,
                                               'from': copy, 'bytes': size(copy)})
            plan.append(entry)
    return plan


def show(plan):
    total = 0
    for e in plan:
        if e['status'] == 'ROLLED_BACK':
            print('UNDONE   %-10s %s (nothing set aside; record would be removed)' % (e['kind'], Path(e['journal']).stem[:12]))
            continue
        print('RECORD   %-10s %s %s %s %s -> record removed (no Retained/Restore entry)' % (
            e['kind'], Path(e['journal']).stem[:12], e.get('status'), e.get('emulator'), (e.get('labels') or e.get('game') or '')))
        for i in e['items']:
            total += i['bytes'] if i['action'] == 'DELETE' else 0
            print('   %-7s %12s  %s' % (i['action'], format(i['bytes'], ','), i['held']))
            print('           why: %s; from %s' % (i['why'], i['from']))
    print('TOTAL THAT WOULD BE DELETED: %s bytes' % format(total, ','))
    print('PREVIEW ONLY: nothing was changed')


HOLDING = ('.gcm-uninstalled', '.gcm-uninstalled-updates', 'uninstalled-dlc')
FINISHED = 'state/uninstall-cleanup/finished'
LOG = Path(os.environ.get('GCM_CLEANUP_LOG', '/userdata/system/logs/gcm-uninstall-cleanup.log'))


def held_by(journal_id, path):
    """True only for a path inside this uninstall's own holding folder (<holding>/<journal id>/...)."""
    parts = Path(path).parts
    return any(parts[i] in HOLDING and parts[i + 1] == journal_id and len(parts) > i + 2 for i in range(len(parts) - 2))


def live_sources():
    """Package paths still named by current (not set-aside) GCM install records."""
    out = set()
    for f in GAME_CONTENT.glob('*/*/installed-*/*.json') if GAME_CONTENT.is_dir() else []:
        if any(part in HOLDING for part in f.parts):
            continue
        try:
            doc = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get('source'), str):
            out.add(doc['source'])
    return out


def remove(path):
    import shutil
    p = Path(path)
    if p.is_symlink() or p.is_file():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)


def log(*a):
    import time
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + ' '.join(map(str, a))
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a') as f:
            f.write(line + '\n')
    except OSError:
        pass


def busy():
    for comm in Path('/proc').glob('[0-9]*/comm'):
        try:
            name = comm.read_text().strip().casefold()
        except OSError:
            continue
        if name.startswith(('rpcs3', 'cemu', 'vita3k', 'shadps4', 'ryujinx', 'citron', 'yuzu', 'eden', 'azahar', 'citra', 'xenia', 'dolphin', 'ppsspp')):
            return 'close the running emulator first (%s)' % name
    for folder in ('global-content-actions', 'global-install-locally-jobs', 'global-uninstall-jobs', 'beta-native-folder-jobs',
                   'platform-version-install-jobs', 'platform-dlc-install-jobs'):
        for f in (COMMON / 'state' / folder).rglob('*.json') if (COMMON / 'state' / folder).is_dir() else []:
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and (d.get('status') == 'RUNNING' or d.get('recovery_required')):
                return 'another content operation is running or needs recovery'
    for kind, rel in JOURNALS.items():
        for f in (COMMON / rel).glob('*.json') if (COMMON / rel).is_dir() else []:
            try:
                status = json.loads(f.read_text()).get('status')
            except (OSError, ValueError):
                return 'an uninstall record cannot be read: ' + str(f)
            if status not in ('COMPLETE', 'ROLLED_BACK'):
                return 'an uninstall is unfinished: ' + str(f)
    return ''


def apply():
    import fcntl
    lock = COMMON / 'state/global-content-actions/.start.lock'
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        why = busy()
        if why:
            print('STOP: ' + why + '; nothing was deleted'); return 1
        live = live_sources()
        deleted = kept = files = 0
        freed = 0
        for e in scan():
            journal = Path(e['journal']); jid = journal.stem
            if e['status'] not in ('COMPLETE', 'ROLLED_BACK'):
                continue
            keep_record = False
            for i in e['items']:
                if i['action'] == 'GONE':
                    continue
                if i['action'] == 'KEEP' and held_by(jid, i['held']) and not os.path.lexists(i['from']) \
                        and not any(q.is_symlink() for q in Path(i['from']).parents) and Path(i['from']).parent.is_dir():
                    os.rename(i['held'], i['from'])  # an original never stays set aside: put it back
                    log('ORIGINAL PUT BACK', i['from'])
                    continue
                if i['action'] != 'DELETE':
                    keep_record = True; kept += 1
                    log('KEPT', i['action'], i['held'], '-', i['why'])
                    continue
                held = i['held']
                copy = i['why'].startswith('GCM package copy')
                if copy and held in live:
                    log('IN USE BY ANOTHER INSTALL, NOT DELETED', held)
                    continue
                safe = (copy and Path(held).is_relative_to(GAME_CONTENT) and os.path.isfile(held) and held not in live) or \
                       (not copy and held_by(jid, held))
                if not safe or held.startswith(EXTERNAL) or any(q.is_symlink() for q in Path(held).parents):
                    keep_record = True; kept += 1
                    log('KEPT (failed a safety check)', held)
                    continue
                n = size(held)
                remove(held); deleted += 1; freed += n
                log('DELETED', format(n, ','), 'bytes', held)
            # tidy this uninstall's now-empty holding folders
            for move in json.loads(journal.read_text()).get('moves', []) if journal.exists() else []:
                q = Path(str(move.get('quarantine', '')))
                for parent in q.parents:
                    if parent.name in HOLDING:
                        break
                    if parent.name == jid and parent.parent.name in HOLDING:
                        for d in (parent, parent.parent):
                            try:
                                d.rmdir()
                            except OSError:
                                pass
                        break
            if keep_record:
                log('RECORD KEPT (something was kept):', journal)
                continue
            dest = COMMON / FINISHED / (e['kind'] + '-' + journal.name)
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.replace(journal, dest)
            files += 1
            log('RECORD REMOVED', journal.name, '(archived in %s)' % dest.parent)
        print('PASS: uninstall cleanup deleted %d items (%s MB) and removed %d records; %d items kept' % (
            deleted, round(freed / 1048576, 1), files, kept))
        return 0
    finally:
        os.close(fd)


def summary(plan, report):
    """One line per uninstall; the full list goes to the report file."""
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        show(plan)
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(buf.getvalue())
    totals = {'DELETE': [0, 0], 'KEEP': [0, 0], 'SHARED': [0, 0], 'GONE': [0, 0]}
    undone = 0; shown = 0; hidden = 0
    for e in plan:
        if e['status'] != 'COMPLETE':
            undone += 1
            continue
        c = {k: [0, 0] for k in totals}
        for i in e['items']:
            c[i['action']][0] += 1; c[i['action']][1] += i['bytes']
            totals[i['action']][0] += 1; totals[i['action']][1] += i['bytes']
        if shown >= 20:
            hidden += 1
            continue
        shown += 1
        label = e.get('labels') or e.get('game') or ''
        label = (label[0] if isinstance(label, list) and label else str(label))[:45]
        print('%-10s %-8s %-10s %-45s delete %d (%s MB)  keep %d  shared %d' % (
            e['kind'], Path(e['journal']).stem[:8], e.get('emulator') or '', label,
            c['DELETE'][0], round(c['DELETE'][1] / 1048576, 1), c['KEEP'][0], c['SHARED'][0]))
    if hidden:
        print('... and %d more uninstalls (see the full list)' % hidden)
    print('UNDONE UNINSTALLS (nothing set aside):', undone)
    print('TOTAL: delete %d items (%s MB), keep %d originals, %d shared Switch copies' % (
        totals['DELETE'][0], round(totals['DELETE'][1] / 1048576, 1), totals['KEEP'][0], totals['SHARED'][0]))
    kept = [i for e in plan for i in e['items'] if i['action'] in ('KEEP', 'SHARED')]
    for i in kept[:8]:
        print('  %s: %s (%s)' % (i['action'], i['held'][-90:], i['why']))
    if len(kept) > 8:
        print('  ... and %d more kept items (see the full list)' % (len(kept) - 8))
    print('FULL LIST:', report)
    print('PREVIEW ONLY: nothing was changed')


if __name__ == '__main__':
    if sys.argv[1:] == ['apply', '--confirm']:
        if os.geteuid() != 0 and not os.environ.get('GCM_COMMON'):
            raise SystemExit('run as root')
        raise SystemExit(apply())
    if sys.argv[1:2] == ['plan'] and sys.argv[2:] in ([], ['--full']):
        show(scan()) if sys.argv[2:] else summary(scan(), os.environ.get('GCM_CLEANUP_REPORT', '/userdata/system/logs/gcm-uninstall-cleanup-preview.txt'))
    else:
        print(__doc__); raise SystemExit(2)
