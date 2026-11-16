"""Durable configuration replacement plus quarantine; callers own locks and scope."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
ENGINE_SHA='1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec'

def engine():
 path=Path(__file__).with_name('gcm-uninstall-transaction-v01.py')
 if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=ENGINE_SHA:raise ValueError('Quarantine engine changed')
 spec=importlib.util.spec_from_file_location('removal_engine',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def raw(path):
 p=engine().safe(path)
 if not p.exists():return None
 if not p.is_file() or p.stat().st_size>64*1024*1024:raise ValueError('Bounded metadata file required')
 return p.read_bytes()

def encode(data):return None if data is None else base64.b64encode(data).decode('ascii')
def decode(data):return None if data is None else base64.b64decode(data,validate=True)

def atomic(path,data):
 e=engine();p=e.safe(path)
 if data is None:
  p.unlink();e.sync(p.parent);return
 mode=(p.stat().st_mode & 0o777) if p.exists() else 0o600
 fd,tmp=tempfile.mkstemp(prefix='.content-remove-',dir=p.parent)
 try:
  os.fchmod(fd,mode)
  with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
  os.replace(tmp,p);e.sync(p.parent)
 finally:
  if os.path.exists(tmp):os.unlink(tmp)

def recover(journal):
 e=engine();j=json.loads(e.safe(journal).read_text())
 if j.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1':raise ValueError('Unknown removal transaction')
 # Validate ALL paths before any recovery writes; do not overwrite concurrent changes.
 for change in j['changes']:
  current=raw(change['path'])
  if current not in (decode(change['before']),decode(change['after'])):raise ValueError('Configuration changed outside transaction')
 for move in j['moves']:
  src,dst=e.safe(move['source']),e.safe(move['quarantine'])
  if src.exists()==dst.exists():raise ValueError('Ambiguous removal recovery paths')
  if not e.matches(e.snapshot(src if src.exists() else dst),move['snapshot'],renamed=True):raise ValueError('Recovery content changed')
 j['status']='ROLLING_BACK';e.save(journal,j)
 for change in reversed(j['changes']):
  before=decode(change['before'])
  if raw(change['path'])!=before:atomic(change['path'],before)
 for move in reversed(j['moves']):
  src,dst=Path(move['source']),Path(move['quarantine'])
  if dst.exists():os.rename(dst,src);e.sync(dst.parent);e.sync(src.parent)
 j['status']='ROLLED_BACK';e.save(journal,j);return j

def apply(journal,moves,changes,metadata,check_before=None,check_after=None,after_step=None):
 e=engine();journal=e.safe(journal)
 if journal.exists():raise ValueError('New removal journal required')
 e.validate_moves(moves)
 paths=[e.safe(c['path']) for c in changes]
 if len(paths)!=len(set(paths)):raise ValueError('Duplicate configuration writes')
 for p in paths:
  for move in moves:
   for key in ('source','quarantine'):
    root=Path(move[key])
    if p==root or p in root.parents or root in p.parents:raise ValueError('Configuration and quarantine paths overlap')
 for change in changes:
  if raw(change['path'])!=change['before']:raise ValueError('Configuration changed before removal')
 if check_before:check_before()
 j={'schema':'GCM_CONTENT_REMOVAL_TRANSACTION_V1','status':'PREPARED','metadata':metadata,
  'moves':moves,'changes':[{'path':str(c['path']),'before':encode(c['before']),'after':encode(c['after'])} for c in changes]}
 e.save(journal,j)
 step=0
 try:
  j['status']='APPLYING';e.save(journal,j)
  for move in moves:
   src,dst=e.safe(move['source']),e.safe(move['quarantine'])
   if dst.exists() or not e.matches(e.snapshot(src),move['snapshot']):raise ValueError('Removal source changed')
   os.rename(src,dst);e.sync(src.parent);e.sync(dst.parent)
   if not e.matches(e.snapshot(dst),move['snapshot'],renamed=True):raise ValueError('Quarantine changed')
   step+=1
   if after_step:after_step(step)
  for change in changes:
   if raw(change['path'])!=change['before']:raise ValueError('Concurrent configuration change')
   atomic(change['path'],change['after']);step+=1
   if after_step:after_step(step)
  if check_after:check_after()
  for change in changes:
   if raw(change['path'])!=change['after']:raise ValueError('Published configuration changed')
  j['status']='COMPLETE';e.save(journal,j);return j
 except Exception as exc:
  try:recover(journal)
  except Exception as recovery:
   j['status']='RECOVERY_REQUIRED';j['error']=str(exc);j['recovery_error']=str(recovery);e.save(journal,j)
   raise RuntimeError('Removal requires recovery assessment: '+str(journal)) from exc
  raise
