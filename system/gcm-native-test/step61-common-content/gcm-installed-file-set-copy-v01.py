"""Copy explicitly bound installed files; preserve all originals and registration."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import uuid

def safe(value):
 p=Path(value)
 if not p.is_absolute() or str(p)!=os.path.normpath(str(p)) or any(x.is_symlink() for x in (p,*p.parents)):
  raise ValueError('Unsafe installed-file path')
 return p

def identity(p):
 s=safe(p).stat()
 if not stat.S_ISREG(s.st_mode):raise ValueError('Regular installed file required')
 return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def sync(path):
 fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)

def atomic(path,data):
 fd,temp=tempfile.mkstemp(prefix='.record-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as stream:json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
  os.replace(temp,path);sync(path.parent)
 finally:
  if os.path.exists(temp):os.unlink(temp)

def validate(files,binding,parent):
 parent=safe(parent)
 if (binding.get('schema')!='GCM_EXISTING_CONTENT_BINDING_V1' or binding.get('game_relationship_verified') is not True
     or binding.get('emulator') not in ('ryujinx','citron-emu') or binding.get('content_type') not in ('update','dlc')):
  raise ValueError('Verified installed-content binding required')
 if not files or len(files)>10000:raise ValueError('Bounded nonempty installed file set required')
 names=set();sources=set()
 for item in files:
  name=item['name'];p=safe(item['path'])
  if not name or Path(name).name!=name or name in ('.','..') or '\\' in name or name in names:
   raise ValueError('Unique simple destination name required')
  if str(p) in sources or parent==p or parent in p.parents or p in parent.parents:
   raise ValueError('Duplicate or overlapping source/destination')
  names.add(name);sources.add(str(p))
  if identity(p)!=item['identity']:raise ValueError('Installed file changed before import')
  digest=item['sha256']
  if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Bound file hash required')
 return parent

def import_files(files,parent,binding,progress=None):
 parent=validate(files,binding,parent);before={i['path']:identity(i['path']) for i in files}
 parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 work=parent/('.import-'+uuid.uuid4().hex);work.mkdir(mode=0o700);output=work/'files';output.mkdir(mode=0o700)
 total=sum(i['identity'][2] for i in files);copied=0;verified=0;entries=[]
 try:
  for item in files:
   p=safe(item['path']);fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
   try:
    s=os.fstat(fd);opened=[s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
    if opened!=item['identity']:raise ValueError('Source changed before open')
    remaining=opened[2];digest=hashlib.sha256()
    with os.fdopen(fd,'rb',closefd=False) as src,(output/item['name']).open('xb') as dst:
     while remaining:
      block=src.read(min(1024*1024,remaining))
      if not block:raise ValueError('Installed file truncated')
      dst.write(block);digest.update(block);remaining-=len(block);copied+=len(block)
      if progress:progress('COPYING',copied,total)
     if src.read(1):raise ValueError('Installed file grew')
     dst.flush();os.fsync(dst.fileno())
    if digest.hexdigest()!=item['sha256'] or identity(p)!=item['identity']:
     raise ValueError('Installed source differs from bound verified file')
   finally:os.close(fd)
   entries.append({'name':item['name'],'source':str(p),'size':opened[2],'sha256':item['sha256']})
  for entry in entries:
   digest=hashlib.sha256();size=0
   with (output/entry['name']).open('rb') as stream:
    while True:
     block=stream.read(1024*1024)
     if not block:break
     digest.update(block);size+=len(block);verified+=len(block)
     if progress:progress('VERIFYING LOCAL COPY',verified,total)
   if size!=entry['size'] or digest.hexdigest()!=entry['sha256']:raise ValueError('Local read-back mismatch')
  if any(identity(p)!=value for p,value in before.items()):raise ValueError('Installed content changed before publication')
  target=parent/uuid.uuid4().hex
  record={'schema':'GCM_IMPORTED_INSTALLED_FILE_SET_V1','binding':binding,'files':files,
   'manifest':entries,'managed_root':str(target/'files'),'status':'LOCAL_COPY_FILES_VERIFIED',
   'package_integrity':'UNASSESSED','original_preserved':True,'emulator_registration_changed':False,
   'source_snapshot':before,'managed_snapshot':{i['name']:identity(output/i['name']) for i in files},
   'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'import_ready':False}
  atomic(work/'record.json',record);sync(output);sync(work);os.rename(work,target);sync(parent)
  return record
 except BaseException:
  if work.exists():shutil.rmtree(work)
  raise

def reusable(record,files,binding):
 if (record.get('schema')!='GCM_IMPORTED_INSTALLED_FILE_SET_V1' or record.get('import_ready') is not True
     or record.get('binding')!=binding or record.get('files')!=files
     or record.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()):return False
 root=safe(record['managed_root'])
 if not root.is_dir() or {p.name for p in root.iterdir()}!={i['name'] for i in files}:return False
 return (all(identity(i['path'])==i['identity'] for i in files)
     and all(identity(root/i['name'])==record['managed_snapshot'][i['name']] for i in files))
