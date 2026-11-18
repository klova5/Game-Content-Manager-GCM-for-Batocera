"""61TI fixtures: stock-frontend fallback check and X cookie lookup."""
import importlib.util, os, subprocess, sys, tempfile, time
from pathlib import Path
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
spec = importlib.util.spec_from_file_location('sup', sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
t = Path(tempfile.mkdtemp())
es = t / 'emulationstation'; es.write_bytes(os.urandom(64)); es.chmod(0o755)
m.STANDARD = es
check('any stock frontend is accepted (no fingerprint)', m.standard_frontend_problem() is None)
es.write_bytes(os.urandom(64))
check('a Batocera update (new file content) is still accepted', m.standard_frontend_problem() is None)
real = Path.read_text
def fake(self, *a, **k):
    if str(self) == '/proc/self/mountinfo':
        return '36 25 0:31 / / rw - overlay overlay rw\n99 36 8:3 /gcm/es %s rw - ext4 /dev/sda3 rw\n' % es
    return real(self, *a, **k)
Path.read_text = fake
check('something mounted over the frontend is refused', 'mounted over' in (m.standard_frontend_problem() or ''))
Path.read_text = real
es.chmod(0o644); check('a non-executable frontend is refused', 'missing' in (m.standard_frontend_problem() or ''))
es.unlink(); check('a missing frontend is refused', 'missing' in (m.standard_frontend_problem() or ''))
cookie = t / '.serverauth.2288'; cookie.write_bytes(b'c')
x = subprocess.Popen(['bash', '-c', 'exec -a /usr/bin/X python3 -c "import time; time.sleep(20)" -auth %s' % cookie])
time.sleep(0.5)
try:
    check('the running X server\'s cookie is found', m.live_x_cookie() == str(cookie))
    good = t / 'good'; good.write_bytes(b'g')
    check('a working XAUTHORITY is kept', m.x_authority(str(good)) == str(good))
    expect = '/.Xauthority' if Path('/.Xauthority').exists() else str(cookie)
    check('a stale XAUTHORITY is replaced (%s)' % expect, m.x_authority('/nonexistent/cookie') == expect)
    check('no XAUTHORITY at all gets a working cookie', m.x_authority(None) == expect)
finally:
    x.kill()
print('ALL V44 FIXTURES PASS' if not fails else 'FAILED: %s' % fails)
sys.exit(1 if fails else 0)
