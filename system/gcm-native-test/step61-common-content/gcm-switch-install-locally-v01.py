"""Switch existing-content imports for the shared Install Locally action."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import types
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
MANAGED=Path('/userdata/system/game-content')
PINS={'gcm-switch-content-action-plan-v01.py': '25e8f04e0017f723b5dce647f2ba91519608baad57d993529d567c46e78ab3b0', 'gcm-installed-file-set-copy-v01.py': 'a8c74e8369c42af308ca83b8e6374a594d0ace0aab81554a38d39111bdfb2db6', 'gcm-uninstall-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec'}

def load(name):
 p=ROOT/name
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:
  raise ValueError('Switch local-import dependency changed: '+name)
 spec=importlib.util.spec_from_file_location('switch_local_'+p.stem,p)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def normalize(emulator):
 result={'ryujinx-emu':'ryujinx','citron':'citron-emu'}.get(emulator,emulator)
 if result not in ('ryujinx','citron-emu'):raise ValueError('Switch Install Locally adapter unavailable')
 return result

def rows_from_records(app,emulator,records,planner):
 copier=load('gcm-installed-file-set-copy-v01.py');rows=[];seen=set()
 registered=[r for r in records if r['application_id'].upper()==app]
 def signature(r):return (r['title_id'].upper(),r['content_type'],r['internal_version'])
 managed_signatures={signature(r) for r in registered if r.get('origin')=='External' and copier.safe(r['path']).is_relative_to(MANAGED)}
 for r in registered:
  files_checked=planner.checked(r,emulator)
  if emulator=='ryujinx':
   source=r['registered_path']
   if copier.safe(source).is_relative_to(MANAGED):continue
   files=[{'path':source,'name':Path(source).name,'identity':files_checked[0]['identity'],'sha256':r['sha256']}]
  elif r['origin'] in ('UserNAND','SDMC'):
   if signature(r) in managed_signatures:continue
   source=r['path']
   files=[{'path':source,'name':Path(source).name,'identity':files_checked[0]['identity'],'sha256':r['metadata_sha256']}]
   files.extend({'path':x['path'],'name':Path(x['path']).name,'identity':files_checked[i+1]['identity'],'sha256':x['sha256']} for i,x in enumerate(r['verified_records']))
  elif r['origin']=='External':
   source=r['path']
   if copier.safe(source).is_relative_to(MANAGED):continue
   files=[{'path':source,'name':Path(source).name,'identity':files_checked[0]['identity'],'sha256':r['sha256']}]
  else:raise ValueError('Unknown Switch registration origin')
  token='existing:'+str(source).encode().hex()
  if token in seen:raise ValueError('Ambiguous installed-content import token')
  seen.add(token)
  binding={'schema':'GCM_EXISTING_CONTENT_BINDING_V1','game_relationship_verified':True,
   'application_id':app,'title_id':r['title_id'].upper(),'content_type':r['content_type'],
   'internal_version':r['internal_version'],'emulator':emulator,'origin':r.get('origin')}
  copier.validate(files,binding,MANAGED/'switch'/app/'imported-installed'/emulator/r['content_type'])
  rows.append({'token':token,'source_root':source,'content_type':r['content_type'],'content_id':r['title_id'].upper(),
   'version':r.get('display_version',str(r['internal_version'])),'binding':binding,'files':files,
   'install_locally_available':True,'managed_status':'EXTERNAL_INSTALLED'})
 return {'application_id':app,'emulator':emulator,'rows':rows}

def inventory(rom,emulator):
 emulator=normalize(emulator);planner=load('gcm-switch-content-action-plan-v01.py')
 plan=planner.inspect(rom,emulator)
 if emulator=='ryujinx':records=planner.load('ryujinx-installed-integrity-v02.py').validate(rom)['records']
 else:records=planner.load('citron-installed-integrity.py').validate()['records']
 return rows_from_records(plan['application_id'],emulator,records,planner)

def parent_for(doc,row):
 return load('gcm-installed-file-set-copy-v01.py').safe(MANAGED/'switch'/doc['application_id']/'imported-installed'/doc['emulator']/row['content_type'])

def local_copy(row,doc):
 copier=load('gcm-installed-file-set-copy-v01.py');parent=parent_for(doc,row)
 for path in sorted(parent.glob('*/record.json')) if parent.exists() else []:
  copier.safe(path)
  if path.stat().st_size>16*1024*1024:raise ValueError('Oversized local import record')
  record=json.loads(path.read_text())
  if record.get('managed_root')!=str(path.parent/'files'):raise ValueError('Local import location disagrees')
  if copier.reusable(record,row['files'],row['binding']):return record
 return None

def resolve(rom,emulator,token):
 if not isinstance(token,str) or not token.startswith('existing:'):raise ValueError('Existing-content token required')
 doc=inventory(rom,emulator);rows=[r for r in doc['rows'] if r['token']==token]
 if len(rows)!=1:raise ValueError('Selected existing content is no longer registered for this game')
 return doc,rows[0]

def idle():
 for p in Path('/proc').glob('[0-9]*/comm'):
  try:name=p.read_text().strip().casefold()
  except (FileNotFoundError,ProcessLookupError):continue
  if name.startswith(('ryujinx','citron','yuzu')):raise ValueError('Close Switch emulators before importing content')
 for folder in ('global-content-actions','global-uninstall-jobs'):
  for p in (ROOT/'state'/folder).rglob('*.json'):
   if p.name.startswith('.'):continue
   if json.loads(p.read_text()).get('status')=='RUNNING':raise ValueError('Another content operation is running')

def execute(rom,emulator,tokens,confirmed=False,progress=None):
 if confirmed is not True or len(tokens)!=1:raise ValueError('Confirm exactly one existing-content import')
 emulator=normalize(emulator);copier=load('gcm-installed-file-set-copy-v01.py')
 lock=copier.safe(ROOT/'state/global-content-actions/.start.lock');lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX);idle();doc,row=resolve(rom,emulator,tokens[0])
  existing=local_copy(row,doc)
  if existing:return existing
  record=copier.import_files(row['files'],parent_for(doc,row),row['binding'],progress)
  current_doc,current=resolve(rom,emulator,tokens[0])
  if current!=row or current_doc['application_id']!=doc['application_id']:
   raise ValueError('Registration changed; local copy remains unready')
  idle();record['import_ready']=True
  copier.atomic(Path(record['managed_root']).parent/'record.json',record)
  if not copier.reusable(record,row['files'],row['binding']):raise ValueError('Imported copy changed before completion')
  return record
 finally:os.close(fd)

def provider(emulator):
 emulator=normalize(emulator)
 def plan(rom,tokens):
  if len(tokens)!=1:raise ValueError('Select one existing-content item')
  return resolve(rom,emulator,tokens[0])
 return types.SimpleNamespace(load=load,plan=plan,idle=idle,
  execute=lambda rom,tokens,confirmed=False,progress=None:execute(rom,emulator,tokens,confirmed,progress))
