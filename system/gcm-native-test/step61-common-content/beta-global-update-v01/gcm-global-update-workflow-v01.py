"""Import emulator-installed updates; copied-file evidence is not publisher authentication."""
import argparse,hashlib,importlib.util,json,os,shutil,stat,time,uuid
from pathlib import Path
COMMON=Path('/userdata/system/gcm-native-test/step61-common-content')
DEPENDENCY_SHA='8a8e6621ce70ee6a0e40de461e091b1b1d222fd6d88f2330ae911d80bad9abd5'
def provider():
 p=COMMON/'beta-global-update-uninstall-v02/gcm-global-update-uninstall-beta-v02.py'
 if any(q.is_symlink() for q in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=DEPENDENCY_SHA:raise ValueError('Confirmed installed-update provider changed')
 s=importlib.util.spec_from_file_location('installed_updates',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return configure(m)

def digest(m,path):
 path=m.safe(path);before=m.tree(path)
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1:raise ValueError('Single-link regular content file required')
  if before['.']!=[s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns]:raise ValueError('File changed before opening')
  h=hashlib.sha256()
  with os.fdopen(fd,'rb',closefd=False) as stream:
   for b in iter(lambda:stream.read(4*1024*1024),b''):h.update(b)
  if m.tree(path)!=before:raise ValueError('File changed during read-back verification')
 finally:os.close(fd)
 return h.hexdigest()

def verify_copy(m,root,manifest):
 root=m.safe(root);actual=m.tree(root);wanted={}
 for entry in manifest['entries']:
  rel=entry['path']
  if not rel or Path(rel).is_absolute() or '..' in Path(rel).parts or rel in wanted:raise ValueError('Invalid local-copy manifest path')
  wanted[rel]=entry
 if set(actual)!={'.',*wanted}:raise ValueError('Local-copy file set changed')
 for rel,entry in wanted.items():
  path=m.safe(root/rel)
  if entry['kind']=='directory':
   if not path.is_dir():raise ValueError('Local directory changed')
  elif entry['kind']=='file':
   if not path.is_file() or path.stat().st_size!=entry['size'] or digest(m,path)!=entry['sha256']:raise ValueError('Local-copy bytes changed')
  else:raise ValueError('Unsupported local-copy entry')
 return actual

def native_import(m,rom,e,row,after_copy=None):
 n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;p=n.provider(e);scope=n.scope(rom,e,'update')
 old=row['evidence'].get('record')
 if old:
  p.valid(old,rom)
  if old.get('pending'):raise ValueError('Recover the interrupted native operation first')
  return {'label':row['label'],'state':'ALREADY_MANAGED','item_id':old['item_id']}
 if row['blocked']:raise ValueError(row['blocked'])
 source=m.safe(row['evidence']['metadata']['source_root']);before=p.worker.snapshot(source)
 # Deterministic transaction identity permits recovery of a copied-but-unpublished import.
 key=hashlib.sha256(json.dumps({'scope':scope,'source':str(source),'snapshot':before},sort_keys=True).encode()).hexdigest()
 parent=p.state/'prepared'/key;receipt=p.path(key)
 if receipt.exists():raise ValueError('Earlier managed receipt needs review before a new adoption')
 binding=row['evidence']['metadata']['binding'];total=sum(v[2] for v in before['files'].values());p.worker.check_space(p.state,total)
 imported=None
 for recordfile in sorted(parent.glob('*/record.json')):
  record=m.read(recordfile);root=m.safe(record['managed_root'])
  if root.parent!=recordfile.parent or root.name!='files' or record['binding']!=binding or record['original_root']!=str(source):raise ValueError('Interrupted local import binding changed')
  if record['source_snapshot']!=p.copy.tree(source):raise ValueError('Original update changed after an interrupted copy')
  verify_copy(m,root,record['manifest']);imported=record;break
 if imported is None:imported=p.copy.import_tree(source,parent,binding,getattr(m,'progress',None))
 root=m.safe(imported['managed_root'])
 if after_copy:after_copy()
 # Bind the ordinary lifecycle provider to the verified persistent local copy.
 # The existing installed directory becomes its active target; it is never overwritten here.
 b=p.source(rom,root,'update')
 if m.safe(b['target'])!=source:raise ValueError('Installed native path differs from lifecycle target; content preserved')
 if p.worker.snapshot(source)!=before or n.scope(rom,e,'update')!=scope or row not in m.collect(rom,e):raise ValueError('Selected game or installed update changed while copying')
 prepared=p.worker.snapshot(root)
 record={'schema':'GCM_NATIVE_FOLDER_ITEM_BETA_V1','item_id':key,'binding':b,'rom':str(Path(rom).absolute()),
  'prepared_root':str(root),'prepared_snapshot':prepared,'source_snapshot':prepared,'manifest':imported['manifest'],
  'native_snapshot':before,'state':'ENABLED','package_integrity':'UNASSESSED','pending':None,
  'ownership_origin':'ADOPTED_EXISTING_UPDATE','adopted_label':row['label'],'original_installed_root':str(source),'adopted_epoch':time.time()}
 # Atomic receipt publication is the sole change to the live lifecycle view.
 p.valid(record,rom);p.worker.atom(receipt,record)
 return {'label':row['label'],'state':'IMPORTED','item_id':key,'local_root':str(root)}

def archive_folder(m):return m.safe(m.COMMON/'state/imported-update-archives')
def roots_for(row,e):
 if e in ('xenia','azahar'):return [Path(p) for p in row['moves']]
 if e=='ryujinx':return [Path(row['evidence']['registration'])]
 if e=='shadps4':return [Path(p) for p in row['moves']]
 return [Path(p) for p in sorted({f['path'] for reg in row['evidence']['plan']['registrations'] for f in reg['files']})]

def archive_import(m,rom,e,row,after_copy=None):
 if row['blocked']:raise ValueError(row['blocked'])
 roots=[m.safe(p) for p in roots_for(row,e)]
 if not roots or any(not p.exists() for p in roots):raise ValueError('Installed content or registered package is missing; locate the original first')
 originals={str(p):m.tree(p) for p in roots};identifier=hashlib.sha256(json.dumps({'rom':str(rom),'e':e,'origins':originals},sort_keys=True).encode()).hexdigest()
 folder=archive_folder(m);folder.mkdir(mode=0o700,parents=True,exist_ok=True);dest=folder/identifier;recordpath=dest/'record.json'
 if recordpath.exists():
  record=m.read(recordpath);check_archive(m,record,recordpath);return {'label':row['label'],'state':'ALREADY_MANAGED','local_root':str(dest)}
 if dest.exists():raise ValueError('Interrupted archive requires review; original content preserved')
 n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;p=n.provider('cemu')
 total=sum(v[3] for snap in originals.values() for v in snap.values() if stat.S_ISREG(v[2]));p.worker.check_space(folder,total)
 work=folder/('.import-'+uuid.uuid4().hex);work.mkdir(mode=0o700);groups=[]
 try:
  for i,source in enumerate(roots):
   parent=work/str(i);parent.mkdir(mode=0o700)
   if source.is_dir():
    binding={'schema':'GCM_EXISTING_CONTENT_BINDING_V1','emulator':e,'application_id':application_id(m,rom,e),'content_type':'update','game_relationship_verified':True}
    imported=p.copy.import_tree(source,parent,binding,getattr(m,'progress',None));local=Path(imported['managed_root']);manifest=imported['manifest'];verify_copy(m,local,manifest)
    groups.append({'original':str(source),'local':str(local.relative_to(work)),'manifest':manifest,'directory':True,'snapshot':m.tree(local)})
   else:
    local=parent/source.name;before=digest(m,source)
    # Exclusive destination, original read-only, bounded by the current source size.
    fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
    try:
     with os.fdopen(fd,'rb',closefd=False) as src,local.open('xb') as dst:
      remaining=source.stat().st_size
      while remaining:
       block=src.read(min(4*1024*1024,remaining))
       if not block:raise ValueError('Installed file truncated while copying')
       dst.write(block);remaining-=len(block)
      if src.read(1):raise ValueError('Installed file grew while copying')
      dst.flush();os.fsync(dst.fileno())
    finally:os.close(fd)
    if digest(m,local)!=before or m.tree(source)!=originals[str(source)]:raise ValueError('Installed copy changed while reading back')
    groups.append({'original':str(source),'local':str(local.relative_to(work)),'directory':False,'sha256':before,'snapshot':m.tree(local)})
  if after_copy:after_copy()
  if any(m.tree(path)!=snap for path,snap in originals.items()) or row not in m.collect(rom,e):raise ValueError('Installed update changed before archive publication')
  record={'schema':'GCM_EXISTING_UPDATE_ARCHIVE_V1','rom':str(Path(rom).absolute()),'emulator':e,'application_id':application_id(m,rom,e),
   'label':row['label'],'native_evidence':row['evidence'],'registration_evidence':capture_registration(m,rom,e,row),'original_token':row['token'],'originals':originals,'groups':groups,'local_copy_verified':True,'package_authentication':'UNASSESSED','created_epoch':time.time()}
  p.copy.atomic_json(work/'record.json',record);p.copy.sync(work);os.rename(work,dest);p.copy.sync(folder)
  # Directory rename changes root ctime only; descendant file identities remain bound.
  check_archive(m,record,dest/'record.json')
  return {'label':row['label'],'state':'IMPORTED','local_root':str(dest)}
 except BaseException:
  if work.exists():shutil.rmtree(work)
  raise

def application_id(m,rom,e):
 if e in profile_module().EXTRA:return profile_module().identify(m,rom,e)['application_id']
 if e in ('rpcs3','cemu','vita3k'):
  n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;return n.scope(rom,e,'update')['identity']['application_id']
 if e=='shadps4':return m.load(m.COMMON/'gcm-ps4-base-identity.py').identify(rom)['application_id']
 if e=='ryujinx':return m.ryu_snapshot(rom)['application_id']
 return raw_citron(m,rom)['app']

def check_archive(m,record,path,deep=True):
 path=m.safe(path);dest=path.parent
 if (path.name!='record.json' or dest.parent!=archive_folder(m) or record.get('schema')!='GCM_EXISTING_UPDATE_ARCHIVE_V1'
     or len(dest.name)!=64 or any(c not in '0123456789abcdef' for c in dest.name)):raise ValueError('Invalid imported update archive')
 for group in record['groups']:
  relative=Path(group['local'])
  if relative.is_absolute() or '..' in relative.parts:raise ValueError('Archive path escapes local directory')
  local=m.safe(dest/relative)
  if m.tree(local)!=group['snapshot']:raise ValueError('Managed archive changed; review required')
  if deep:
   if group['directory']:verify_copy(m,local,group['manifest'])
   elif digest(m,local)!=group['sha256']:raise ValueError('Managed archive bytes changed')
 return record

def import_updates(m,rom,emulator,confirmed=False,after_copy=None):
 if confirmed is not True:raise ValueError('Explicit copy confirmation required')
 e=m.canonical(emulator);rom=str(m.safe(Path(rom).absolute()));result=[]
 with m.lock(),m.compatibility_locks(e):
  m.idle()
  for receipt in (m.COMMON/'state/beta-native-folder-actions/items').glob('*/record.json'):
   if m.read(receipt).get('pending'):raise ValueError('Recover the pending native transaction before importing updates')
  for folder in ('global-content-actions','global-install-locally-jobs','global-uninstall-jobs','beta-native-folder-jobs'):
   for path in (m.COMMON/'state'/folder).rglob('*.json'):
    if m.read(path).get('recovery_required'):raise ValueError('Existing content operation requires recovery before import')
  m.allow_native_assessment=True
  try:rows=m.collect(rom,e)
  finally:m.allow_native_assessment=False
  for row in rows:
   try:
    result.append(native_import(m,rom,e,row,after_copy) if e in ('rpcs3','cemu','vita3k') else archive_import(m,rom,e,row,after_copy))
   except (OSError,ValueError,KeyError) as exc:result.append({'label':row['label'],'state':'REVIEW_REQUIRED','note':str(exc)})
 return result

def catalog(m,rom,emulator):
 e=m.canonical(emulator);rom=str(m.safe(Path(rom).absolute()));app=application_id(m,rom,e);result=[]
 for path in sorted(archive_folder(m).glob('*/record.json')):
  record=m.read(path)
  if record.get('rom')!=rom or record.get('emulator')!=e:continue
  if record.get('application_id')!=app:raise ValueError('Imported archive belongs to an earlier game identity')
  check_archive(m,record,path,deep=False)
  result.append({'section':'AVAILABLE','state':'IMPORTED_ARCHIVE','label':record['label']+' (local copy)','token':'archive:'+path.parent.name})
 return result

ROOT=Path(__file__).resolve().parent
PROFILES_SHA='5809fee6b9c3622456c65f62ab784da7c2e2c6a3a932775e7cd03ae5626f31f4'
EXTRA_PINS={'step61-common-content/gcm-xbox-content-metadata.py': 'c0fcfe71406a65d0037b74b633cd6f879c9ee432b272d27885d617749459ecde', 'step61-common-content/gcm-cia-metadata-v02.py': '0a6f9554f3a78730b314b5dd5fc87087b74f07c02a43c71d8a1e1575ae4068ab', 'step61-common-content/gcm-cia-payload-verify-v02.py': '5a131cf7c8fe65516db8e2860ce3fd4f3f322a19f4def83b65251f133977e9f6', 'step61-common-content/gcm-content-stream-checks.py': 'b18fefd1c1b8645bea59c681beb20fc235a242e5bb72c74322d09fe74a4602e7', 'step60-shared-switch/ryujinx-installed-integrity-v02.py': 'ced277370d7f282dba78c596d727601401ff24e91e16ad98f204fe06fb3600e0'}
def profile_module():
 path=ROOT/'gcm-update-profiles-v01.py'
 if any(p.is_symlink() for p in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=PROFILES_SHA:raise ValueError('Update profile code changed')
 s=importlib.util.spec_from_file_location('gcm_profiles',path);p=importlib.util.module_from_spec(s);s.loader.exec_module(p);return p

def configure(m):
 p=profile_module();oldcanon=m.canonical;oldcollect=m.collect;oldrestore=m.check_restore;oldexecute=m.execute
 m.PINS={**m.PINS,**EXTRA_PINS};m._own_job=None
 def canon(e):
  e=p.canonical(e)
  return e if e in p.EXTRA else oldcanon(e)
 def collect(rom,e):
  e=canon(e)
  if e in p.EXTRA:return p.collect(m,rom,e)
  try:return oldcollect(rom,e)
  except (ValueError,OSError):
   if e!='citron-emu' or not getattr(m,'allow_native_assessment',False):raise
   assess_citron(m);return oldcollect(rom,e)
 def restore_check(rom,e,path,d=None):
  e=canon(e)
  if e not in p.EXTRA:return oldrestore(rom,e,path,d)
  path=m.safe(path);d=d or m.read(path);meta=d.get('metadata',{})
  if path.parent!=m.COMMON/'state/global-update-uninstall/transactions' or d.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or d.get('status')!='COMPLETE':raise ValueError('Completed game-bound uninstall journal required')
  if meta.get('rom')!=str(Path(rom).absolute()) or meta.get('emulator')!=e or meta.get('game_identity')!=p.identify(m,rom,e):raise ValueError('Retained update game/storage identity changed')
  for move in d['moves']:
   src=m.safe(move['source']);q=m.safe(move['quarantine']);p.authorize_destination(m,rom,e,{'application_id':meta['game_identity']['application_id']},src)
   if src.exists() or not q.exists() or not m.engine().matches(m.tree(q),move['snapshot'],renamed=True):raise ValueError('Retained content changed or its destination is occupied')
  return d
 def execute(rom,e,tokens,confirmed=False,after_step=None):
  e=canon(e)
  if e not in p.EXTRA:return oldexecute(rom,e,tokens,confirmed,after_step)
  if confirmed is not True:raise ValueError('Explicit uninstall confirmation required')
  with m.lock(),m.compatibility_locks(e):
   m.idle();base=p.identify(m,rom,e);selected=m.plan(rom,e,tokens);moves=[];identifier=uuid.uuid4().hex
   for row in selected:
    for value in row['moves']:
     src=m.safe(value);p.authorize_destination(m,rom,e,{'application_id':base['application_id']},src)
     parking=m.safe(src.parent/'.gcm-uninstalled-updates'/identifier);parking.mkdir(mode=0o700,parents=True,exist_ok=True)
     moves.append({'source':str(src),'quarantine':str(parking/src.name),'snapshot':m.tree(src)})
   path=m.COMMON/'state/global-update-uninstall/transactions'/(identifier+'.json');path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   def check():
    m.idle()
    if p.identify(m,rom,e)!=base or m.plan(rom,e,tokens)!=selected:raise ValueError('Selected update changed before uninstall')
   m.transaction().apply(path,moves,[],{'rom':str(Path(rom).absolute()),'emulator':e,'game_identity':base,'tokens':tokens,'labels':[r['label'] for r in selected]},check_before=check,after_step=after_step)
   return {'status':'PASS','journal':str(path),'count':len(tokens)}
 def idle():
  for path in Path('/proc').glob('[0-9]*/comm'):
   try:name=path.read_text().strip().casefold()
   except (FileNotFoundError,ProcessLookupError,PermissionError):continue
   if name.startswith(('rpcs3','cemu','vita3k','shadps4','ryujinx','citron','yuzu','azahar','citra','xenia','dolphin','ppsspp')):raise ValueError('Close emulators before managing updates')
  for folder in ('global-content-actions','global-install-locally-jobs','global-uninstall-jobs','beta-native-folder-jobs','platform-version-install-jobs','platform-dlc-install-jobs'):
   for path in (m.COMMON/'state'/folder).rglob('*.json'):
    d=m.read(path)
    if m._own_job and str(path)==m._own_job['path'] and d.get('nonce')==m._own_job['nonce'] and d.get('pid')==os.getpid():continue
    if d.get('status')=='RUNNING' or d.get('recovery_required'):raise ValueError('Another content operation is active or requires recovery')
  for path in (m.COMMON/'state/global-update-uninstall/transactions').glob('*.json'):
   if m.read(path).get('status') not in ('COMPLETE','ROLLED_BACK'):raise ValueError('Interrupted update operation needs recovery')
 m.canonical=canon;m.collect=collect;m.check_restore=restore_check;m.execute=execute;m.idle=idle
 return m

def assess_citron(m):
 # Expensive native metadata/payload assessment runs only after Copy and Verify confirmation.
 provider=m.load(m.COMMON/'gcm-switch-dlc-uninstall-v01.py');planner=provider.load('gcm-switch-content-action-plan-v01.py');integrity=planner.load('citron-installed-integrity.py');catalog=integrity.catalog_module()
 catalog.refresh();integrity.refresh()

def raw_citron(m,rom):
 try:return m.citron_context(rom)[1]
 except (ValueError,OSError):
  provider=m.load(m.COMMON/'gcm-switch-dlc-uninstall-v01.py');planner=provider.load('gcm-switch-content-action-plan-v01.py');integrity=planner.load('citron-installed-integrity.py');catalog=integrity.catalog_module()
  app=planner.load('gcm-switch-base-identity.py').identify(str(rom))['application_id'].upper()
  inventory=m.read(integrity.STORE);metadata=m.read(catalog.STORE);now=catalog.capture()
  return {'app':app,'records':[r for r in inventory['records'] if r['application_id'].upper()==app],'all_records':inventory['records'],
   'inventory':inventory,'metadata':metadata,'current':now,'store':str(integrity.STORE),'catalog_store':str(catalog.STORE),
   'config':str(catalog.CONFIG),'config_bytes':catalog.CONFIG.read_bytes()}

def capture_registration(m,rom,e,row):
 if e=='ryujinx':return {'snapshot':m.ryu_snapshot(rom),'original_path':row['evidence']['registration']}
 if e=='citron-emu':
  c=raw_citron(m,rom);own={f['path'] for reg in row['evidence']['plan']['registrations'] for f in reg['files']}
  return {'roots':c['current']['roots'],'config_sha256':hashlib.sha256(c.get('config_bytes',b'')).hexdigest(),
   'records':[r for r in c['records'] if r['content_type']=='update' and r['path'] in own],
   'metadata_records':[r for r in c['metadata']['records'] if r['content_type']=='update' and r['path'] in own]}
 return {}

def selected_archive(m,rom,e,token):
 if not token.startswith('archive:') or len(token)!=72 or any(c not in '0123456789abcdef' for c in token[8:]):raise ValueError('Managed update token required')
 path=archive_folder(m)/token[8:]/'record.json';record=m.read(path);e=m.canonical(e)
 if record.get('rom')!=str(Path(rom).absolute()) or record.get('emulator')!=e or record.get('application_id')!=application_id(m,rom,e):raise ValueError('Managed copy does not match selected game')
 check_archive(m,record,path);return path,record

def destination_ok(m,rom,e,record,destination):
 p=profile_module();dst=m.safe(destination);app=application_id(m,rom,e)
 if e in p.EXTRA:return p.authorize_destination(m,rom,e,record,dst)
 if e=='shadps4':
  identity=m.load(m.COMMON/'gcm-ps4-base-identity.py').identify(rom);base=m.safe(identity['metadata_path']).parent.parent
  overlays=(base.with_name(base.name+'-UPDATE'),base.with_name(base.name+'-patch'))
  store=m.SYSTEM/'system/game-content/ps4'/app/'installed-updates'
  if dst in overlays:return
  if dst==m.COMMON/'state/ps4-update-selection'/(app+'.json'):return
  if dst.parent==store and len(dst.stem)==64 and all(c in '0123456789abcdef' for c in dst.stem) and dst.suffix in ('','.json'):return
 elif e=='citron-emu':
  ctx=raw_citron(m,rom);proof=record['registration_evidence']
  if ctx['current']['roots']!=proof['roots'] or hashlib.sha256(ctx.get('config_bytes',b'')).hexdigest()!=proof['config_sha256']:raise ValueError('Citron content roots changed; reassess the installation')
  owned={r['path'] for r in proof['records']}
  owned.update(f['path'] for r in proof['records'] for f in r.get('verified_records',[]))
  if str(dst) in owned and any(dst.is_relative_to(Path(r['path'])) for r in proof['roots']):return
 raise ValueError('Backup target is outside the selected game native storage')

def source_matches(m,path,group):
 if not path.exists():return False
 try:
  if group['directory']:verify_copy(m,path,group['manifest']);return True
  return path.is_file() and digest(m,path)==group['sha256']
 except (OSError,ValueError):return False

def refresh_citron(m,rom,record,token=None):
 ctx=raw_citron(m,rom);proof=record['registration_evidence'];own={r['path'] for r in proof['records']};copies={}
 for group in record['groups']:copies[group['original']]=group
 def fileid(path):
  s=m.safe(path).stat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
 changes=[]
 for path,doc,records in ((ctx['store'],ctx['inventory'],proof['records']),(ctx['catalog_store'],ctx['metadata'],proof['metadata_records'])):
  after=json.loads(json.dumps(doc));after['inventory']=ctx['current'];after['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
  # Every unrelated stored file must still match before carrying its evidence forward.
  own_files=set(copies)
  oldfiles=doc.get('inventory',{}).get('files',{})
  for file,value in ctx['current']['files'].items():
   if file not in own_files and oldfiles.get(file)!=value:raise ValueError('Other Citron content changed; refresh its inventory first')
  for file in oldfiles:
   if file not in own_files and file not in ctx['current']['files']:raise ValueError('Other Citron content was removed; refresh inventory first')
  after['records']=[r for r in after['records'] if r['path'] not in own]
  for r in records:
   r=json.loads(json.dumps(r));r['stat_identity']=fileid(r['path'])
   for f in r.get('verified_records',[]):f['stat_identity']=fileid(f['path'])
   after['records'].append(r)
  changes.append({'path':path,'before':m.transaction().raw(path),'after':m.packed(after)})
 ledger=m.COMMON/'state/global-update-uninstall/transactions'/(uuid.uuid4().hex+'.json')
 m.transaction().apply(ledger,[],changes,{'rom':str(rom),'emulator':'citron-emu','tokens':[],'kind':'IMPORTED_UPDATE_EVIDENCE_REFRESH','archive_token':token})

def install_archive(m,rom,emulator,token,confirmed=False,after_step=None):
 if confirmed is not True:raise ValueError('Explicit install confirmation required')
 e=m.canonical(emulator);rom=str(m.safe(Path(rom).absolute()))
 with m.lock(),m.compatibility_locks(e):
  m.idle();path,record=selected_archive(m,rom,e,token)
  # Prefer the proven combined content/config rollback when this was a GCM uninstall.
  for retained in m.retained_journals(rom,e):
   if retained['blocked']:continue
   journal=m.read(m.COMMON/'state/global-update-uninstall/transactions'/(retained['token'][8:]+'.json'))
   if e!='ryujinx' and len(journal.get('metadata',{}).get('tokens',[]))==1 and any(move['source'] in record['originals'] for move in journal['moves']):
    # Already hold the exact locks expected by restore_update; recover the validated journal directly.
    m.check_restore(rom,e,m.COMMON/'state/global-update-uninstall/transactions'/(retained['token'][8:]+'.json'),journal)
    if e=='citron-emu':m.citron_restore_evidence(journal,prepare=True)
    m.transaction().recover(m.COMMON/'state/global-update-uninstall/transactions'/(retained['token'][8:]+'.json'))
    if e=='citron-emu':m.citron_restore_evidence(journal)
    return {'status':'PASS','restored_journal':retained['token']}
  if e=='ryujinx':
   current=m.ryu_snapshot(rom);config=current['configuration']['updates'];before=m.transaction().raw(config['path']);doc=json.loads(before)
   local=path.parent/record['groups'][0]['local']
   if not local.is_file():raise ValueError('Managed update package is missing')
   after=json.loads(json.dumps(doc));original=record['registration_evidence']['original_path'];managed=str(local)
   after['paths']=list(dict.fromkeys(managed if p==original else p for p in after.get('paths',[])))
   if managed not in after['paths']:after['paths'].append(managed)
   if after.get('selected') in (None,'',original):after['selected']=managed
   journal=m.COMMON/'state/global-update-uninstall/transactions'/(uuid.uuid4().hex+'.json');journal.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   def guard():
    if m.ryu_snapshot(rom)!=current or m.transaction().raw(config['path'])!=before:raise ValueError('Ryujinx registration changed before installation')
   m.transaction().apply(journal,[],[{'path':config['path'],'before':before,'after':m.packed(after)}],{'rom':rom,'emulator':e,'tokens':[],'kind':'IMPORTED_UPDATE_INSTALL','archive_token':token},check_before=guard,after_step=after_step)
   # Refresh through the existing verified inventory implementation; never invent proof.
   integrity=m.load(m.SHARED/'ryujinx-installed-integrity-v02.py')
   actual_store=Path(integrity.STORE);cached=m.transaction().raw(actual_store);old=m.read(actual_store) if cached else {}
   scratch=m.safe(m.COMMON/'state/imported-update-verification');scratch.mkdir(mode=0o700,parents=True,exist_ok=True);temporary=scratch/(uuid.uuid4().hex+'.json');integrity.STORE=temporary
   try:
    fresh=integrity.refresh([rom]);merged=json.loads(json.dumps(fresh))
    if old.get('boot_id')==fresh.get('boot_id') and old.get('schema')==fresh.get('schema'):merged['games']={**old.get('games',{}),**fresh.get('games',{})}
    if m.transaction().raw(actual_store)!=cached:raise ValueError('Ryujinx integrity inventory changed while verifying local registration')
    evidence=m.COMMON/'state/global-update-uninstall/transactions'/(uuid.uuid4().hex+'.json')
    m.transaction().apply(evidence,[],[{'path':str(actual_store),'before':cached,'after':m.packed(merged)}],{'rom':rom,'emulator':e,'tokens':[],'kind':'IMPORTED_UPDATE_EVIDENCE_REFRESH','archive_token':token})
   finally:temporary.unlink(missing_ok=True)
   return {'status':'PASS','local_package':managed}
  # Content roots are staged and read back before any target is changed.
  copies=[];temporaries=[];txid=uuid.uuid4().hex;install_journal=None
  n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;p=n.provider('cemu')
  try:
   for group in record['groups']:
    dst=m.safe(group['original']);destination_ok(m,rom,e,record,dst);local=m.safe(path.parent/group['local'])
    if dst.exists():
     if source_matches(m,dst,group):continue
     raise ValueError('Existing destination differs; it will not be overwritten: '+str(dst))
    dst.parent.mkdir(mode=0o700,parents=True,exist_ok=True);temporary=dst.parent/('.gcm-import-install-'+uuid.uuid4().hex);temporary.mkdir(mode=0o700);temporaries.append(temporary)
    required=sum(entry.get('size',0) for entry in group.get('manifest',{}).get('entries',[])) if group['directory'] else local.stat().st_size;p.worker.check_space(dst.parent,required)
    if group['directory']:
     binding={'schema':'GCM_EXISTING_CONTENT_BINDING_V1','emulator':e,'application_id':record['application_id'],'content_type':'update','game_relationship_verified':True}
     imported=p.copy.import_tree(local,temporary,binding,getattr(m,'progress',None));staged=Path(imported['managed_root']);verify_copy(m,staged,group['manifest'])
    else:
     staged=temporary/dst.name;shutil.copyfile(local,staged)
     with staged.open('rb') as stream:os.fsync(stream.fileno())
     if digest(m,staged)!=group['sha256']:raise ValueError('Installed staging copy failed verification')
    copies.append({'source':str(staged),'quarantine':str(dst),'snapshot':m.tree(staged)})
   def guard():
    selected_archive(m,rom,e,token)
    for group in record['groups']:
     destination_ok(m,rom,e,record,m.safe(group['original']))
    for move in copies:
     if Path(move['quarantine']).exists():raise ValueError('Update destination became occupied before installation')
   journal=m.COMMON/'state/global-update-uninstall/transactions'/(txid+'.json');journal.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   install_journal=journal
   if copies:m.transaction().apply(journal,copies,[],{'rom':rom,'emulator':e,'tokens':[],'kind':'IMPORTED_UPDATE_INSTALL','archive_token':token},check_before=guard,after_step=after_step)
   if e=='citron-emu':refresh_citron(m,rom,record,token)
   return {'status':'PASS','installed_groups':len(copies)}
  finally:
   for temporary in temporaries:
    # Committed targets and rollback evidence are never cleanup candidates.
    recoverable=install_journal is not None and install_journal.exists() and m.read(install_journal).get('status') not in ('COMPLETE','ROLLED_BACK')
    if temporary.exists() and not recoverable:shutil.rmtree(temporary)

def recover_archive(m,rom,emulator,token,confirmed=False):
 if confirmed is not True:raise ValueError('Explicit recovery confirmation required')
 e=m.canonical(emulator)
 with m.lock(),m.compatibility_locks(e):
  path,record=selected_archive(m,rom,e,token);recovered=0
  for journal in sorted((m.COMMON/'state/global-update-uninstall/transactions').glob('*.json')):
   d=m.read(journal);meta=d.get('metadata',{})
   if meta.get('archive_token')!=token or meta.get('rom')!=str(Path(rom).absolute()) or meta.get('emulator')!=e:continue
   if d.get('status') in ('COMPLETE','ROLLED_BACK'):continue
   for move in d.get('moves',[]):
    dst=m.safe(move['quarantine']);source=m.safe(move['source']);destination_ok(m,rom,e,record,dst)
    try:relative=source.relative_to(dst.parent)
    except ValueError:raise ValueError('Recovery staging source is outside target storage')
    if not relative.parts[0].startswith('.gcm-import-install-'):raise ValueError('Recovery source is not an update staging directory')
   allowed=set()
   if e=='ryujinx':allowed={m.ryu_snapshot(rom)['configuration']['updates']['path'],str(m.SHARED/'state/ryujinx-verified-inventory.json')}
   if e=='citron-emu':
    ctx=raw_citron(m,rom);allowed={ctx['store'],ctx['catalog_store']}
   if any(change['path'] not in allowed for change in d.get('changes',[])):raise ValueError('Recovery contains an unrelated configuration path')
   m.transaction().recover(journal);recovered+=1
  return {'status':'PASS','recovered_transactions':recovered}

def menu(m,args):
 e=m.canonical(args.emulator);p=profile_module();kind=args.type
 if args.mode=='summary':print('SUMMARY\t'+(str(len(m.collect(args.rom,e)))+' INSTALLED' if p.CAPABILITIES[e]['update'] else 'N/A')+'\tNOT ASSESSED');return
 if args.mode=='capabilities':print('CAPABILITY\tCOMMIT_AVAILABLE_DLC\t0');print('CAPABILITY\tSELECT_UPDATE\t1');return
 if args.mode!='list':raise ValueError('Unsupported shared extra-provider menu operation')
 if kind!='update':print('STATUS\tDLC\tNo update operation is applied to DLC');return
 for row in m.collect(args.rom,e):print('ROW\tAVAILABLE\tREGISTERED_UNVERIFIED\t'+m.clean(row['label'])+'\t'+row['token'])
 # Candidate rows are supplied by the shared scanner index.
 try:
  jobs=job_module();s=jobs.scope(m,args.rom,e,kind);index=jobs.read_index(m,s)
  for row in index.get('candidates',[]):print('ROW\tDETECTED\tUNVERIFIED\t'+m.clean(row['label'])+'\t'+row['token'])
 except FileNotFoundError:pass

def job_module():
 path=ROOT/'gcm-global-update-jobs-v01.py';expected=JOB_SHA
 if any(p.is_symlink() for p in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Shared update job transport changed')
 s=importlib.util.spec_from_file_location('gcm_update_jobs',path);module=importlib.util.module_from_spec(s);s.loader.exec_module(module);return module
JOB_SHA='abe7556525f6af8d06cdadcef8e08c8fc9de8c12c5af455cc20f947a9e2404f0'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('action',nargs='?',default='menu',choices=('import','list','details','install','recover','menu','remove-api'))
 parser.add_argument('--rom');parser.add_argument('--emulator');parser.add_argument('--token',default='');parser.add_argument('--confirm',action='store_true');parser.add_argument('--mode');parser.add_argument('--type',default='update');args,rest=parser.parse_known_args();m=provider()
 if args.action=='remove-api':
  import sys
  sys.argv=[sys.argv[0],*rest]+(['--rom',args.rom] if args.rom is not None else [])+(['--emulator',args.emulator] if args.emulator is not None else [])+(['--confirm'] if args.confirm else []);m.main();return
 if args.mode:menu(m,args);return
 if args.action=='import':
  e=m.canonical(args.emulator);results=import_updates(m,args.rom,e,args.confirm)
  print('UPDATE IMPORT COMPLETE')
  if not results:print(profile_module().CAPABILITIES.get(e,{}).get('reason','No installed updates found. Scan or restore retained content first.'))
  for row in results:print('\n'+m.clean(row['label'])+' — '+row['state'].replace('_',' '));print(m.clean(row.get('note','Local copy verified and retained. Installed content and originals preserved.')))
  print('\nLocal-copy verification does not claim publisher authentication.')
 elif args.action=='list':
  for row in catalog(m,args.rom,args.emulator):print('\t'.join(('ROW',row['section'],row['state'],m.clean(row['label']),row['token'])))
 elif args.action=='details':
  path,record=selected_archive(m,args.rom,args.emulator,args.token);print('LOCAL COPY VERIFIED\n'+record['label']+'\n'+str(path.parent)+'\nINSTALL uses the retained local copy and never overwrites different content.')
 elif args.action=='recover':recover_archive(m,args.rom,args.emulator,args.token,args.confirm);print('OK\tRECOVERY ASSESSED')
 elif args.action=='install':install_archive(m,args.rom,args.emulator,args.token,args.confirm);print('OK\tINSTALLED')
 else:raise ValueError('Choose a shared update action')
if __name__=='__main__':
 try:main()
 except (OSError,ValueError,KeyError) as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
