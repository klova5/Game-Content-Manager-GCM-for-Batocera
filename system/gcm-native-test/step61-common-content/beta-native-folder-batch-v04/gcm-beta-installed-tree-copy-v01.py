"""Copy existing installed trees into a managed store; no emulator registration changes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid


def safe(value):
    path=Path(value)
    if not path.is_absolute() or Path(os.path.abspath(path))!=path:
        raise ValueError('Absolute normalized installed-content path required')
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Symlink installed-content path rejected')
    return path


def identity(path):
    s=safe(path).lstat()
    return [s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]


def tree(root):
    root=safe(root)
    if not root.is_dir():raise ValueError('Installed content directory required')
    result={'.':identity(root)}
    for directory,dirs,files in os.walk(root,followlinks=False):
        for name in sorted(dirs+files):
            path=safe(Path(directory)/name);value=identity(path)
            if not (stat.S_ISDIR(value[2]) or stat.S_ISREG(value[2])):
                raise ValueError('Unsupported installed-content entry')
            result[path.relative_to(root).as_posix()]=value
    return result


def sync(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def atomic_json(path,data):
    fd,temp=tempfile.mkstemp(prefix='.record-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path);sync(path.parent)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def import_tree(source,managed_parent,binding,progress=None):
    source=safe(source);parent=safe(managed_parent)
    if source==parent or source in parent.parents or parent in source.parents:
        raise ValueError('Source and managed storage must not overlap')
    if (binding.get('schema')!='GCM_EXISTING_CONTENT_BINDING_V1'
            or binding.get('game_relationship_verified') is not True
            or binding.get('content_type') not in ('update','dlc')
            or not binding.get('application_id') or not binding.get('emulator')):
        raise ValueError('Adapter-verified game binding required')
    before=tree(source)
    total=sum(v[3] for v in before.values() if stat.S_ISREG(v[2]))
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    work=safe(parent/('.import-'+uuid.uuid4().hex));work.mkdir(mode=0o700)
    output=work/'files';output.mkdir(mode=0o700)
    entries=[];completed=0
    try:
        for name,expected in sorted(before.items()):
            if name=='.':continue
            original=safe(source/name);destination=output/name
            if stat.S_ISDIR(expected[2]):
                destination.mkdir(mode=0o700,parents=True,exist_ok=True)
                entries.append({'path':name,'kind':'directory'});continue
            destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd=os.open(original,os.O_RDONLY|os.O_NOFOLLOW)
            try:
                s=os.fstat(fd)
                opened=[s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
                if opened!=expected:raise ValueError('Source changed before copy')
                digest=hashlib.sha256();remaining=expected[3]
                with os.fdopen(fd,'rb',closefd=False) as src,destination.open('xb') as dst:
                    while remaining:
                        block=src.read(min(1024*1024,remaining))
                        if not block:raise ValueError('Installed source truncated')
                        dst.write(block);digest.update(block);remaining-=len(block)
                        completed+=len(block)
                        if progress:progress('COPYING',completed,total)
                    if src.read(1):raise ValueError('Installed source grew')
                    dst.flush();os.fsync(dst.fileno())
                if identity(original)!=expected:raise ValueError('Source changed during copy')
            finally:os.close(fd)
            os.chmod(destination,stat.S_IMODE(expected[2]) & 0o777)
            entries.append({'path':name,'kind':'file','size':expected[3],'sha256':digest.hexdigest()})
        if tree(source)!=before:raise ValueError('Installed source tree changed during import')
        checked=0
        for entry in entries:
            if entry['kind']!='file':continue
            digest=hashlib.sha256();size=0
            with (output/entry['path']).open('rb') as stream:
                while True:
                    block=stream.read(1024*1024)
                    if not block:break
                    digest.update(block);size+=len(block);checked+=len(block)
                    if progress:progress('VERIFYING LOCAL COPY',checked,total)
            if size!=entry['size'] or digest.hexdigest()!=entry['sha256']:
                raise ValueError('Managed copy read-back mismatch')
        if tree(source)!=before:raise ValueError('Installed source changed before publication')
        manifest={'schema':'GCM_IMPORTED_INSTALLED_FILES_V1','entries':entries}
        digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        # A separate record per successful import avoids overwriting an earlier damaged copy.
        target=parent/(digest+'-'+uuid.uuid4().hex)
        record={'schema':'GCM_IMPORTED_INSTALLED_CONTENT_V1','binding':binding,
                'original_root':str(source),'managed_root':str(target/'files'),
                'tree_sha256':digest,'manifest':manifest,'source_snapshot':before,
                'status':'LOCAL_COPY_FILES_VERIFIED','package_integrity':'UNASSESSED',
                'emulator_registration_changed':False,'original_preserved':True,
                'in_game_availability':'UNVERIFIED'}
        atomic_json(work/'record.json',record)
        for directory,dirs,files in os.walk(output,topdown=False):sync(directory)
        sync(work);os.rename(work,target);sync(parent)
        return record
    except BaseException:
        if work.exists():shutil.rmtree(work)
        raise
