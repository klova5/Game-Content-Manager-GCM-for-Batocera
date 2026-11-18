import hashlib, importlib.util, io, json, os, sys, tempfile, time, contextlib
from pathlib import Path
BOOT = sys.argv[1]; fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
def run_scenario(t, behaviour):
    t = Path(t); flag = t / 'flag'; flag.write_text('')
    sup = t / 'sup.py'
    sup.write_text('''
import json, os, signal, sys, time
from pathlib import Path
T = Path(%r)
FLAG = T / 'flag'; LOCK = T / 'glock'
def v34_pids(): return [1]
def wrapper_pids(): return [1]
if __name__ == '__main__':
    n = int((T / 'count').read_text()) + 1 if (T / 'count').exists() else 1
    (T / 'count').write_text(str(n))
    plan = %r
    what = plan[min(n, len(plan)) - 1]
    if what == 'killed': os.kill(os.getpid(), signal.SIGTERM); time.sleep(5)
    if what == 'exit2': sys.exit(2)
    if what == 'up':
        (T / 'state.json').write_text(json.dumps({'phase': 'v36_persistent_running', 'supervisor_pid': os.getpid()}))
        time.sleep(30)
''' % (str(t), behaviour))
    spec = importlib.util.spec_from_file_location('bw', BOOT); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    binary = t / 'bin'; binary.write_bytes(b'gcm')
    m.SUP, m.BIN, m.CANCEL, m.LOCK, m.STATE = sup, binary, t / 'cancel', t / 'lock', t / 'state.json'
    m.EXPECTED = hashlib.sha256(b'gcm').hexdigest(); m.SUPERVISOR_LOG = t / 'sup.log'; m.RETRY_PAUSE = 0.2; m.WORKDIR = str(t)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = m.main()
    return code, out.getvalue(), int((t / 'count').read_text())
with tempfile.TemporaryDirectory() as t:
    code, out, n = run_scenario(t, ['killed', 'up'])
    print(out)
    check('a supervisor killed at boot is reported with its signal', 'signal SIGTERM' in out)
    check('the boot worker retries and GCM comes up on the second attempt', code == 0 and n == 2 and 'PASS: GCM v3.6 persistent frontend running' in out)
with tempfile.TemporaryDirectory() as t:
    code, out, n = run_scenario(t, ['exit2'])
    check('three failed starts end with a clear message and the standard frontend kept', code == 1 and n == 3 and 'did not stay up after 3 attempts' in out and 'exit code 2' in out)
with tempfile.TemporaryDirectory() as t:
    code, out, n = run_scenario(t, ['up'])
    check('a normal start passes on the first attempt', code == 0 and n == 1)
if fails: raise SystemExit('FAILED: ' + ', '.join(fails))
print('ALL BOOT WORKER FIXTURES PASS')
