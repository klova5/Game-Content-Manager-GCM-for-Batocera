"""Game-bound update removal; preserves packages and journals reversible changes."""
import argparse,contextlib,fcntl,hashlib,importlib.util,json,os,re,stat,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
COMMON=ROOT.parent
SYSTEM=Path('/userdata')
PINS={'step61-common-content/beta-native-folder-batch-v04/gcm-native-game-resolver-beta-v01.py': 'f71c3823b525b47d440c9505d9af5bb2e303a88720b2811687cdeaecfeeaffee', 'step61-common-content/beta-native-folder-batch-v04/gcm-beta-filesystem-space-v01.py': 'd5bc6f049146f9262f2c1f9e4648a86ea0341f6ded160cca31e3d9dd5b368a21', 'step61-common-content/beta-native-folder-batch-v04/gcm-beta-installed-tree-copy-v01.py': 'dd5026ddbf58cd2cf2f8aa91db6ccf1d0a7d94daef271e049f7240db14296d40', 'step61-common-content/beta-native-folder-batch-v04/gcm-wiiu-title-metadata-v02.py': 'c25b772bd748f31b4cfd31dcd5f907b0a97ce0f403cb2e051bd02f7c26b94a2f', 'step61-common-content/beta-native-folder-batch-v04/gcm-native-folder-batch-v01.py': 'c5d07bffd0a65a72920d5263d182922925bda6ee753cc73f690f0f2cb0db9b67', 'step61-common-content/beta-native-folder-batch-v04/gcm-xml-archive-metadata.py': '9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2', 'step61-common-content/beta-native-folder-batch-v04/gcm-beta-rename-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec', 'step61-common-content/beta-native-folder-batch-v04/gcm-native-folder-actions-beta-v01.py': '3ee492e59dc6f8ce33189e6ad74d4d1a08398db51cafa13babefd91a7552aa93', 'step61-common-content/beta-native-folder-batch-v04/gcm-ps3-base-identity.py': 'f8741b41b64ce50fd2d754b88893a7e1227564680e789cf0f0ed90a81971330a', 'step61-common-content/beta-native-folder-batch-v04/gcm-shared-installed-worker-beta-v01.py': '1d51640ca1e38d2382a3748f654b376778fc219d1d987e2c31619f6fac3a7b2b', 'step61-common-content/beta-native-folder-batch-v04/gcm-sfo-metadata.py': '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41', 'step61-common-content/beta-native-folder-batch-v04/gcm-native-folder-inventory-beta-v01.py': 'bdae591705d77a753a973fa2ae7abe9b34f6c410da4139a54af37f0498a3d82d', 'step61-common-content/gcm-uninstall-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec', 'step61-common-content/gcm-content-removal-transaction-v01.py': '16e5c3a5274c6a4e7137fc31ba0b301d48309e64901675d98c975f677d32adb2', 'step61-common-content/gcm-sfo-metadata.py': '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41', 'step61-common-content/gcm-ps4-base-identity.py': 'f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438', 'step61-common-content/gcm-switch-dlc-uninstall-v01.py': '2e2f23c09029e88ee9d35f8823cfc5cfcb7bf880b3ca1ad5987b24eceaa63066', 'step60-shared-switch/ryujinx-native-adapter.py': 'dbf3498d04fb0d38a2c0ed09b5fc34bd6943a7bfec80b8ede2e78351085f1141'}
NATIVE=COMMON/'beta-native-folder-batch-v04'
SHARED=COMMON.parent/'step60-shared-switch'
ALIASES={'ryujinx-emu':'ryujinx','citron':'citron-emu'}
SUPPORTED=('rpcs3','cemu','vita3k','shadps4','ryujinx','citron-emu')
def canonical(e):
 e=ALIASES.get(e,e)
 if e not in SUPPORTED:raise ValueError('Update removal provider unfinished: '+e)
 return e
def safe(p):
 p=Path(p)
 if not p.is_absolute() or str(p)!=os.path.normpath(str(p)) or any(q.is_symlink() for q in (p,*p.parents)):raise ValueError('Absolute nonsymlink path required')
 return p
def load(path):
 path=safe(path);key=str(path.relative_to(COMMON.parent))
 if key not in PINS or hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[key]:raise ValueError('Update removal dependency changed: '+key)
 spec=importlib.util.spec_from_file_location('update_remove_'+path.stem,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def engine():return load(COMMON/'gcm-uninstall-transaction-v01.py')
def transaction():return load(COMMON/'gcm-content-removal-transaction-v01.py')
def native():return load(NATIVE/'gcm-native-folder-batch-v01.py')
def packed(d):return (json.dumps(d,indent=2)+'\n').encode()
def read(p):
 p=safe(p)
 if p.stat().st_size>64*1024*1024:raise ValueError('Oversized record')
 return json.loads(p.read_text())
def tree(p):
 p=safe(p);snapshot=engine().snapshot(p)
 for rel in snapshot:
  f=p if rel=='.' else p/rel
  st=f.lstat()
  if stat.S_ISREG(st.st_mode) and st.st_nlink!=1:raise ValueError('Hardlinked installed content requires review')
 return snapshot
def add(rows,label,moves=(),changes=(),evidence=None,blocked=''):
 row={'label':str(label),'moves':[str(safe(p)) for p in moves],'changes':changes,'evidence':evidence,'blocked':blocked}
 # Tokens bind the selection to exact metadata, file identity and prior configuration.
 row['snapshots']={p:tree(p) for p in row['moves']}
 row['token']='update:'+hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
 rows.append(row)
def ps3_mixed(root):
 # Known entitlement data must not be silently removed with a PS3 update root.
 for p in root.rglob('*'):
  safe(p)
  if p.suffix.lower() in ('.edat','.rap','.rif') or p.name.casefold() in ('exdata','addcont'):
   return 'This PS3 installation includes entitlement or DLC data. A file-level update manifest is required to preserve it.'
 return ''
def native_rows(rom,e):
 m=native();m.COMMON=COMMON;m.SYSTEM=SYSTEM;s=m.scope(rom,e,'update');p=m.provider(e);rows=[];owned=set()
 for r in m.receipts(s):
  b=r['binding'];state=r['state']
  if (b.get('application_id')!=s['identity']['application_id'] or b.get('native_root')!=s['native_root']
      or b.get('identity_evidence',{}).get('base')!=s['identity']['metadata']):raise ValueError('Stored update game/storage binding changed')
  title=s['identity']['application_id'];native_root=Path(s['native_root'])
  if e=='cemu':
   cid=b.get('content_id','')
   if not re.fullmatch('0005000[Ee][0-9A-Fa-f]{8}',cid):raise ValueError('Invalid stored Cemu update title')
   expected=native_root/'usr/title'/cid[:8].lower()/cid[8:].lower()
  elif e=='rpcs3':expected=native_root/'game'/title
  else:expected=native_root/'ux0/patch'/title
  if safe(b['target'])!=expected or safe(r['prepared_root']).parent.parent!=p.state/'prepared'/r['item_id'] or Path(r['prepared_root']).name!='files':raise ValueError('Stored update paths disagree')
  if state=='UNINSTALLED':continue
  if state=='ENABLED':owned.add(b['target'])
  if r.get('pending'):add(rows,Path(b['source_root']).name,evidence=r,blocked='Recover the interrupted content operation first');continue
  if state=='ENABLED':path=safe(b['target'])
  elif state=='DISABLED':path=safe(Path(b['native_root'])/'.gcm-disabled-content'/r['item_id'])
  elif state=='PREPARED':path=safe(r['prepared_root'])
  else:continue
  blocked=ps3_mixed(path) if e=='rpcs3' and state!='PREPARED' and path.exists() else ''
  if path.exists() and p.worker.snapshot(path)!=r['prepared_snapshot' if state=='PREPARED' else 'native_snapshot']:
   blocked='Installed or prepared files changed; assess the current installation before removal'
  moves=[p.path(r['item_id']).parent]
  prepared_container=p.state/'prepared'/r['item_id']
  if prepared_container.exists():moves.append(prepared_container)
  if state!='PREPARED' and path.exists():moves.append(path)
  add(rows,Path(b['source_root']).name+' '+str(b['version']),moves,evidence={'scope':s,'record':r},blocked=blocked)
 doc=p.inventory.inventory(rom,e,'update',SYSTEM)
 for r in doc['rows']:
  if r['source_root'] in owned:continue
  path=safe(r['source_root']);base=safe(Path(rom).absolute())
  if path==base or path in base.parents or base in path.parents:raise ValueError('Installed update overlaps selected base game')
  blocked=ps3_mixed(path) if e=='rpcs3' else ''
  add(rows,doc['application_id']+' '+str(r['version']),[path],evidence={'scope':s,'metadata':r},blocked=blocked)
 return rows

def ryu_snapshot(rom):
 return load(SHARED/'ryujinx-native-adapter.py').snapshot(rom)
def ryujinx_rows(rom):
 current=ryu_snapshot(rom);config=current['configuration']['updates'];doc=config['document'];rows=[]
 for path in doc.get('paths',[]):
  # Removing a registration is safe even when the package cannot be verified or is missing.
  after=json.loads(json.dumps(doc));after['paths']=[p for p in doc['paths'] if p!=path]
  if after.get('selected')==path:after['selected']=None
  add(rows,Path(path).name,changes=[{'path':config['path'],'before':doc,'after':after}],evidence={'base':current['base_stat_identity'],'application_id':current['application_id'],'registration':path,'dlc':current['configuration']['dlc']})
 return rows

def ps4_rows(rom):
 identity=load(COMMON/'gcm-ps4-base-identity.py').identify(rom);sfo=load(COMMON/'gcm-sfo-metadata.py');base=safe(identity['metadata_path']).parent.parent
 app=identity['application_id'];rows=[];overlays=[]
 for path in (base.with_name(base.name+'-UPDATE'),base.with_name(base.name+'-patch')):
  if not path.exists():continue
  meta=sfo.read(safe(path/'sce_sys/param.sfo'))
  if meta['fields'].get('TITLE_ID')!=app or meta['fields'].get('CATEGORY')!='gp':raise ValueError('PS4 update overlay identity disagrees')
  overlays.append((path,meta))
 store=SYSTEM/'system/game-content/ps4'/app/'installed-updates';covered=set()
 for file in sorted(store.glob('*.json')):
  r=read(file)
  if r.get('schema')!='GCM_PS4_INSTALLED_UPDATE_VERSION_V1' or r.get('application_id')!=app:continue
  root=safe(r['installed_root'])
  if root.parent!=store or root.name!=r['package_sha256']:raise ValueError('PS4 version store root disagrees')
  meta=sfo.read(safe(root/'sce_sys/param.sfo'))
  if meta['fields'].get('TITLE_ID')!=app or meta['fields'].get('CATEGORY')!='gp':raise ValueError('PS4 stored update identity disagrees')
  moves=[root,file]
  receipt=COMMON/'state/ps4-update-selection'/(app+'.json')
  if receipt.exists():
   selection=read(receipt)
   if selection.get('installed_record')==r:
    for path,proof in overlays:
     if str(path)==selection.get('overlay'):moves.append(path);moves.append(receipt);covered.add(str(path))
  add(rows,app+' '+str(r['version']),moves,evidence={'base':identity,'version':r})
 for path,meta in overlays:
  if str(path) not in covered:
   moves=[path];receipt=COMMON/'state/ps4-update-selection'/(app+'.json')
   if receipt.exists() and read(receipt).get('overlay')==str(path):moves.append(receipt)
   add(rows,app+' '+str(meta['fields'].get('APP_VER','')),moves,evidence={'base':identity,'metadata':meta})
 return rows

def citron_context(rom):
 # Reuse the already pinned, game-bound Switch registration evidence.
 provider=load(COMMON/'gcm-switch-dlc-uninstall-v01.py')
 return provider,provider.context(rom,'citron-emu')
def citron_rows(rom):
 provider,ctx=citron_context(rom);rows=[]
 planner=provider.load('gcm-switch-content-action-plan-v01.py')
 plan=planner.build(ctx['app'],'citron-emu',ctx['records'],ctx['all_records'])
 for r in plan['rows']:
  if r['content_type']!='update':continue
  paths=[];blocked=''
  own={f['path'] for reg in r['registrations'] for f in reg['files']}
  other_files=set()
  for record in ctx['all_records']:
   if record['path'] not in own:other_files.update(planner.paths(record,'citron-emu'))
  other_inodes={tuple(ctx['current']['files'][p][:2]) for p in other_files}
  shared={p for p in own if p in other_files or tuple(ctx['current']['files'][p][:2]) in other_inodes}
  for reg in r['registrations']:
   if reg['origin']=='External':blocked='External registration removal requires a dedicated alias; source packages will not be moved.';continue
   metadata={record['path'] for record in ctx['records'] if record['path'] in own}
   if metadata & shared:blocked='Selected update metadata is referenced by another installed item'
   paths.extend(f['path'] for f in reg['files'] if f['path'] not in shared)
  if not paths:blocked=blocked or 'Update has no independently removable native files'
  add(rows,r['title_id'],paths,evidence={'plan':r,'inventory':ctx['current']},blocked=blocked)
 return rows

def collect(rom,emulator):
 e=canonical(emulator);rom=str(safe(Path(rom).absolute()))
 if e in ('rpcs3','cemu','vita3k'):rows=native_rows(rom,e)
 elif e=='ryujinx':rows=ryujinx_rows(rom)
 elif e=='shadps4':rows=ps4_rows(rom)
 else:rows=citron_rows(rom)
 if len(rows)>100:raise ValueError('Too many installed updates; review inventory')
 return rows

def idle():
 for path in Path('/proc').glob('[0-9]*/comm'):
  try:name=path.read_text().strip().casefold()
  except (FileNotFoundError,ProcessLookupError,PermissionError):continue
  if name.startswith(('rpcs3','cemu','vita3k','shadps4','ryujinx','citron','yuzu')):raise ValueError('Close emulators before removing updates')
 for folder in ('global-content-actions','global-install-locally-jobs','global-uninstall-jobs','beta-native-folder-jobs','platform-version-install-jobs','platform-dlc-install-jobs'):
  for file in (COMMON/'state'/folder).rglob('*.json'):
   d=read(file)
   if d.get('status')=='RUNNING':raise ValueError('Another content operation is active; wait or cancel its scan')
 for file in (COMMON/'state/global-update-uninstall/transactions').glob('*.json'):
  if read(file).get('status') not in ('COMPLETE','ROLLED_BACK'):raise ValueError('An interrupted update removal requires recovery')
@contextlib.contextmanager
def lock():
 path=safe(COMMON/'state/global-content-actions/.start.lock');path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
 finally:os.close(fd)
@contextlib.contextmanager
def compatibility_locks(e):
 paths=[]
 if e=='ryujinx':paths=[SHARED/'state/ryujinx-configuration.lock']
 elif e=='citron-emu':paths=[COMMON.parent/'step59-citron-backend-v01/configuration-commit.lock']
 elif e=='shadps4':paths=[COMMON/'state/ps4-update-selection/.lock']
 descriptors=[]
 try:
  for path in paths:
   path=safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);descriptors.append(fd)
   fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  yield
 finally:
  for fd in reversed(descriptors):os.close(fd)

def plan(rom,e,tokens):
 if not tokens or len(tokens)>32 or len(tokens)!=len(set(tokens)):raise ValueError('Select between 1 and 32 unique updates')
 rows={r['token']:r for r in collect(rom,e)}
 if any(t not in rows for t in tokens):raise ValueError('Installed update changed; reopen the uninstall list')
 selected=[rows[t] for t in tokens]
 if any(r['blocked'] for r in selected):raise ValueError(next(r['blocked'] for r in selected if r['blocked']))
 return selected

def execute(rom,e,tokens,confirmed=False,after_step=None):
 if confirmed is not True:raise ValueError('Explicit uninstall confirmation required')
 e=canonical(e)
 with lock(),compatibility_locks(e):
  idle();selected=plan(rom,e,tokens);tx=transaction();eng=engine();changes={};sources=set()
  for row in selected:
   sources.update(row['moves'])
   for c in row['changes']:
    if c['path'] not in changes:changes[c['path']]=json.loads(json.dumps(c))
    elif e=='ryujinx':
     prior=changes[c['path']]['after'];removed=set(c['before']['paths'])-set(c['after']['paths'])
     prior['paths']=[p for p in prior['paths'] if p not in removed]
     if prior.get('selected') in removed:prior['selected']=None
    elif changes[c['path']]!=c:raise ValueError('Conflicting update metadata changes')
  # Refresh current Ryujinx integrity evidence as part of the same registration transaction.
  # Removal itself does not require package verification or a GCM installation receipt.
  if e=='ryujinx':
   integrity_path=SHARED/'state/ryujinx-verified-inventory.json'
   if integrity_path.exists():
    cached=read(integrity_path);after=json.loads(json.dumps(cached));key=str(Path(rom).resolve(strict=True))
    game=after.get('games',{}).get(key)
    if game is not None:
     current=ryu_snapshot(rom);config=current['configuration']['updates'];newdoc=changes[config['path']]['after']
     removed=set(config['document']['paths'])-set(newdoc['paths'])
     snap=json.loads(json.dumps(current));snap['configuration']['updates'].update(document=newdoc,sha256=hashlib.sha256(packed(newdoc)).hexdigest())
     snap['registered']['updates']=[r for r in snap['registered']['updates'] if r['path'] not in removed]
     game['native_snapshot']=snap
     game['records']=[r for r in game.get('records',[]) if not (r.get('content_type')=='update' and r.get('registered_path') in removed)]
     still_registered={r['path'] for kind in ('updates','dlc') for r in snap['registered'][kind]}
     game['file_identities']={p:v for p,v in game.get('file_identities',{}).items() if p not in removed or p in still_registered}
     game['problems']=[r for r in game.get('problems',[]) if not (r.get('content_type')=='update' and r.get('path') in removed)]
     game['all_registered_content_verified']=not game.get('problems')
     changes[str(integrity_path)]={'path':str(integrity_path),'before':cached,'after':after}
  # Citron: preserve shared files; project the existing evidence/catalog atomically.
  if e=='citron-emu':
   provider,ctx=citron_context(rom);wanted={r['evidence']['plan']['token'] for r in selected}
   chosen=[r for r in ctx['records'] if r['content_type']=='update' and ('citron:update:'+r['title_id'].upper()+':'+str(r['internal_version'])+':'+r['origin']+':'+hashlib.sha256(r['path'].encode()).hexdigest()[:16]) in wanted]
   removed=set(sources);newcapture={'roots':ctx['current']['roots'],'files':{p:v for p,v in ctx['current']['files'].items() if p not in removed}}
   for path,doc in ((ctx['store'],ctx['inventory']),(ctx['catalog_store'],ctx['metadata'])):
    after=json.loads(json.dumps(doc));after['inventory']=newcapture;after['records']=[r for r in after['records'] if not any(r['path']==s['path'] for s in chosen)]
    changes[path]={'path':path,'before':doc,'after':after}
  paths=sorted(sources)
  for p in paths:
   if any(Path(p)!=Path(q) and Path(p).is_relative_to(Path(q)) for q in paths):raise ValueError('Overlapping selected roots require reconciliation')
  txid=uuid.uuid4().hex;moves=[]
  for p in paths:
   src=safe(p);parent=src.parent
   if e=='citron-emu':
    roots=[Path(r['path']) for r in ctx['current']['roots'] if src.is_relative_to(Path(r['path']))]
    if not roots:raise ValueError('Update file is outside registered storage')
    parent=min(roots,key=lambda p:len(p.parts)).parent
   parking=safe(parent/'.gcm-uninstalled-updates'/txid)
   if e=='citron-emu' and any(parking.is_relative_to(Path(r['path'])) for r in ctx['current']['roots']):raise ValueError('Quarantine overlaps registered storage')
   parking.mkdir(mode=0o700,parents=True,exist_ok=True)
   # Under native registered stores, use sibling quarantine outside the scanned title root.
   moves.append({'source':p,'quarantine':str(parking/src.name),'snapshot':tree(src)})
  converted=[]
  for c in changes.values():
   before=tx.raw(c['path'])
   if before is None or json.loads(before)!=c['before']:raise ValueError('Update registration changed before removal')
   converted.append({'path':c['path'],'before':before,'after':packed(c['after'])})
  folder=safe(COMMON/'state/global-update-uninstall/transactions');folder.mkdir(mode=0o700,parents=True,exist_ok=True);journal=folder/(txid+'.json')
  def check():
   idle()
   if plan(rom,e,tokens)!=selected:raise ValueError('Update selection changed before commit')
  result=tx.apply(journal,moves,converted,{'emulator':e,'rom':str(rom),'tokens':tokens,'labels':[row['label'] for row in selected],'external_originals_preserved':True},check_before=check,after_step=after_step)
  return {'status':'PASS','journal':str(journal),'count':len(tokens)}
def retained_journals(rom,e):
 e=canonical(e);rom=str(safe(Path(rom).absolute()));rows=[]
 for path in sorted((COMMON/'state/global-update-uninstall/transactions').glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
  d=read(path);meta=d.get('metadata',{})
  if d.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or d.get('status')!='COMPLETE' or meta.get('rom')!=rom or meta.get('emulator')!=e or not meta.get('tokens'):continue
  blocked=''
  try:check_restore(rom,e,path,d)
  except (ValueError,OSError,KeyError,TypeError) as exc:blocked=str(exc)
  count=len(meta.get('tokens',[]));label='Retained update' if count==1 else str(count)+' retained updates (restore together)'
  if count==1:
   if meta.get('labels'):label='Retained '+str(meta['labels'][0])
   else:
    for move in d.get('moves',[]):
     q=Path(move['quarantine'])
     for suffix in ('PARAM.SFO','sce_sys/param.sfo'):
      f=q/suffix
      if f.is_file():
       fields=load(COMMON/'gcm-sfo-metadata.py').read(safe(f))['fields'];label='Retained update '+str(fields.get('APP_VER',''));break
  rows.append({'token':'restore:'+path.stem,'label':label,'blocked':blocked,'count':count,'journal':str(path)})
 return rows

def check_restore(rom,e,path,d=None):
 path=safe(path)
 if path.parent!=COMMON/'state/global-update-uninstall/transactions' or not re.fullmatch('[0-9a-f]{32}',path.stem):raise ValueError('Unknown update recovery journal')
 d=read(path) if d is None else d;meta=d.get('metadata',{})
 if (d.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or d.get('status')!='COMPLETE'
     or meta.get('rom')!=str(safe(Path(rom).absolute())) or meta.get('emulator')!=canonical(e)):raise ValueError('Retained update belongs to another game or emulator')
 # Verify that the current selected game still resolves to the original native destination/configuration.
 if e in ('rpcs3','cemu','vita3k'):
  n=native();n.COMMON=COMMON;n.SYSTEM=SYSTEM;s=n.scope(rom,e,'update');p=n.provider(e);app=s['identity']['application_id'];root=Path(s['native_root'])
  expected=root/'game'/app if e=='rpcs3' else root/'ux0/patch'/app if e=='vita3k' else root/'usr/title/0005000e'/app[8:].lower()
  found=False
  for move in d['moves']:
   source=safe(move['source']);q=safe(move['quarantine'])
   if source==expected:found=True
   elif source.parent==p.state/'items':
    record=read(q/'record.json')
    if record.get('rom')!=str(Path(rom).absolute()) or record.get('binding',{}).get('application_id')!=app:raise ValueError('Retained receipt game identity changed')
    found=True
   elif source.parent!=p.state/'prepared':raise ValueError('Retained update native path changed')
  if not found:raise ValueError('Retained update does not match current game storage')
 elif e=='shadps4':
  identity=load(COMMON/'gcm-ps4-base-identity.py').identify(rom);app=identity['application_id'];base=safe(identity['metadata_path']).parent.parent
  for move in d['moves']:
   source=safe(move['source']);store=SYSTEM/'system/game-content/ps4'/app/'installed-updates'
   if source not in (base.with_name(base.name+'-UPDATE'),base.with_name(base.name+'-patch'),COMMON/'state/ps4-update-selection'/(app+'.json')) and source.parent!=store:raise ValueError('Retained PS4 update game identity changed')
 elif e=='ryujinx':
  configuration=ryu_snapshot(rom)['configuration']['updates']['path']
  if configuration not in [c['path'] for c in d['changes']]:raise ValueError('Retained update registration belongs to another game')
 else:
  provider=load(COMMON/'gcm-switch-dlc-uninstall-v01.py');planner=provider.load('gcm-switch-content-action-plan-v01.py')
  app=planner.load('gcm-switch-base-identity.py').identify(str(rom))['application_id'].upper();found=False;tx=transaction()
  for change in d['changes']:
   before=json.loads(tx.decode(change['before']));after=json.loads(tx.decode(change['after']))
   removed=[r for r in before.get('records',[]) if r not in after.get('records',[])]
   if any(r.get('content_type')!='update' or r.get('application_id','').upper()!=app for r in removed):raise ValueError('Retained Citron update belongs to another game')
   found=found or bool(removed)
  if not found:raise ValueError('Retained Citron update identity is ambiguous')
 tx=transaction();eng=engine()
 for change in d['changes']:
  if tx.raw(change['path'])!=tx.decode(change['after']):raise ValueError('Configuration changed since uninstall; restore needs reconciliation')
 for move in d['moves']:
  source=safe(move['source']);q=safe(move['quarantine'])
  if source.exists():raise ValueError('An installed item already occupies the restore destination')
  if not q.exists() or not eng.matches(eng.snapshot(q),move['snapshot'],renamed=True):raise ValueError('Retained update files changed or are unavailable')
 return d

def citron_restore_evidence(d,prepare=False):
 tx=transaction();restored={m['source']:m['quarantine'] for m in d['moves']};docs=[];expected={}
 for change in d['changes']:
  doc=json.loads(tx.decode(change['before']));docs.append((change['path'],doc))
  for record in doc.get('records',[]):
   if record.get('metadata_sha256'):expected[record['path']]=record['metadata_sha256']
   for f in record.get('verified_records',[]):
    if f.get('sha256'):expected[f['path']]=f['sha256']
 if prepare:
  for source,q in restored.items():
   if source not in expected:raise ValueError('Retained Citron content needs integrity reassessment before restore')
   h=hashlib.sha256()
   with safe(q).open('rb') as f:
    for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
   if h.hexdigest()!=expected[source]:raise ValueError('Retained Citron content hash changed')
  return
 def identity(path):
  s=safe(path).stat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
 changes=[]
 for path,doc in docs:
  before=tx.raw(path)
  if json.loads(before)!=doc:raise ValueError('Citron evidence changed during restore')
  for source in restored:
   if source in doc.get('inventory',{}).get('files',{}):doc['inventory']['files'][source]=identity(source)
  for record in doc.get('records',[]):
   if record['path'] in restored:record['stat_identity']=identity(record['path'])
   for f in record.get('verified_records',[]):
    if f['path'] in restored:f['stat_identity']=identity(f['path'])
  changes.append({'path':path,'before':before,'after':packed(doc)})
 journal=COMMON/'state/global-update-uninstall/transactions'/(uuid.uuid4().hex+'.json')
 tx.apply(journal,[],changes,{'kind':'RESTORE_EVIDENCE_REFRESH','rom':d['metadata']['rom'],'emulator':'citron-emu','tokens':[]})

def restore_update(rom,e,token,confirmed=False):
 if confirmed is not True:raise ValueError('Restore confirmation required')
 e=canonical(e)
 if not re.fullmatch('restore:[0-9a-f]{32}',token):raise ValueError('Select one retained update entry')
 path=COMMON/'state/global-update-uninstall/transactions'/(token[8:]+'.json')
 with lock(),compatibility_locks(e):
  idle();d=check_restore(rom,e,path)
  if e=='citron-emu':citron_restore_evidence(d,prepare=True)
  transaction().recover(path)
  if e=='citron-emu':citron_restore_evidence(d)
 return {'status':'PASS','journal':str(path)}

def clean(s):return ''.join(c if ord(c)>=32 else ' ' for c in str(s))[:1000]
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('list','preview','uninstall','recover','retained','restore'));p.add_argument('--rom');p.add_argument('--emulator');p.add_argument('--tokens',default='');p.add_argument('--confirm',action='store_true');p.add_argument('--journal');a=p.parse_args()
 if a.action=='retained':
  for row in retained_journals(a.rom,a.emulator):print('ROW\tAVAILABLE\t'+('RESTORE_REVIEW' if row['blocked'] else 'RETAINED')+'\t'+clean(row['label'])+'\t'+row['token'])
  return
 if a.action=='restore':
  restore_update(a.rom,a.emulator,a.tokens,a.confirm);print('OK\tRESTORED');return
 if a.action=='recover':
  if not a.confirm:raise ValueError('Recovery confirmation required')
  path=safe(a.journal)
  if path.parent!=COMMON/'state/global-update-uninstall/transactions':raise ValueError('Foreign recovery journal')
  with lock():
   if read(path).get('metadata',{}).get('rom') is None:raise ValueError('Unknown update removal journal')
   transaction().recover(path)
  print('OK\tUpdate removal rolled back');return
 if a.action=='preview' and a.tokens.startswith('restore:'):
  row=next((r for r in retained_journals(a.rom,a.emulator) if r['token']==a.tokens),None)
  if row is None:raise ValueError('Retained update entry no longer available')
  print(row['blocked'] or 'Retained installed content is intact and can be restored. External originals are unchanged.');return
 if a.action=='preview':
  selected={r['token']:r for r in collect(a.rom,a.emulator)}
  tokens=a.tokens.split(',') if a.tokens else []
  if not tokens or any(t not in selected for t in tokens):raise ValueError('Update selection changed; reopen the list')
  for token in tokens:
   r=selected[token];print(r['label']);print(r['blocked'] or 'Installed update can be uninstalled. External originals, base game and saves are preserved. Removed files are retained for recovery.')
  return
 if a.action=='list':
  for r in collect(a.rom,a.emulator):print('ROW\tAVAILABLE\t'+('REVIEW_REQUIRED' if r['blocked'] else 'REMOVABLE')+'\t'+clean(r['label'])+'\t'+r['token'])
  return
 result=execute(a.rom,a.emulator,a.tokens.split(',') if a.tokens else [],a.confirm)
 print('OK\tUNINSTALLED='+str(result['count']))
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+clean(exc));raise SystemExit(1)
