"""Shared conservative failure classification and verified workspace release."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
MANAGED=Path('/userdata/system/game-content/ps4')

def recovery_required(mutated,report=None):
 if not mutated:return False
 if isinstance(report,dict) and (report.get('status')=='ERROR' and report.get('recovery_required')=='NO'
    and report.get('phase') in ('VERIFYING','COPYING','PREPARING')):return False
 return True

def preparation_peak(required,same_filesystem=True,reserve=64*1024*1024):
 if type(required) is not int or required<0:raise ValueError('Valid preparation size required')
 return required*(2 if same_filesystem else 1)+reserve

def safe(value):
 p=Path(value)
 if not p.is_absolute() or str(p)!=os.path.normpath(str(p)) or any(v.is_symlink() for v in (p,*p.parents)):raise ValueError('Unsafe lifecycle path')
 return p

def identity(path):
 s=safe(path).stat()
 return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def snapshot(path):
 result={};root=safe(path);device=root.stat().st_dev
 for directory,dirs,files in os.walk(root,followlinks=False):
  for name in sorted(dirs+files):
   p=safe(Path(directory)/name);s=p.lstat()
   if s.st_dev!=device or not (stat.S_ISREG(s.st_mode) or stat.S_ISDIR(s.st_mode)):raise ValueError('Unexpected workspace entry')
   if stat.S_ISREG(s.st_mode) and s.st_nlink!=1:raise ValueError('Shared workspace inode cannot be released')
   result[p.relative_to(root).as_posix()]=identity(p)
 return result

def normalized(manifest):
 return sorted((e['path'],e['kind'],e.get('size'),e.get('sha256')) for e in manifest['entries'])

def atomic(path,data):
 fd,temp=tempfile.mkstemp(prefix='.lifecycle-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as stream:json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
  os.replace(temp,path)
  fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(temp):os.unlink(temp)

def release(prepared,result):
 prepared=safe(prepared);work=prepared.parent
 if prepared.name!='output' or work.parent!=ROOT or not re.fullmatch('step61-ps4-dlc-preparation-[0-9a-f]{32}',work.name):raise ValueError('Dedicated preparation output required')
 names={p.name for p in work.iterdir()}
 if not {'output','report.json'}.issubset(names) or names-{'output','report.json','progress.json'}:raise ValueError('Unexpected workspace contents')
 report=safe(work/'report.json')
 if report.stat().st_size>64*1024*1024:raise ValueError('Oversized preparation report')
 doc=json.loads(report.read_text());proof=result['installed_evidence'];record=result['installed_record']
 if (result.get('status')!='INSTALLED_FILES_VERIFIED' or result.get('source_preserved') is not True
  or result.get('configuration_changed') is not False or result.get('enablement_performed') is not False
  or proof.get('status')!='GAME_BOUND_DLC_FILES_VERIFIED' or doc.get('status')!='DLC_PREPARATION_FILES_VERIFIED'
  or doc.get('output')!=str(prepared) or doc.get('source')!=record['source']
  or proof['package_sha256']!=record['package_sha256'] or doc['manifest']['package_sha256']!=proof['package_sha256']
  or normalized(doc['manifest'])!=normalized(proof['manifest'])):raise ValueError('Publication proof and preparation manifest disagree')
 title=proof['application_id'];digest=proof['package_sha256'];installed=safe(record['installed_root']);source=safe(record['source'])
 if not re.fullmatch('CUSA[0-9]{5}',title) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid published game/package identity')
 if installed!=MANAGED/title/'installed-dlc'/digest or source.parent!=MANAGED/title/'dlc' or not installed.is_dir() or not source.is_file():raise ValueError('Publication root or preserved package unavailable')
 if identity(source)!=result['managed_source_identity'] or identity(result['base_metadata'])!=result['base_metadata_identity']:raise ValueError('Published source or base changed')
 expected_files={e['path']:e for e in proof['manifest']['entries'] if e['kind']=='file'}
 verified=result['verified_files']
 if len(verified)!=len(expected_files):raise ValueError('Incomplete publication file evidence')
 seen=set()
 for item in verified:
  path=safe(item['path']);name=path.relative_to(installed).as_posix()
  if name in seen or name not in expected_files or identity(path)!=item['identity'] or path.stat().st_size!=expected_files[name]['size']:raise ValueError('Published files changed before cleanup')
  seen.add(name)
 before=snapshot(work)
 output_names={p.relative_to(prepared).as_posix() for p in prepared.rglob('*')}
 if output_names!={e['path'] for e in doc['manifest']['entries']}:raise ValueError('Workspace paths differ from preparation evidence')
 audit=safe(ROOT/'state/preparation-lifecycle-audits');audit.mkdir(mode=0o700,parents=True,exist_ok=True)
 log=audit/(uuid.uuid4().hex+'.json')
 data={'schema':'GCM_PREPARATION_LIFECYCLE_V1','status':'ASSESSED','workspace':str(work),'published_root':str(installed),
  'package_sha256':digest,'preparation_report':doc,'snapshot':before,'original_source_preserved':str(source)}
 if (work/'progress.json').exists():
  progress=safe(work/'progress.json')
  if progress.stat().st_size>65536:raise ValueError('Oversized preparation telemetry')
  data['progress']=json.loads(progress.read_text())
  if data['progress'].get('schema')!='GCM_GLOBAL_ACTION_PROGRESS_V1':raise ValueError('Unexpected preparation telemetry')
 atomic(log,data)
 if snapshot(work)!=before or any(identity(item['path'])!=item['identity'] for item in verified):raise ValueError('Evidence changed before workspace release')
 shutil.rmtree(work);data['status']='RELEASED';atomic(log,data)
 return {'status':'RELEASED','workspace':str(work),'audit':str(log)}
