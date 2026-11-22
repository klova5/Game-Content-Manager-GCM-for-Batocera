"""x86-64-v3 (Wayland/LabWC) fixtures: the frontend inherits the stock ES Wayland session;
the x86_64 (Xorg) image behaves exactly as before."""
import importlib.util, os, subprocess, sys, tempfile, time
from pathlib import Path
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
spec = importlib.util.spec_from_file_location('sup', sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
t = Path(tempfile.mkdtemp())
board = t / 'batocera.board'; m.BOARD_FILE = board
for k in ('WAYLAND_DISPLAY', 'DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'QT_QPA_PLATFORM', 'SDL_VIDEO_DRIVER'):
    os.environ.pop(k, None)

def es_with(env):
    """A stand-in 'stock ES' process whose environment is exactly `env`."""
    return subprocess.Popen(['python3', '-c', 'import time; time.sleep(20)'], env=env)

# v3: stock ES runs as a Wayland client
board.write_text('x86-64-v3\n')
p = es_with({'WAYLAND_DISPLAY': 'wayland-0', 'XDG_RUNTIME_DIR': '/var/run', 'DISPLAY': ':0',
             'SDL_VIDEO_DRIVER': 'wayland', 'QT_QPA_PLATFORM': 'wayland', 'PATH': '/usr/bin:/bin'})
time.sleep(0.3)
try:
    e = m.graphical_environment(p.pid)
    check('v3: WAYLAND_DISPLAY comes from stock ES', e.get('WAYLAND_DISPLAY') == 'wayland-0')
    check('v3: SDL/Qt Wayland settings are passed through',
          e.get('SDL_VIDEO_DRIVER') == 'wayland' and e.get('QT_QPA_PLATFORM') == 'wayland')
    check('v3: no Xorg cookie is forced on a Wayland session', 'XAUTHORITY' not in e)
finally:
    p.kill()

# v3, ES environment unreadable: find the compositor socket
rt = t / 'run'; rt.mkdir(); (rt / 'wayland-0').write_text('')
os.environ['XDG_RUNTIME_DIR'] = str(rt)
e = m.graphical_environment(999999999)
check('v3: falls back to the wayland-N socket in XDG_RUNTIME_DIR', e.get('WAYLAND_DISPLAY') == 'wayland-0')
os.environ.pop('XDG_RUNTIME_DIR')

# x86_64: unchanged Xorg behaviour
board.write_text('x86_64\n')
good = t / 'cookie'; good.write_bytes(b'c')
p = es_with({'DISPLAY': ':0', 'XAUTHORITY': str(good), 'PATH': '/usr/bin:/bin'})
time.sleep(0.3)
try:
    e = m.graphical_environment(p.pid)
    check('x86_64: DISPLAY and the working cookie are kept', e.get('DISPLAY') == ':0' and e.get('XAUTHORITY') == str(good))
    check('x86_64: no Wayland variable appears', 'WAYLAND_DISPLAY' not in e)
finally:
    p.kill()
e = m.graphical_environment(999999999)
check('x86_64: DISPLAY defaults to :0 as before', e.get('DISPLAY') == ':0' and 'XAUTHORITY' in e)

board.unlink()
check('no board file counts as Xorg', m.wayland_board() is False)
print('ALL WAYLAND FIXTURES PASS' if not fails else 'FAILED: %s' % fails)
sys.exit(1 if fails else 0)
