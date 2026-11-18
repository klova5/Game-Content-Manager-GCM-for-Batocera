"""Recompute the checksum pins after changing files: any live file that names the old checksum of a
changed file gets the new one, and that change ripples up to the files that pin it in turn."""
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1]); changes = dict(a.split('=', 1) for a in sys.argv[2:])   # relpath=newfile
sha = lambda b: hashlib.sha256(b).hexdigest()
files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}
texts = {k for k, v in files.items() if k.endswith(('.py', '.sh', '.json')) or '/services/' in k}
mapping = {}
for rel, new in changes.items():
    old = sha(files[rel]); files[rel] = Path(new).read_bytes(); mapping[old] = sha(files[rel])
touched = set(changes)
while mapping:
    nxt = {}
    for k in sorted(texts):
        data = files[k]; before = sha(data)
        for old, newh in mapping.items():
            data = data.replace(old.encode(), newh.encode())
        if data != files[k]:
            files[k] = data; nxt[before] = sha(data); touched.add(k)
            print('repinned', k)
    mapping = nxt
for k in sorted(touched):
    (root / k).write_bytes(files[k])
print('changed files:', len(touched))
