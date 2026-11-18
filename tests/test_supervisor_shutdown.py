import importlib.util, os, signal, subprocess, sys, tempfile, time, fcntl
from pathlib import Path
SUP = sys.argv[1]; fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
def alive(pid):
    try: return Path('/proc/%d/stat' % pid).read_text().split()[2] != 'Z'
    except OSError: return False
def pids_with(arg):
    out = []
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try: args = (p / 'cmdline').read_bytes().split(b'\0')
            except OSError: continue
            if arg.encode() in args and alive(int(p.name)): out.append(int(p.name))
    return out
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    spec = importlib.util.spec_from_file_location('sup', SUP); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.LOG = t / 'sup.log'; m.STOP = t / 'stop'
    front = t / 'emulationstation-gcm-v36'
    # stand-in GCM frontend: starts a "reboot helper" (stays in its process group), then keeps running
    front.write_text('#!/bin/sh\nsleep 300 &\necho $! > "$1"\nwhile :; do sleep 1; done\n'); front.chmod(0o755); m.BIN = front
    helper_file = t / 'helper.pid'
    child = subprocess.Popen(['sh', '-c', '"%s" "%s"; :' % (front, helper_file)], start_new_session=True)
    for _ in range(50):
        if helper_file.exists() and pids_with(str(front)): break
        time.sleep(0.1)
    helper = int(helper_file.read_text())
    check('stand-in frontend and helper run in one process group', pids_with(str(front)) and os.getpgid(helper) == os.getpgid(child.pid))
    start = time.time(); m.terminate_test_session(child); took = time.time() - start
    check('the GCM frontend is stopped', not pids_with(str(front)))
    check('the session wrapper is stopped', child.poll() is not None)
    check('a reboot helper started by the frontend keeps running', alive(helper))
    check('stopping is prompt (%.1f s)' % took, took < 5)
    if alive(helper): os.kill(helper, signal.SIGKILL)
    if hasattr(m, 'frontend_pids'):
        m.LOCK = t / 'lock'; held = open(m.LOCK, 'a+'); fcntl.flock(held, fcntl.LOCK_EX)
        subprocess.run([sys.executable, '-c', '''
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("sup", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.LOG = Path(sys.argv[2]); m.LOCK = Path(sys.argv[3]); print(m.supervise())''', SUP, str(t / 'run.log'), str(m.LOCK)], capture_output=True, text=True, timeout=30)
        text = (t / 'run.log').read_text() if (t / 'run.log').exists() else ''
        check('supervisor logs its start first', 'starting (parent' in text and 'Another GCM graphical supervisor' in text)
if fails: raise SystemExit('FAILED: ' + ', '.join(fails))
print('ALL SUPERVISOR FIXTURES PASS')
