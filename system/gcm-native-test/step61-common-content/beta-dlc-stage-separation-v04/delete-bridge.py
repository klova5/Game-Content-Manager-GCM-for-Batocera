"""Permanent deletion of game-bound GCM-owned copies only; external sources remain."""
import argparse,contextlib,fcntl,hashlib,importlib.util,json,os,re,stat,subprocess,sys,uuid
from pathlib import Path
COMMON=Path('/userdata/system/gcm-native-test/step61-common-content')
WORKFLOW_SHA='dd7dedef3dd55ec6a941f96cf518acc28a2375717bdd7885bcdc5bfe6a606593'
MENU_PINS={'step61-common-content/gcm-global-menu-existing-v01.py': 'ceded0b0b6376fa584ccc69691cbaa6e435c2de98ce4728c7e60eaa1b69fae73', 'step61-common-content/beta-global-update-v01/gcm-global-update-workflow-v01.py': 'dd7dedef3dd55ec6a941f96cf518acc28a2375717bdd7885bcdc5bfe6a606593', 'step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py': '2f9c1bf01577888ebe78202b365add28d66bd25760b2ce61e4ea3d154b595102'}
ACTIVATION_SHA='ceacdca05d3925f6ed21b835d21decbe2347f77c2b3fe1a9028a9b850046a74f'
def dlc_identity():
 p=COMMON/'beta-dlc-stage-separation-v04/gcm-dlc-stage-workflow-v01.py'
 if any(q.is_symlink() for q in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!='585a390df1fcdd106aed119838c43bc996c2d07a31712f19f9497fb145dabc76':raise ValueError('DLC identity delete bridge changed')
 spec=importlib.util.spec_from_file_location('dlc_identity_delete',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def workflow():
 p=COMMON/'beta-global-update-v01/gcm-global-update-workflow-v01.py'
 if any(q.is_symlink() for q in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=WORKFLOW_SHA:raise ValueError('Managed-content dependency changed')
 spec=importlib.util.spec_from_file_location('delete_workflow',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@contextlib.contextmanager
def cache_locks(m):
 descriptors=[]
 try:
  for path in (m.COMMON/'state/verified-content.lock',m.COMMON.parent/'step60-shared-switch/state/verified-content.lock',dlc_identity().LOCK):
   path=m.safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);descriptors.append(fd);fcntl.flock(fd,fcntl.LOCK_EX)
  yield
 finally:
  for fd in reversed(descriptors):os.close(fd)
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def validate_journal(m,path,d):
 j=m.read(path)
 if j.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or j.get('metadata')!={'purpose':'PERMANENT_GCM_DELETE','scope':d['scope'],'job_id':d['job_id']} or fingerprint(j['moves'])!=d.get('move_fingerprint') or fingerprint(j['changes'])!=d.get('cache_fingerprint'):raise ValueError('Delete staging journal binding changed')
 return j

def scope(m,rom,e,kind):
 if kind not in ('update','dlc'):raise ValueError('Update or DLC required')
 e=m.canonical(e);rom=str(m.safe(Path(rom).absolute()));app=workflow().application_id(m,rom,e)
 return {'rom':rom,'emulator':e,'kind':kind,'application_id':app,'base':m.tree(rom)}
def registry(m,s):return m.COMMON/'state/deleted-available-entries'/(fingerprint(s)+'.json')
def suppressed(m,s):
 path=registry(m,s)
 if not path.exists():return {}
 d=m.read(path)
 if d.get('scope')!=s:raise ValueError('Deleted-entry scope differs')
 return d.get('entries',{})
def menu_rows(m,s):
 e=s['emulator']
 relative='step61-common-content/beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py' if e in ('rpcs3','cemu','vita3k') else 'step61-common-content/beta-global-update-v01/gcm-global-update-workflow-v01.py' if e in ('xenia','azahar','dolphin','ppsspp') else 'step61-common-content/gcm-global-menu-existing-v01.py'
 path=m.safe(m.COMMON.parent/relative)
 if hashlib.sha256(path.read_bytes()).hexdigest()!=MENU_PINS[relative]:raise ValueError('Authoritative menu reader changed')
 result=subprocess.run([sys.executable,'-B',str(path),'--rom',s['rom'],'--emulator',e,'--mode','list','--type',s['kind']],capture_output=True,text=True,timeout=120)
 rows=[]
 for line in result.stdout.splitlines():
  fields=line.split('\t')
  if len(fields)==5 and fields[0]=='ROW':rows.append({'section':fields[1],'state':fields[2],'label':fields[3],'token':fields[4]})
 if result.returncode and not rows:raise ValueError('Cannot assess active status before Delete: '+result.stdout[:500])
 if s['kind']=='update':
  for r in workflow().catalog(m,s['rom'],e):rows.append(r)
  for r in m.retained_journals(s['rom'],e):rows.append({'section':'AVAILABLE','state':'REVIEW_REQUIRED' if r['blocked'] else 'RETAINED','label':r['label'],'token':r['token']})
  path=m.safe(m.COMMON/'beta-update-deactivation-v01/gcm-update-deactivation-v01.py')
  if hashlib.sha256(path.read_bytes()).hexdigest()!=ACTIVATION_SHA:raise ValueError('Deactivation reader changed')
  spec=importlib.util.spec_from_file_location('deletion_activation',path);a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
  rows.extend(a.recovery_rows(m,s['rom'],e));rows.extend(a.disabled_rows(m,s['rom'],e))
  if e in ('xenia','azahar'):
   current=m.collect(s['rom'],e)
   if len(current)==1:rows.append({'section':'AVAILABLE','state':'ACTIVE','label':current[0]['label'],'token':current[0]['token']})
 if s['kind']=='dlc' and e in ('ryujinx','citron-emu'):
  text=''.join('\t'.join(('ROW',r['section'],r['state'],r['label'],r['token']))+'\n' for r in rows)
  rows=[dict(zip(('section','state','label','token'),line.split('\t')[1:])) for line in dlc_identity().overlay(s['rom'],e,text).splitlines() if len(line.split('\t'))==5]
 output={r['token']:r for r in rows if r['section']=='AVAILABLE'}
 hidden=suppressed(m,s)
 return [r for t,r in output.items() if t not in hidden]
def source_path(token):
 encoded=token[9:] if token.startswith('existing:') else token
 if not re.fullmatch('(?:[0-9a-f]{2})+',encoded):return None
 try:
  value=bytes.fromhex(encoded).decode();return value if value.startswith('/') else None
 except (ValueError,UnicodeError):return None

def available_inventory(m,s):
 raw=menu_rows(m,s);active={r['token'] for r in raw if r['state'] in ('ACTIVE','ENABLED')};visible=[r for r in raw if r['token'] not in active];owned=owned_inventory(m,s);result=[];covered=set();active_refs=references(m,s,raw)
 for row in owned:
  sources=set(row.get('sources',[]));tokens=set(row.get('gui_tokens',[]));roots=[Path(p) for p in row['roots']]
  for r in raw:
   path=source_path(r['token'])
   if r['token'] in tokens or path and (path in sources or any(Path(path)==p or p in Path(path).parents for p in roots)):tokens.add(r['token'])
  if tokens & active or any(Path(ref)==root or root in Path(ref).parents or Path(ref) in root.parents for root in roots for ref in active_refs):continue
  row['gui_tokens']=sorted(tokens);row['blocked']='' if row['blocked'].startswith('Uninstall ') else row['blocked'];row.pop('token',None);row['token']='delete:'+fingerprint(row)
  result.append(row);covered.update(tokens)
 for r in visible:
  if r['token'] in covered:continue
  # External/review-only Available entries have no owned file to erase.
  # Delete removes their GUI verification/entry only; no source is moved or deleted.
  path=source_path(r['token']);row={'label':r['label'],'roots':[],'snapshots':{},'scope':s,'blocked':'','evidence':[],'evidence_snapshots':{},'sources':[path] if path else [],'gui_tokens':[r['token']]}
  if r['token'].startswith('restore:'):
   journal=m.COMMON/'state/global-update-uninstall/transactions'/(r['token'][8:]+'.json')
   d=m.read(journal)
   if d.get('metadata',{}).get('rom')!=s['rom'] or d.get('metadata',{}).get('emulator')!=s['emulator'] or d.get('status')!='COMPLETE':raise ValueError('Retained-entry identity changed')
   row['evidence']=[str(journal)];row['evidence_snapshots']={str(journal):snap(m,journal)}
  row['token']='delete:'+fingerprint(row);result.append(row)
 return result

def overlay(m,s,text):
 hidden=suppressed(m,s);lines=[]
 for line in text.splitlines():
  fields=line.split('\t')
  if len(fields)==5 and fields[:2]==['ROW','AVAILABLE'] and fields[4] in hidden and fields[2] not in ('ACTIVE','ENABLED'):continue
  lines.append(line)
 return '\n'.join(lines)+'\n'
def verified(m,s,tokens):
 with m.lock():
  path=registry(m,s);entries=suppressed(m,s);sources={p for t in tokens if (p:=source_path(t))}
  if s['emulator'] in ('rpcs3','cemu','vita3k'):
   n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;ns=n.scope(s['rom'],s['emulator'],s['kind']);index=n.index_path(ns)
   if index.exists():sources.update(r['source'] for r in m.read(index).get('candidates',[]) if r['token'] in tokens)
  # A resumed verification job may complete after its original page was closed.
  # Fresh matching proofs can reveal the entry again; the normal inventory still
  # performs its own full proof validation before showing/installing it.
  for store in (m.COMMON/'state/verified-content.json',m.COMMON.parent/'step60-shared-switch/state/verified-content.json'):
   if not store.exists():continue
   for r in m.read(store).get('records',{}).values():
    if r.get('base')==s['rom'] and r.get('application_id')==s['application_id'] and r.get('content_type')==s['kind'] and r.get('status')=='PASS' and r.get('source'):sources.add(r['source'])
  retained={token:r for token,r in entries.items() if token not in tokens and not (set(r.get('sources',[])) & sources) and not any(Path(p).exists() for p in r.get('owned_roots',[]))}
  if retained!=entries:save(m,path,{'scope':s,'entries':retained})
 return 'Verified entries returned to Available'

def managed(m,s):return m.SYSTEM/'system/game-content'/('switch' if s['emulator'] in ('ryujinx','citron-emu') else {'shadps4':'ps4','rpcs3':'ps3','cemu':'wiiu','vita3k':'psvita','xenia':'xbox360','azahar':'3ds','dolphin':'wii','ppsspp':'psp'}[s['emulator']])/s['application_id']
def snap(m,path):
 value=m.engine().snapshot(m.safe(path))
 if len(value)>100000:raise ValueError('Managed copy exceeds deletion entry limit')

 return value

def local_digest(m,path):
 path=m.safe(path);before=snap(m,path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW);h=hashlib.sha256()
 try:
  with os.fdopen(fd,'rb',closefd=False) as stream:
   for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
  if snap(m,path)!=before:raise ValueError('GCM copy changed while identifying its verification records')
 finally:os.close(fd)
 return h.hexdigest()

def references(m,s,raw=()):
 refs=set();active={r['token'] for r in raw if r['state'] in ('ACTIVE','ENABLED')}
 for token in active:
  path=source_path(token)
  if path:refs.add(path)
 if s['emulator'] in ('ryujinx','citron-emu'):
  # Shared packages must also be protected when active in the other Switch emulator.
  roots=(m.SYSTEM/'system/configs/Ryujinx/games',m.SYSTEM/'system/configs/ryujinx/games')
  configs=[]
  if s['emulator']=='ryujinx':configs.append(m.ryu_snapshot(s['rom'])['configuration'])
  for root in roots:
   for path in root.glob('*/updates.json'):
    d=m.read(path)
    if d.get('selected'):refs.add(str(m.safe(d['selected'])))
   for path in root.glob('*/dlc.json'):
    for d in m.read(path):
     if any(x.get('is_enabled',True) for x in d.get('dlc_nca_list',[])):refs.add(str(m.safe(d['path'])))
  for config in configs:
   selected=config['updates']['document'].get('selected')
   if selected:refs.add(str(m.safe(selected)))
   for d in config['dlc']['document']:
    if any(x.get('is_enabled',True) for x in d.get('dlc_nca_list',[])):refs.add(str(m.safe(d['path'])))
  cache=m.COMMON.parent/'step60-shared-switch/state/citron-verified-inventory.json'
  if cache.exists():
   if s['emulator']!='citron-emu':active.update(r['token'] for r in menu_rows(m,{**s,'emulator':'citron-emu'}) if r['state'] in ('ACTIVE','ENABLED'))
   for r in m.read(cache).get('records',[]):
    token='citron:dlc:'+r.get('application_id','')+':'+r.get('title_id','') if r.get('content_type')=='dlc' else 'citron:update:'+r.get('title_id','')+':'+str(r.get('internal_version'))+':'+r.get('origin','')+':'+hashlib.sha256(r.get('path','').encode()).hexdigest()[:16]
    if token in active:
     refs.add(r['path']);refs.update(x['path'] for x in r.get('verified_records',[]))
 return refs

def add(m,s,rows,label,roots,blocked='',evidence=(),sources=(),gui_tokens=()):
 roots=[m.safe(p) for p in roots if Path(p).exists()];evidence=[m.safe(p) for p in evidence]
 if not roots and not evidence:return
 snapshots={str(p):snap(m,p) for p in roots}
 for p in roots:
  base=Path(s['rom'])
  if p==base or p in base.parents or base in p.parents:raise ValueError('Delete selection overlaps the selected base game')
 row={'label':label,'roots':[str(p) for p in roots],'snapshots':snapshots,'scope':s,'blocked':blocked,'evidence':[str(p) for p in evidence], 'evidence_snapshots':{str(p):snap(m,p) for p in evidence},'sources':list(sources),'gui_tokens':list(gui_tokens)}
 row['token']='delete:'+hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest();rows.append(row)

def owned_inventory(m,s):
 rows=[];base=managed(m,s);kind=s['kind'];e=s['emulator'];app=s['application_id']
 if app=='NO_SEPARATE_UPDATE_MODEL':return []
 refs=set()
 def blocked(roots):
  return 'Uninstall this content before deleting its GCM copy' if any(Path(ref)==p or p in Path(ref).parents or Path(ref) in p.parents for p in roots for ref in refs) else ''
 # Exact kind directories only; never infer ownership from arbitrary emulator paths.
 packages=m.safe(base/('updates' if kind=='update' else 'dlc'))
 if packages.exists():
  for p in sorted(packages.iterdir()):
   m.safe(p)
   if p.is_file() and p.suffix.lower() in ('.pkg','.nsp','.xci','.cia'):
    roots=[p];store=base/('installed-updates' if kind=='update' else 'installed-dlc')
    for recordpath in store.glob('*.json'):
     r=m.read(recordpath)
     if r.get('source')!=str(p):continue
     digest=r.get('package_sha256','');expected_schema='GCM_PS4_INSTALLED_UPDATE_VERSION_V1' if kind=='update' else 'GCM_PS4_INSTALLED_DLC_V1'
     if e!='shadps4' or r.get('application_id')!=app or r.get('schema')!=expected_schema or not re.fullmatch('[0-9a-f]{64}',digest) or recordpath.name!=digest+'.json' or r.get('installed_root')!=str(store/digest):raise ValueError('Managed installation record ownership disagrees')
     if (store/digest).exists():roots.append(m.safe(store/digest))
     roots.append(recordpath)
    add(m,s,rows,p.name,roots,'',sources=[str(p)],gui_tokens=[str(p).encode().hex()])
 # Imported installed Switch file sets have their own explicit ownership manifest.
 if e in ('ryujinx','citron-emu'):
  parent=m.safe(base/'imported-installed'/e/kind)
  for recordpath in sorted(parent.glob('*/record.json')):
   r=m.read(recordpath);b=r.get('binding',{});root=recordpath.parent
   if r.get('schema')!='GCM_IMPORTED_INSTALLED_FILE_SET_V1' or b.get('emulator')!=e or b.get('content_type')!=kind or b.get('application_id')!=app or r.get('managed_root')!=str(root/'files'):raise ValueError('Managed Switch copy identity disagrees')
   add(m,s,rows,root.name+' (local file copy)',[root],'',sources=[i['path'] for i in r['files']],gui_tokens=['existing:'+i['path'].encode().hex() for i in r['files']])
 if e=='shadps4':
  parent=m.safe(base/'imported-installed'/kind)
  for recordpath in sorted(parent.glob('*/record.json')):
   r=m.read(recordpath);b=r.get('binding',{});root=recordpath.parent
   if r.get('schema')!='GCM_IMPORTED_INSTALLED_CONTENT_V1' or b.get('content_type')!=kind or b.get('application_id')!=app or r.get('managed_root')!=str(root/'files'):raise ValueError('Managed PS4 copy ownership disagrees')
   source=r.get('original_root','')
   add(m,s,rows,root.name+' (local folder copy)',[root],sources=[source],gui_tokens=['existing:'+source.encode().hex()])
 # Existing-update archives are independently owned backups, not original native files.
 if kind=='update':
  for recordpath in sorted(workflow().archive_folder(m).glob('*/record.json')):
   r=m.read(recordpath)
   if r.get('rom')!=s['rom'] or r.get('emulator')!=e:continue
   if r.get('application_id')!=app:raise ValueError('Imported archive belongs to an earlier game identity')
   if r.get('schema')!='GCM_EXISTING_UPDATE_ARCHIVE_V1' or not re.fullmatch('[0-9a-f]{64}',recordpath.parent.name):raise ValueError('Imported archive ownership record disagrees')
   add(m,s,rows,r['label']+' (local copy)',[recordpath.parent],'',sources=list(r.get('originals',{})),gui_tokens=['archive:'+recordpath.parent.name])
 if e in ('cemu','vita3k','rpcs3') and not (e=='rpcs3' and kind=='dlc'):
  n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;native_scope=n.scope(s['rom'],e,kind);p=n.provider(e)
  for r in n.receipts(native_scope):
   b=r['binding'];identifier=r['item_id'];item=p.path(identifier).parent;prepared=m.safe(p.state/'prepared'/identifier)
   if b.get('application_id')!=app or b.get('content_type')!=kind or b.get('native_root')!=native_scope['native_root']:raise ValueError('Native copy game/storage binding disagrees')
   if not Path(r['prepared_root']).is_relative_to(prepared) or Path(r['prepared_root']).name!='files':raise ValueError('Native copy ownership path disagrees')
   if r['state']=='ENABLED':continue
   reason='Recover the interrupted item first' if r.get('pending') else ''
   add(m,s,rows,r.get('adopted_label',str(b['version']))+' (local folder)',[prepared],reason,[item],sources=[b['source_root']],gui_tokens=['folder:'+identifier])
 # Retained update batches may contain GCM copies moved out of the normal directory.
 # Preserve native/external originals; only exact managed roots and their evidence qualify.
 if kind=='update':
  for journal in sorted((m.COMMON/'state/global-update-uninstall/transactions').glob('*.json')):
   d=m.read(journal);meta=d.get('metadata',{})
   if d.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or d.get('status')!='COMPLETE' or meta.get('rom')!=s['rom'] or meta.get('emulator')!=e:continue
   roots=[]
   for move in d.get('moves',[]):
    src=m.safe(move['source']);q=m.safe(move['quarantine']);owned=any(src.is_relative_to(base/name) and src!=base/name for name in ('updates','installed-updates'))
    if e in ('cemu','vita3k','rpcs3'):
     # A matching quarantined receipt proves ownership of prepared/item slots.
     if src.parent==m.COMMON/'state/beta-native-folder-actions/items':
      r=m.read(q/'record.json');b=r['binding']
      owned=r.get('rom')==s['rom'] and b.get('emulator')==e and b.get('content_type')==kind and b.get('application_id')==app
     elif src.parent==m.COMMON/'state/beta-native-folder-actions/prepared':
      owned=any(Path(x['source'])==m.COMMON/'state/beta-native-folder-actions/items'/src.name and m.read(Path(x['quarantine'])/'record.json').get('rom')==s['rom'] and m.read(Path(x['quarantine'])/'record.json').get('binding',{}).get('content_type')==kind for x in d['moves'])
    if not owned:continue
    expected=src.parent/'.gcm-uninstalled-updates'/journal.stem/src.name
    if q!=expected:raise ValueError('Retained managed copy quarantine path disagrees')
    if not q.exists():continue
    roots.append(q)
   if roots:add(m,s,rows,'Retained '+str(len(meta.get('tokens',[])))+' update(s) (GCM copies only)',roots,'',[journal],gui_tokens=['restore:'+journal.stem])
 # A path can appear only once in a selectable deletion plan.
 seen=set();result=[]
 for row in rows:
  if any(p in seen for p in row['roots']):continue
  seen.update(row['roots']);result.append(row)
 if len(result)>200:raise ValueError('Too many managed copies; refine the selected game')
 return result

def registration_changes(m,s,chosen):
 if s['emulator'] not in ('ryujinx','citron-emu'):return
 roots=[Path(p) for r in chosen for p in r['roots']];sources={p for r in chosen for p in r.get('sources',[])}
 def removed(path):return path in sources or any(Path(path)==root or root in Path(path).parents for root in roots)
 key='updates' if s['kind']=='update' else 'dlc';paths=set()
 if s['emulator']=='ryujinx':paths.add(m.ryu_snapshot(s['rom'])['configuration'][key]['path'])
 for root in (m.SYSTEM/'system/configs/Ryujinx/games',m.SYSTEM/'system/configs/ryujinx/games'):
  paths.update(str(p) for p in root.glob('*/'+('updates.json' if key=='updates' else 'dlc.json')))
 changed={}
 for value in sorted(paths):
  path=m.safe(value);before=m.transaction().raw(path)
  if before is None:continue
  doc=json.loads(before)
  if key=='updates':
   if doc.get('selected') and removed(doc['selected']):raise ValueError('Active updates cannot be deleted')
   after=json.loads(json.dumps(doc));after['paths']=[p for p in doc.get('paths',[]) if not removed(p)];lost=set(doc.get('paths',[]))-set(after['paths'])
  else:
   for r in doc:
    if removed(r['path']) and any(x.get('is_enabled',True) for x in r.get('dlc_nca_list',[])):raise ValueError('Active DLC cannot be deleted')
   after=[r for r in doc if not removed(r['path'])];lost={r['path'] for r in doc}-{r['path'] for r in after}
  if lost:changed[str(path)]=(after,lost);yield {'path':str(path),'before':before,'after':m.packed(after)}
 cache=m.COMMON.parent/'step60-shared-switch/state/ryujinx-verified-inventory.json'
 if changed and cache.exists():
  before=m.transaction().raw(cache);doc=json.loads(before);after=json.loads(json.dumps(doc))
  for game in after.get('games',{}).values():
   snapshot=game.get('native_snapshot',{});config=snapshot.get('configuration',{}).get(key,{})
   if config.get('path') not in changed:continue
   newdoc,lost=changed[config['path']];config.update(document=newdoc,sha256=hashlib.sha256(m.packed(newdoc)).hexdigest());snapshot['registered'][key]=[r for r in snapshot['registered'][key] if r['path'] not in lost]
   game['records']=[r for r in game.get('records',[]) if not (r.get('content_type')==s['kind'] and r.get('registered_path') in lost)]
   registered={r['path'] for k in ('updates','dlc') for r in snapshot['registered'][k]}
   game['file_identities']={p:v for p,v in game.get('file_identities',{}).items() if p not in lost or p in registered};game['problems']=[r for r in game.get('problems',[]) if not (r.get('content_type')==s['kind'] and r.get('path') in lost)];game['all_registered_content_verified']=not game['problems']
  yield {'path':str(cache),'before':before,'after':m.packed(after)}
 for name in ('citron-verified-inventory.json','citron-metadata-catalog.json'):
  path=m.COMMON.parent/'step60-shared-switch/state'/name
  if not path.exists():continue
  before=m.transaction().raw(path);doc=json.loads(before);after=json.loads(json.dumps(doc))
  capture=after.get('inventory',{})
  gone={p for p in capture.get('files',{}) if any(Path(p)==root or root in Path(p).parents for root in roots)}
  if not gone:continue
  capture['files']={p:v for p,v in capture['files'].items() if p not in gone};after['records']=[r for r in after.get('records',[]) if r.get('path') not in gone and not any(x.get('path') in gone for x in r.get('verified_records',[]))]
  yield {'path':str(path),'before':before,'after':m.packed(after)}

def cache_changes(m,s,chosen):
 yield from registration_changes(m,s,chosen)
 hashes=set();paths={p for row in chosen for p in row['roots']};sources={p for row in chosen for p in row.get('sources',[])}
 for row in chosen:
  for value in row['roots']:
   root=Path(value)
   if root.is_file() and root.suffix.lower() in ('.pkg','.nsp','.xci','.cia'):hashes.add(local_digest(m,root))
   if root.is_file() and root.suffix=='.json':
    recorded=m.read(root)
    if recorded.get('package_sha256'):hashes.add(recorded['package_sha256'])
   record=root/'record.json' if root.is_dir() else None
   if record and record.is_file():
    r=m.read(record)
    for item in r.get('files',[]):
     if isinstance(item,dict) and item.get('sha256'):hashes.add(item['sha256'])
    for group in r.get('groups',[]):
     if group.get('sha256'):hashes.add(group['sha256'])
 for path,schema in ((m.COMMON/'state/verified-content.json','GCM_PLATFORM_VERIFIED_CONTENT_V1'),(m.COMMON.parent/'step60-shared-switch/state/verified-content.json','GCM_SHARED_SWITCH_VERIFIED_CONTENT_V1')):
  if not path.exists():continue
  before=m.transaction().raw(path);doc=json.loads(before)
  if doc.get('schema')!=schema or not isinstance(doc.get('records'),dict):raise ValueError('Unexpected verification cache schema')
  after=json.loads(json.dumps(doc));removed=[]
  for key,r in doc['records'].items():
   if r.get('base')!=s['rom'] or r.get('content_type')!=s['kind'] or r.get('application_id')!=s['application_id']:continue
   if r.get('sha256') in hashes or r.get('source') in sources or any(Path(r.get('source','/'))==Path(p) or Path(p) in Path(r.get('source','/')).parents for p in paths):removed.append(key)
  for key in removed:del after['records'][key]
  if removed:yield {'path':str(path),'before':before,'after':m.packed(after)}

 if s['kind']=='dlc' and s['emulator'] in ('ryujinx','citron-emu'):
  identity=dlc_identity();path=m.safe(identity.STORE)
  if path.exists():
   before=m.transaction().raw(path);doc=identity.read();after=json.loads(json.dumps(doc))
   for key,r in doc['records'].items():
    if r.get('rom')==s['rom'] and r.get('emulator')==s['emulator'] and r.get('application_id')==s['application_id'] and (r.get('source') in sources or any(Path(r.get('source','/'))==Path(p) or Path(p) in Path(r.get('source','/')).parents for p in paths)):del after['records'][key]
   if after!=doc:yield {'path':str(path),'before':before,'after':m.packed(after)}

 path=registry(m,s);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);before=m.transaction().raw(path);doc={'scope':s,'entries':suppressed(m,s)}
 for row in chosen:
  for token in row.get('gui_tokens',[]):doc['entries'][token]={'sources':row.get('sources',[]),'owned_roots':row['roots']}
 if doc['entries']:yield {'path':str(path),'before':before,'after':m.packed(doc)}

def inventory(m,s):return available_inventory(m,s)

def location(m,identifier):
 if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid delete job')
 return m.safe(m.COMMON/'state/global-content-actions'/identifier/'state.json')
def save(m,path,d):path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);m.transaction().atomic(path,m.packed(d))
def own(m,path,d):m._own_job={'path':str(path),'nonce':d['nonce']}
def protected_job(m,path,s):
 d=m.read(path)
 if d.get('schema')!='GCM_MANAGED_DELETE_JOB_V1' or d.get('scope')!=s:raise ValueError('Delete job belongs to another game/type')
 return d

def purge(m,path,d,after_unlink=None):
 folder=path.parent;stage=m.safe(m.COMMON/'state/permanent-delete-staging'/d['job_id']);journal=folder/'move.json';j=validate_journal(m,journal,d)
 if j.get('schema')!='GCM_CONTENT_REMOVAL_TRANSACTION_V1' or j.get('metadata')!={'purpose':'PERMANENT_GCM_DELETE','scope':d['scope'],'job_id':d['job_id']} or j['status']!='COMPLETE':raise ValueError('Completed delete staging transaction required')
 targets=[move for move in j['moves'] if Path(move['quarantine']).is_relative_to(stage)]
 if 'plan_sha256' not in d:
  steps=[]
  for move in targets:
   root=m.safe(move['quarantine']);current=snap(m,root)
   if not m.engine().matches(current,move['snapshot'],renamed=True):raise ValueError('Managed deletion staging changed')
   for rel in sorted(current,key=lambda r:(len(Path(r).parts) if r!='.' else 0,r),reverse=True):
    p=root if rel=='.' else root/rel;steps.append({'path':str(p),'stat':current[rel],'directory':stat.S_ISDIR(current[rel][2])})
  plan=folder/'delete-plan.json';save(m,plan,{'job_id':d['job_id'],'steps':steps})
  d.update(plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest(),cursor=0,intent=None,status='RUNNING',phase='PERMANENTLY DELETING');save(m,path,d)
 plan=m.safe(folder/'delete-plan.json')
 if hashlib.sha256(plan.read_bytes()).hexdigest()!=d['plan_sha256']:raise ValueError('Permanent delete plan changed')
 document=m.read(plan)
 if document.get('job_id')!=d['job_id']:raise ValueError('Delete plan belongs to another job')
 steps=document['steps']
 # Refuse unexpected entries before each retry; missing entries are allowed only if logged.
 remaining={step['path'] for step in steps[d['cursor']:]}
 allowed_missing=steps[d['cursor']]['path'] if d['cursor']<len(steps) and d.get('intent')==d['cursor'] else None
 for move in targets:
  root=Path(move['quarantine'])
  if not root.exists():
   if str(root) in remaining and str(root)!=allowed_missing:raise ValueError('Deletion staging disappeared without a recorded intent')
   continue
  current=m.engine().snapshot(root)
  for rel in current:
   p=str(root if rel=='.' else root/rel)
   if p not in remaining:raise ValueError('Untracked files appeared in deletion staging')
 prior_inodes={tuple(step['stat'][:2]) for step in steps[:d['cursor']] if not step['directory']}
 for i in range(d['cursor'],len(steps)):
  step=steps[i];p=m.safe(step['path'])
  if not p.is_relative_to(stage) or p==stage:raise ValueError('Delete step is outside the protected staging directory')
  if not p.exists():
   if d.get('intent')!=i:raise ValueError('Managed file disappeared before its delete intent')
  else:
   current=p.lstat();actual=[current.st_dev,current.st_ino,current.st_mode,current.st_size,current.st_mtime_ns,current.st_ctime_ns];expected=step['stat']
   if actual[:3]!=expected[:3] or (not step['directory'] and actual!=expected and not (tuple(actual[:2]) in prior_inodes and actual[:5]==expected[:5])):raise ValueError('Managed file changed before permanent deletion')
   d['intent']=i;save(m,path,d)
   # Open each parent component without following symlinks, then unlink by directory FD.
   fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
   try:
    for part in p.parent.parts[1:]:
     nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nextfd
    st=os.stat(p.name,dir_fd=fd,follow_symlinks=False)
    at_unlink=[st.st_dev,st.st_ino,st.st_mode,st.st_size,st.st_mtime_ns,st.st_ctime_ns]
    if at_unlink[:3]!=expected[:3] or (not step['directory'] and at_unlink!=expected and not (tuple(at_unlink[:2]) in prior_inodes and at_unlink[:5]==expected[:5])):raise ValueError('Delete file identity changed at unlink')
    if step['directory']:os.rmdir(p.name,dir_fd=fd)
    else:os.unlink(p.name,dir_fd=fd)
    os.fsync(fd)
   finally:os.close(fd)
   if after_unlink:after_unlink(i)
  if not step['directory']:prior_inodes.add(tuple(step['stat'][:2]))
  d.update(cursor=i+1,intent=None);save(m,path,d)
 d.update(status='PASS',recovery_required=False,phase='PERMANENTLY DELETED');save(m,path,d)
 return d

def execute(m,s,tokens,confirmed=False,after_unlink=None):
 if confirmed is not True:raise ValueError('Explicit permanent-delete confirmation required')
 if not tokens or len(tokens)>32 or len(tokens)!=len(set(tokens)):raise ValueError('Select between 1 and 32 unique local copies')
 with m.lock(),m.compatibility_locks(s['emulator']),cache_locks(m):
  m.idle()
  if scope(m,s['rom'],s['emulator'],s['kind'])!=s:raise ValueError('Selected game changed')
  rows={r['token']:r for r in available_inventory(m,s)}
  if any(t not in rows for t in tokens):raise ValueError('Selected local copies changed; reopen Delete')
  chosen=[rows[t] for t in tokens]
  if any(r['blocked'] for r in chosen):raise ValueError(next(r['blocked'] for r in chosen if r['blocked']))
  roots=[p for r in chosen for p in r['roots']];evidence=[p for r in chosen for p in r['evidence']]
  if len(roots)!=len(set(roots)) or len(evidence)!=len(set(evidence)) or any(p!=q and Path(p).is_relative_to(Path(q)) for p in roots for q in roots):raise ValueError('Overlapping local copies cannot be deleted together')
  identifier=uuid.uuid4().hex;path=location(m,identifier);stage=m.safe(m.COMMON/'state/permanent-delete-staging'/identifier);stage.mkdir(mode=0o700,parents=True)
  moves=[]
  for i,value in enumerate(roots):moves.append({'source':value,'quarantine':str(stage/str(i)),'snapshot':snap(m,value)})
  for value in evidence:
   dest=m.safe(m.COMMON/'state/deleted-content-evidence'/identifier/Path(value).name);dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   moves.append({'source':value,'quarantine':str(dest),'snapshot':snap(m,value)})
  changes=list(cache_changes(m,s,chosen))
  d={'schema':'GCM_MANAGED_DELETE_JOB_V1','job_id':identifier,'scope':s,'status':'RUNNING','recovery_required':True,'phase':'STAGING PERMANENT DELETE','pid':os.getpid(),'nonce':uuid.uuid4().hex,'tokens':tokens,'move_fingerprint':fingerprint(moves),'cache_fingerprint':fingerprint([{'path':c['path'],'before':m.transaction().encode(c['before']),'after':m.transaction().encode(c['after'])} for c in changes])};save(m,path,d);own(m,path,d)
  try:
   def check():
    m.idle()
    if scope(m,s['rom'],s['emulator'],s['kind'])!=s:raise ValueError('Game or delete selection changed')
    current={r['token']:r for r in available_inventory(m,s)}
    if any(current.get(r['token'])!=r for r in chosen):raise ValueError('Selected GCM copy changed before staging')
   m.transaction().apply(path.parent/'move.json',moves,changes,{'purpose':'PERMANENT_GCM_DELETE','scope':s,'job_id':identifier},check_before=check)
   return purge(m,path,d,after_unlink)
  except BaseException as exc:
   journal=path.parent/'move.json';rolled_back=not journal.exists() or m.read(journal)['status']=='ROLLED_BACK';d.update(status='ERROR',recovery_required=not rolled_back,error=str(exc));save(m,path,d);raise
  finally:m._own_job=None

def resume(m,s,token,confirmed=False,after_unlink=None):
 if confirmed is not True:raise ValueError('Explicit permanent-delete resume confirmation required')
 if not token.startswith('delete-job:'):raise ValueError('Select an interrupted delete job')
 with m.lock(),m.compatibility_locks(s['emulator']),cache_locks(m):
  path=location(m,token.split(':',1)[1]);d=protected_job(m,path,s)
  # Admission lock excludes active deletion; no worker is killed or guessed from a PID.
  if not d.get('recovery_required'):raise ValueError('This deletion does not require recovery')
  d.update(pid=os.getpid(),status='RUNNING',nonce=uuid.uuid4().hex);save(m,path,d);own(m,path,d)
  try:
   m.idle()
   if scope(m,s['rom'],s['emulator'],s['kind'])!=s:raise ValueError('Selected game changed before deletion resume')
   journal=path.parent/'move.json'
   if not journal.exists() or m.read(journal)['status']!='COMPLETE':
    if journal.exists() and validate_journal(m,journal,d)['status']!='ROLLED_BACK':m.transaction().recover(journal)
    d.update(status='ERROR',recovery_required=False,phase='STAGING ROLLED BACK');save(m,path,d);return d
   return purge(m,path,d,after_unlink)
  except BaseException as exc:d.update(status='ERROR',recovery_required=True,error=str(exc));save(m,path,d);raise
  finally:m._own_job=None

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('list','preview','delete','overlay','verified'));p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True);p.add_argument('--type',choices=('update','dlc'),required=True);p.add_argument('--tokens',default='');p.add_argument('--confirm',action='store_true');p.add_argument('--inventory-base64',default='');a=p.parse_args();m=workflow().provider();s=scope(m,a.rom,a.emulator,a.type)
 if a.action=='overlay':
  import base64
  print(overlay(m,s,base64.b64decode(a.inventory_base64,validate=True).decode()),end='');return
 if a.action=='verified':print('OK\t'+verified(m,s,a.tokens.split(',') if a.tokens else []));return
 if a.action=='list':
  for path in (m.COMMON/'state/global-content-actions').glob('*/state.json'):
   d=m.read(path)
   if d.get('schema')=='GCM_MANAGED_DELETE_JOB_V1' and d.get('scope')==s and d.get('recovery_required'):print('ROW\tAVAILABLE\tREADY\tFinish interrupted permanent deletion\tdelete-job:'+d['job_id'])
  for r in available_inventory(m,s):print('\t'.join(('ROW','AVAILABLE','REVIEW_REQUIRED' if r['blocked'] else 'READY',m.clean(r['label']),r['token'])))
 elif a.action=='preview':
  matches=[r for r in available_inventory(m,s) if r['token']==a.tokens]
  if len(matches)!=1:raise ValueError('Selected copy changed; reopen Delete')
  print(matches[0]['blocked'] or 'Permanently delete the selected GCM-owned local copy. External originals are preserved.')
 else:
  tokens=a.tokens.split(',') if a.tokens else []
  if any(t.startswith('delete-job:') for t in tokens):
   if len(tokens)!=1:raise ValueError('Finish one interrupted deletion at a time')
   result=resume(m,s,tokens[0],a.confirm)
   if result.get('phase')=='STAGING ROLLED BACK':print('OK\tDELETE STAGING RECOVERED; LOCAL COPIES RESTORED');return
  else:execute(m,s,tokens,a.confirm)
  print('OK\tDELETED='+str(len(tokens))+'\tGUI entries and GCM-owned copies permanently deleted; external originals preserved')
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
