"""Shared source-bound installed-file verification and local import worker.

Providers supply current game bindings and native expected file hashes. No
package proof or emulator activation is inferred from copying installed files.
This library has no user-supplied binding CLI and changes no stable routes.
"""
import hashlib
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

MAX_FILES=100000
MAX_JSON=32*1024*1024

def check_space(parent,required):
 path=safe(parent)
 while not path.exists():path=path.parent
 helper=Path(__file__).with_name('gcm-beta-filesystem-space-v01.py')
 if helper.is_symlink() or hashlib.sha256(helper.read_bytes()).hexdigest()!='d5bc6f049146f9262f2c1f9e4648a86ea0341f6ded160cca31e3d9dd5b368a21':
  raise ValueError('Shared space policy changed')
 spec=importlib.util.spec_from_file_location('gcm_beta_space',helper)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 if module.disk_usage(path).free<required+64*1024*1024:
  raise ValueError('Insufficient local-copy space')

def safe(value):
 p=Path(value)
 if not p.is_absolute() or str(p)!=os.path.normpath(str(p)) or any(v.is_symlink() for v in (p,*p.parents)):
  raise ValueError('Absolute normalized nonsymlink path required')
 return p

def ident(p):
 s=safe(p).stat()
 if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:raise ValueError('Single-link regular content file required')
 return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def snapshot(root):
 root=safe(root)
 if not root.is_dir():raise ValueError('Installed tree required')
 files={};dirs=[]
 for folder,names,leaves in os.walk(root,followlinks=False):
  for name in sorted(names):
   p=safe(Path(folder)/name)
   if not p.is_dir():raise ValueError('Unsupported content directory')
   dirs.append(p.relative_to(root).as_posix())
  for name in sorted(leaves):
   p=safe(Path(folder)/name);files[p.relative_to(root).as_posix()]=ident(p)
  if len(files)+len(dirs)>MAX_FILES:raise ValueError('Content tree entry limit exceeded')
 return {'files':files,'directories':sorted(dirs)}

def atom(path,data):
 path=safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 raw=json.dumps(data,sort_keys=True,indent=2).encode()
 if len(raw)>MAX_JSON:raise ValueError('Receipt exceeds bound')
 fd,temp=tempfile.mkstemp(prefix='.gcm-worker-',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
  os.replace(temp,path)
  fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(temp):os.unlink(temp)

def binding(provider,rom,token):
 # No caller-provided JSON can authorize a binding: always requery the adapter.
 b=provider.resolve(rom,token)
 if (b.get('schema')!='GCM_EXISTING_CONTENT_BINDING_V1' or b.get('game_relationship_verified') is not True
     or b.get('content_type') not in ('update','dlc') or not b.get('application_id')
     or not b.get('emulator') or not b.get('identity_evidence')):
  raise ValueError('Current adapter-verified internal game binding required')
 safe(b['source_root']);return b

def digest_file(path,expected,progress,total,done):
 fd=os.open(safe(path),os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd)
  if [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]!=expected or s.st_nlink!=1:
   raise ValueError('File replaced before verification')
  digest=hashlib.sha256();remaining=expected[2]
  with os.fdopen(fd,'rb',closefd=False) as stream:
   while remaining:
    data=stream.read(min(1024*1024,remaining))
    if not data:raise ValueError('File truncated during verification')
    digest.update(data);remaining-=len(data);done+=len(data)
    if progress:progress('VERIFYING INSTALLED FILES',done,total)
   if stream.read(1):raise ValueError('File grew during verification')
  if ident(path)!=expected:raise ValueError('File changed during verification')
  return digest.hexdigest(),done
 finally:os.close(fd)

def verify(provider,rom,token,receipt_path,progress=None):
 b=binding(provider,rom,token);root=safe(b['source_root'])
 destination=safe(receipt_path)
 if destination==root or root in destination.parents:raise ValueError('Receipt must be outside installed content')
 before=snapshot(root)
 expected=provider.expected_files(b)
 if not isinstance(expected,dict) or not expected or set(expected)!=set(before['files']):
  raise ValueError('Complete native expected file manifest required')
 for name,digest in expected.items():
  if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
   raise ValueError('Expected SHA256 required for each installed file')
 total=sum(v[2] for v in before['files'].values());done=0
 for name,current in sorted(before['files'].items()):
  actual,done=digest_file(root/name,current,progress,total,done)
  if actual!=expected[name]:raise ValueError('Installed file hash mismatch: '+name)
 if snapshot(root)!=before or binding(provider,rom,token)!=b:raise ValueError('Content or binding changed during verification')
 record={'schema':'GCM_BETA_INSTALLED_VERIFICATION_V1','binding':b,'snapshot':before,
         'sha256':expected,'status':'INSTALLED_FILES_VERIFIED','package_integrity':'UNASSESSED',
         'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'verified_epoch':time.time()}
 atom(receipt_path,record);return record

def reusable(provider,rom,token,record):
 try:
  b=binding(provider,rom,token)
  return (record.get('schema')=='GCM_BETA_INSTALLED_VERIFICATION_V1'
   and record.get('status')=='INSTALLED_FILES_VERIFIED' and record['binding']==b
   and record['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
   and snapshot(b['source_root'])==record['snapshot']
   and provider.expected_files(b)==record['sha256'])
 except (OSError,ValueError,KeyError):return False

def install_locally(provider,rom,token,record,copier,parent,confirmed=False,progress=None):
 if confirmed is not True:raise ValueError('Explicit local-copy confirmation required')
 if not reusable(provider,rom,token,record):raise ValueError('Current installed-file verification required')
 b=record['binding'];parent=safe(parent)
 if parent.exists():
  for path in sorted(parent.glob('*/record.json')):
   safe(path)
   if path.stat().st_size>MAX_JSON:raise ValueError('Oversized local receipt')
   prior=json.loads(path.read_text())
   root=Path(prior.get('managed_root','/'))
   if root!=path.parent/'files':continue
   if (prior.get('beta_worker_binding')==b and prior.get('import_ready') is True
       and prior.get('boot_id')==record['boot_id'] and prior.get('source_snapshot_beta')==record['snapshot']
       and snapshot(root)==prior.get('managed_snapshot_beta')):return prior
 check_space(parent,sum(v[2] for v in record['snapshot']['files'].values()))
 imported=copier.import_tree(Path(b['source_root']),parent,b,progress)
 # The copier publishes a durable recoverable record first; readiness is separate.
 receipt=Path(imported['managed_root']).parent/'record.json'
 copied={e['path']:e['sha256'] for e in imported['manifest']['entries'] if e['kind']=='file'}
 if copied!=record['sha256'] or not reusable(provider,rom,token,record):
  raise ValueError('Local copy retained without readiness: source/proof changed')
 imported.update(beta_worker_binding=b,source_snapshot_beta=record['snapshot'],
                 managed_snapshot_beta=snapshot(imported['managed_root']),
                 boot_id=record['boot_id'],import_ready=True)
 atom(receipt,imported);return imported

class OwnedProgress:
 """Worker-specific state writer; another owner cannot update this job."""
 def __init__(self,path,job_id,owner):self.path=safe(path);self.job_id=job_id;self.owner=owner
 def emit(self,phase,done,total):
  lock=safe(str(self.path)+'.lock')
  fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   if self.path.stat().st_size>MAX_JSON:raise ValueError('Oversized job')
   current=json.loads(self.path.read_text())
   if current.get('job_id')!=self.job_id or current.get('owner')!=self.owner or current.get('status')!='RUNNING':
    raise ValueError('Content job ownership changed')
   if not 0<=done<=total:raise ValueError('Invalid measured progress')
   current.update(phase=phase,bytes_complete=done,bytes_total=total,
                  percent=(100*done//total if total else -1),updated_epoch=time.time())
   atom(self.path,current)
  finally:os.close(fd)
