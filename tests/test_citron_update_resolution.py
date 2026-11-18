"""61TJ fixtures: Citron update resolution with duplicate copies."""
import copy, importlib.util, sys
fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
spec = importlib.util.spec_from_file_location('res', sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
APP = '0100152000022000'; TITLE = '0100152000022800'
def rec(path, h='a' * 64, ver=1507328, disp='4.0.1', origin='External'):
    return {'application_id': APP, 'title_id': TITLE, 'content_type': 'update', 'internal_version': ver, 'origin': origin,
            'primary_content_hashes_verified': True, 'present_content_hashes_verified': True,
            'display_version': disp, 'sha256': h, 'path': path}
store = rec('/userdata/system/game-content/switch/updates/%s/MK8 v4.0.1.nsp' % APP)
citron = rec('/userdata/system/game-content/citron-registered/%s/updates/MK8 v4.0.1.nsp' % APP)
r = lambda recs, disabled=(): m.resolve(recs, disabled, [], inventory_complete=True)
check('Mario Kart 8 case: identical copies resolve to Citron\'s install copy', r([store, citron]) is citron)
check('order of records does not matter', r([citron, store]) is citron)
a, b = rec('/media/x/a.nsp'), rec('/media/y/b.nsp')
check('identical copies outside citron-registered pick the first path', r([b, a]) is a)
try:
    r([store, rec(citron['path'], h='b' * 64)]); check('different content at the same version is still refused', False)
except ValueError as e:
    check('different content at the same version is still refused', 'indistinguishable' in str(e))
try:
    r([rec('/a', h=None), rec('/b', h=None)]); check('copies without a verified hash are still refused', False)
except ValueError as e:
    check('copies without a verified hash are still refused', 'indistinguishable' in str(e))
check('a single update is unchanged', r([store]) is store)
newer = rec('/x/new.nsp', h='c' * 64, ver=1572864, disp='4.1.0')
check('a newer version still wins over duplicates', r([store, citron, newer]) is newer)
check('an update disabled in Citron is still respected', r([store, citron], ['External Files/Update v4.0.1']) is None)
print('ALL RESOLUTION FIXTURES PASS' if not fails else 'FAILED: %s' % fails)
sys.exit(1 if fails else 0)
