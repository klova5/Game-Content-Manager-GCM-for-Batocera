"""Read-only Switch content plans. A plan is never mutation authorization."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

SHARED=Path('/userdata/system/gcm-native-test/step60-shared-switch')
MANAGED=Path('/userdata/system/game-content')
PINS={
 'ryujinx-installed-integrity-v02.py':'ced277370d7f282dba78c596d727601401ff24e91e16ad98f204fe06fb3600e0',
 'citron-installed-integrity.py':'987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc',
 'gcm-switch-base-identity.py':'4a4d0be60991eeb9ae35e2479d563c5aec5c18aae4d6648f7f7ad4aad75bf3f1'}

def safe(value):
 p=Path(value)
 if not p.is_absolute() or str(p)!=os.path.normpath(str(p)) or any(x.is_symlink() for x in (p,*p.parents)):
  raise ValueError('Unsafe content path: '+str(p))
 return p

def identity(path):
 s=safe(path).stat()
 if not stat.S_ISREG(s.st_mode):raise ValueError('Regular installed file required')
 return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def load(name):
 p=safe(SHARED/name);raw=p.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=PINS[name]:raise ValueError('Switch dependency changed: '+name)
 spec=importlib.util.spec_from_file_location('switch_plan_'+p.stem,p)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def paths(record,emulator):
 if emulator=='ryujinx':return [record['registered_path']]
 return [record['path']]+[r['path'] for r in record.get('verified_records',[])]

def checked(record,emulator):
 if not re.fullmatch('[0-9A-Fa-f]{16}',record.get('title_id','')):raise ValueError('Invalid content title identity')
 if record.get('content_type') not in ('update','dlc'):raise ValueError('Unsupported content kind')
 if record.get('primary_content_hashes_verified') is not True or record.get('present_content_hashes_verified') is not True:
  raise ValueError('Current installed-file proof required')
 result=[]
 for i,p in enumerate(paths(record,emulator)):
  current=identity(p)
  if emulator=='ryujinx':
   expected=record['source_stats'][1]
  elif i==0:expected=record['stat_identity']
  else:expected=record['verified_records'][i-1]['stat_identity']
  if current!=expected:raise ValueError('Installed content identity changed: '+p)
  result.append({'path':p,'identity':current})
 if len({r['path'] for r in result})!=len(result):raise ValueError('Repeated installed-file path')
 return result

def build(app,emulator,records,all_records,configuration=None):
 if emulator not in ('ryujinx','citron-emu') or not re.fullmatch('[0-9A-F]{16}',app):
  raise ValueError('Supported emulator and internal application ID required')
 rows=[];seen=set()
 for r in records:
  if r.get('application_id','').upper()!=app:continue
  files=checked(r,emulator);title=r['title_id'].upper();kind=r['content_type']
  if emulator=='ryujinx':token=r['registered_path'].encode().hex()
  elif kind=='dlc':token='citron:dlc:'+app+':'+title
  else:
   token='citron:update:'+title+':'+str(r['internal_version'])+':'+r['origin']+':'+hashlib.sha256(r['path'].encode()).hexdigest()[:16]
  key=(kind,token)
  if key in seen:
   row=next(x for x in rows if (x['content_type'],x['token'])==key)
   row['registrations'].append({'origin':r.get('origin'),'files':files,'registered_root':r.get('registered_root')})
   continue
  seen.add(key)
  rows.append({'content_type':kind,'title_id':title,'token':token,
   'registrations':[{'origin':r.get('origin'),'files':files,'registered_root':r.get('registered_root')}],
   'package_integrity': 'UNASSESSED', 'originals_preserved':True})
 for row in rows:
  allfiles=[f for registration in row['registrations'] for f in registration['files']]
  inode_keys={tuple(f['identity'][:2]) for f in allfiles}
  shared=[]
  for other in all_records:
   if (other.get('application_id','').upper()==app and other.get('title_id','').upper()==row['title_id']):continue
   for f in paths(other,emulator):
    try:linked=tuple(identity(f)[:2]) in inode_keys
    except FileNotFoundError:continue
    if linked:shared.append(f)
  row['shared_with_other_titles']=sorted(set(shared))
  if emulator=='ryujinx':
   row['uninstall_strategy']='REMOVE_SELECTED_REGISTRATION_ONLY'
   row['preserve_files']=sorted({f['path'] for f in allfiles})
   row['configuration_path']=(configuration or {}).get('dlc' if row['content_type']=='dlc' else 'updates',{}).get('path')
   row['install_locally_candidates']=[f['path'] for f in allfiles if not safe(f['path']).is_relative_to(MANAGED)]
  else:
   strategies=[]
   for registration in row['registrations']:
    if registration['origin'] in ('UserNAND','SDMC'):strategies.append('QUARANTINE_UNSHARED_CONTENT_FILES')
    elif registration['origin']=='External' and all(safe(f['path']).is_relative_to(MANAGED) for f in registration['files']):
     strategies.append('PRESERVE_PACKAGE_AND_REMOVE_MANAGED_REGISTRATION_ALIAS')
    else:strategies.append('REQUIRES_EXTERNAL_REGISTRATION_ADAPTER')
   row['uninstall_strategies']=strategies
   row['install_locally_file_sets']=[reg['files'] for reg in row['registrations'] if reg['origin'] in ('UserNAND','SDMC')]
 return {'schema':'GCM_SWITCH_CONTENT_ACTION_PLAN_V1','application_id':app,'emulator':emulator,
  'mutation_authorized':False,'rows':rows}

def select(plan,tokens,kind='dlc'):
 if not tokens or len(tokens)!=len(set(tokens)):raise ValueError('Select unique installed items')
 rows={r['token']:r for r in plan['rows'] if r['content_type']==kind}
 if any(t not in rows for t in tokens):raise ValueError('Selected item is not registered for this game and type')
 return [rows[t] for t in tokens]

def inspect(rom,emulator):
 emulator={'ryujinx-emu':'ryujinx','citron':'citron-emu'}.get(emulator,emulator)
 base=safe(rom);before=identity(base)
 app=load('gcm-switch-base-identity.py').identify(str(base))['application_id'].upper()
 if emulator=='ryujinx':
  game=load('ryujinx-installed-integrity-v02.py').validate(str(base))
  records=game['records'];all_records=records
  config=game['native_snapshot']['configuration']
  if game.get('problems'):raise ValueError('Resolve unverified registrations before action planning')
  if game['native_snapshot']['application_id'].upper()!=app:raise ValueError('Game identity disagrees')
 elif emulator=='citron-emu':
  inventory=load('citron-installed-integrity.py').validate()
  all_records=inventory['records'];records=[r for r in all_records if r['application_id'].upper()==app];config=None
 else:raise ValueError('Switch adapter unavailable')
 result=build(app,emulator,records,all_records,config)
 if identity(base)!=before:raise ValueError('Base changed during planning')
 for r in records:checked(r,emulator)
 result['base_identity']=before
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rom',required=True)
 p.add_argument('--emulator',required=True,choices=('ryujinx','ryujinx-emu','citron-emu','citron'))
 a=p.parse_args();print(json.dumps(inspect(a.rom,a.emulator),indent=2))
 print('Read-only plan; no registration, copy, uninstall or recovery changes')

if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR:',str(exc));raise SystemExit(1)
