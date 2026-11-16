"""Display-only installed-file receipts; content actions never authorize from these."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / 'state/global-menu-file-evidence'
SCHEMA = 'GCM_MENU_FILE_EVIDENCE_V1'

def boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def safe(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():raise ValueError('Symlink display evidence path rejected')
    return path

def identity(path):
    st = safe(path).stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def snapshot(base, source, installed):
    base, source, installed = (safe(p).resolve(strict=True) for p in (base,source,installed))
    if not base.is_file() or not source.is_file() or not installed.is_dir():
        raise ValueError('Display evidence requires regular source/base and installed directory')
    tree = {'': ['directory', identity(installed)]}
    for parent, dirs, files in os.walk(installed,followlinks=False,onerror=lambda exc:(_ for _ in ()).throw(exc)):
        for name in dirs+files:
            item=safe(Path(parent)/name)
            mode=item.stat().st_mode
            kind='directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else None
            if kind is None:raise ValueError('Unsupported display evidence entry')
            tree[item.relative_to(installed).as_posix()]=[kind,identity(item)]
            if len(tree)>200000:raise ValueError('Display evidence tree exceeds bounds')
    return {'boot_id':boot(),'base':str(base),'source':str(source),'installed':str(installed),
            'base_identity':identity(base),'source_identity':identity(source),'tree':tree}

def location(base,source,installed):
    value='\0'.join(str(safe(p).resolve(strict=True)) for p in (base,source,installed))
    return safe(DIRECTORY / (hashlib.sha256(value.encode()).hexdigest()+'.json'))

def publish(base, source, installed, digest, kind, application, manifest, verified,
            base_identity, source_identity):
    if kind not in ('update','dlc') or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid display evidence identity')
    before=snapshot(base,source,installed)
    if before['base_identity']!=base_identity or before['source_identity']!=source_identity:
        raise ValueError('Source or base changed before receipt publication')
    expected={entry['path']:entry['kind'] for entry in manifest['entries']}
    if {p:v[0] for p,v in before['tree'].items() if p}!=expected:
        raise ValueError('Display receipt tree differs from verified manifest')
    wanted={p for p,k in expected.items() if k=='file'}
    if set(verified)!=wanted or any(before['tree'][p][1]!=verified[p] for p in wanted):
        raise ValueError('Display receipt files differ from verified identities')
    document={'schema':SCHEMA,'application_id':application,'content_type':kind,
              'package_sha256':digest,'snapshot':before,'installation_authorized':False}
    if snapshot(base,source,installed)!=before:raise ValueError('Files changed during receipt publication')
    safe(DIRECTORY).mkdir(mode=0o700,parents=True,exist_ok=True)
    target=location(base,source,installed)
    fd,name=tempfile.mkstemp(prefix='.receipt-',dir=DIRECTORY)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(document,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)
    return document

def valid(base,source,installed,digest,kind,application):
    try:
        target=location(base,source,installed)
        if target.stat().st_size>64*1024*1024:return False
        document=json.loads(target.read_text())
        if (document.get('schema')!=SCHEMA or document.get('application_id')!=application
            or document.get('content_type')!=kind or document.get('package_sha256')!=digest
            or document.get('installation_authorized') is not False):return False
        before=snapshot(base,source,installed)
        return document.get('snapshot')==before and snapshot(base,source,installed)==before
    except (OSError,ValueError,KeyError,TypeError):return False
