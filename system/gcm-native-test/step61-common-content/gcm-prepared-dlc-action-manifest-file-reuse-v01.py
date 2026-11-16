"""Source-bound action manifests published only after complete file verification."""
import hashlib,json,os,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DIRECTORY=ROOT/'state/prepared-dlc-action-manifests'

def safe(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Symlink action evidence path rejected')
    return path

def identity(path):
    st=safe(path).stat()
    return [st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns]

def binding(base,source):
    base,source=(safe(p).resolve(strict=True) for p in (base,source))
    if not base.is_file() or not source.is_file():raise ValueError('Regular base and source required')
    return {'base':str(base),'source':str(source),'base_identity':identity(base),'source_identity':identity(source),
            'boot':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}

def location(base,source):
    return safe(DIRECTORY/(hashlib.sha256((str(safe(base).resolve(strict=True))+'\0'+str(safe(source).resolve(strict=True))).encode()).hexdigest()+'.json'))

def publish(base,source,proof,before):
    if binding(base,source)!=before:raise ValueError('Source changed before action manifest publication')
    if proof.get('status')!='GAME_BOUND_DLC_FILES_VERIFIED':raise ValueError('Complete prepared file proof required')
    manifest=proof['manifest'];digest=proof['package_sha256']
    if manifest.get('package_sha256')!=digest:raise ValueError('Action manifest digest disagrees')
    # Keep display receipts separate: these manifests originate in strict verification.
    doc={'schema':'GCM_PREPARED_DLC_ACTION_MANIFEST_V1','binding':before,'proof':proof}
    data=json.dumps(doc,sort_keys=True).encode()
    safe(DIRECTORY).mkdir(mode=0o700,parents=True,exist_ok=True)
    target=location(base,source);fd,name=tempfile.mkstemp(prefix='.prepared-',dir=DIRECTORY)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
        if binding(base,source)!=before:raise ValueError('Source changed during action manifest publication')
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)

def fetch(base,source):
    target=location(base,source)
    if not target.exists():raise ValueError('Verify Selected DLC to prepare current action evidence')
    if target.stat().st_size>64*1024*1024:raise ValueError('Oversized action manifest')
    before=binding(base,source);doc=json.loads(target.read_text())
    if doc.get('schema')!='GCM_PREPARED_DLC_ACTION_MANIFEST_V1' or doc.get('binding')!=before:
        raise ValueError('Prepared source evidence changed; Verify Selected DLC again')
    proof=doc['proof']
    if proof.get('status')!='GAME_BOUND_DLC_FILES_VERIFIED' or binding(base,source)!=before:
        raise ValueError('Invalid prepared action evidence')
    return {'manifest':proof['manifest'],'game_evidence':{'application_id':proof['application_id']}}

# Action receipts differ from menu display receipts: their producer verifies bytes.
import stat
from copy import deepcopy

def tree(installed):
    installed=safe(installed).resolve(strict=True)
    if not installed.is_dir():raise ValueError('Prepared root must be a directory')
    result={'':['directory',identity(installed)]}
    for parent,dirs,files in os.walk(installed,followlinks=False,onerror=lambda exc:(_ for _ in ()).throw(exc)):
        for name in dirs+files:
            item=safe(Path(parent)/name);mode=item.stat().st_mode
            kind='directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else None
            if kind is None:raise ValueError('Unsupported prepared file type')
            result[item.relative_to(installed).as_posix()]=[kind,identity(item)]
            if len(result)>200000:raise ValueError('Prepared tree exceeds bounds')
    return result

def root_location(base,source,installed):
    key='\0'.join(str(safe(p).resolve(strict=True)) for p in (base,source,installed))
    return safe(ROOT/'state/prepared-dlc-file-evidence'/(hashlib.sha256(key.encode()).hexdigest()+'.json'))

def save_document(path,doc):
    safe(path.parent).mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.prepared-files-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:json.dump(doc,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def publish_root(base,source,installed,proof,before_binding,before_tree):
    if proof.get('status')!='GAME_BOUND_DLC_FILES_VERIFIED':raise ValueError('Verified file proof required')
    if binding(base,source)!=before_binding or tree(installed)!=before_tree:
        raise ValueError('Prepared files changed during verification')
    expected={e['path']:e['kind'] for e in proof['manifest']['entries']}
    if {p:v[0] for p,v in before_tree.items() if p}!=expected:
        raise ValueError('Prepared tree and verified manifest disagree')
    proof=deepcopy(proof);proof['installed_root']=str(safe(installed).resolve(strict=True))
    save_document(root_location(base,source,installed),{'schema':'GCM_PREPARED_DLC_FILE_EVIDENCE_V1',
        'binding':before_binding,'tree':before_tree,'proof':proof})

def reuse_root(base,source,installed):
    # Package and base identity evidence must also still be current.
    manifest=fetch(base,source)['manifest'];path=root_location(base,source,installed)
    if not path.exists():return None
    if path.stat().st_size>128*1024*1024:raise ValueError('Oversized prepared file evidence')
    before=binding(base,source);snapshot=tree(installed);doc=json.loads(path.read_text())
    proof=doc.get('proof',{})
    if (doc.get('schema')!='GCM_PREPARED_DLC_FILE_EVIDENCE_V1' or doc.get('binding')!=before
        or doc.get('tree')!=snapshot or proof.get('manifest')!=manifest
        or proof.get('installed_root')!=str(safe(installed).resolve(strict=True))
        or proof.get('status')!='GAME_BOUND_DLC_FILES_VERIFIED'
        or binding(base,source)!=before or tree(installed)!=snapshot):
        raise ValueError('Prepared DLC files changed; Verify Selected DLC again')
    return deepcopy(proof)

def rename_verified(base,source,origin,destination):
    proof=reuse_root(base,source,origin)
    if proof is None:raise ValueError('Verified origin evidence required before publication')
    before_binding=binding(base,source);before=tree(origin)
    destination=safe(destination)
    if destination.exists():raise ValueError('DLC rename destination appeared')
    os.rename(origin,destination)
    after=tree(destination)
    # Rename may update the root directory ctime; every child identity must match.
    comparable=deepcopy(after);comparable[''][1][4]=before[''][1][4]
    if comparable!=before or binding(base,source)!=before_binding:
        raise ValueError('DLC content changed during verified rename')
    publish_root(base,source,destination,proof,before_binding,after)
