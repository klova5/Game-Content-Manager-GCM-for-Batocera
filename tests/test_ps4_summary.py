"""61TJ fixtures: PS4 summary says ASSESSING / CHECK FAILED / NOT RECOGNISED."""
import importlib.util, json, sys, tempfile, types
from pathlib import Path
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
spec = importlib.util.spec_from_file_location('menu', sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
t = Path(tempfile.mkdtemp()); m.ROOT = t; m.GAMES = t / 'games'
(t / 'games/DLC/CUSA1/extra').mkdir(parents=True)
queued = []
m.queue = lambda rom: queued.append(rom)
evidence = types.SimpleNamespace(safe=lambda p: Path(p), boot=lambda: 'BOOT')
identity = types.SimpleNamespace(identify=lambda rom: {'metadata_path': '/roms/' + rom})
sfo = types.SimpleNamespace(read=lambda base: {'fields': {'APP_VER': '01.00'}})
m.load = lambda k: {'evidence': evidence, 'identity': identity, 'sfo': sfo}[k]
ok = {'update': True, 'dlc': True}
dlc = {'a': ({'entitlement_label': 'known'}, 'ENABLED')}
m.states = lambda rom, kind, assess=False: (evidence, {'application_id': 'CUSA1'}, 'base', {} if kind == 'update' else dlc, ok[kind])
def status(s):
    import hashlib
    key = hashlib.sha256(('/roms/g').encode()).hexdigest()
    d = t / 'state/global-menu-assessments'; d.mkdir(parents=True, exist_ok=True)
    (d / (key + '.json')).write_text(json.dumps({'status': s, 'boot_id': 'BOOT'}))
ok.update(update=False, dlc=False)
line = m.summary('g')[0]
check('unchecked game shows ASSESSING for both (%s)' % line, line == 'SUMMARY\tASSESSING\tASSESSING / 1 NOT RECOGNISED')
check('...and starts the background check', queued == ['g'])
status('ERROR'); queued.clear(); line = m.summary('g')[0]
check('a failed check shows CHECK FAILED (%s)' % line, line.startswith('SUMMARY\tCHECK FAILED\tCHECK FAILED'))
check('...and is not restarted by the summary (no refresh loop)', queued == [])
status('RUNNING'); queued.clear()
check('a running check shows ASSESSING', m.summary('g')[0].startswith('SUMMARY\tASSESSING'))
ok.update(update=True, dlc=True); status('PASS'); queued.clear(); line = m.summary('g')[0]
check('a finished check shows the result (%s)' % line, line == 'SUMMARY\t01.00 (BASE)\t1 ENABLED / 1 NOT RECOGNISED')
check('...and starts nothing', queued == [])
check('the word UNASSESSED no longer appears', 'UNASSESSED' not in Path(sys.argv[1]).read_text().split('def summary', 1)[1].split('def main', 1)[0])
print('ALL MENU FIXTURES PASS' if not fails else 'FAILED: %s' % fails)
sys.exit(1 if fails else 0)
