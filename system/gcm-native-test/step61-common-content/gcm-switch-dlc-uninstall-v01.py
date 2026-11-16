"""Switch DLC removal plans and journaled execution; package originals retained."""
from contextlib import ExitStack
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import tempfile
import types
import uuid
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
SHARED=ROOT.parent/'step60-shared-switch'
MANAGED=Path('/userdata/system/game-content')
PINS={'gcm-uninstall-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec', 'gcm-content-removal-transaction-v01.py': '16e5c3a5274c6a4e7137fc31ba0b301d48309e64901675d98c975f677d32adb2', 'gcm-switch-content-action-plan-v01.py': '25e8f04e0017f723b5dce647f2ba91519608baad57d993529d567c46e78ab3b0'}

def load(name):
 p=ROOT/name
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Switch uninstall dependency changed: '+name)
 spec=importlib.util.spec_from_file_location('switch_remove_'+p.stem,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def packed(doc):return (json.dumps(doc,indent=2)+'\n').encode()
def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def narrow_roots(roots,excluded,files):
 """Keep originals in place; remove selected files from external directory registration."""
 e=load('gcm-uninstall-transaction-v01.py');excluded={e.safe(p) for p in excluded};files={e.safe(p) for p in files}
 result=[]
 def split(root):
  e.safe(root)
  below={p for p in files if p.is_relative_to(root)}
  if not any(p.is_relative_to(root) for p in excluded):return [root]
  keep=below-excluded
  if not keep:return []
  if any(p.parent==root for p in keep):raise ValueError('External directory mixes retained files with selected DLC; cannot remove registration without moving originals')
  dirs=sorted({root/p.relative_to(root).parts[0] for p in keep})
  return [p for d in dirs for p in split(d)]
 for item in roots:
  if item['origin']!='External':result.append(copy.deepcopy(item));continue
  result.extend({**item,'path':str(p)} for p in split(e.safe(item['path'])))
 keys=[(i['origin'],i['path']) for i in result]
 if len(keys)!=len(set(keys)):raise ValueError('Ambiguous external root expansion')
 return result

def render_citron(config,new_roots):
 from PyQt5.QtCore import QSettings
 source=load('gcm-content-removal-transaction-v01.py').raw(config)
 def settings(path):
  q=QSettings(str(path),QSettings.IniFormat);q.setFallbacksEnabled(False);q.setIniCodec('UTF-8');return q
 old=settings(config);prefix='UI/Paths/external_content_dirs/'
 untouched={k:old.value(k) for k in old.allKeys() if not k.startswith(prefix)}
 with tempfile.TemporaryDirectory(prefix='gcm-external-roots-') as td:
  path=Path(td)/'settings.ini';path.write_bytes(source);q=settings(path)
  previous=[]
  for i in range(1,int(q.value(prefix+'size',0))+1):
   stem=prefix+str(i)+'/'
   previous.append((str(q.value(stem+'path','')),{k[len(stem):]:q.value(k) for k in q.allKeys() if k.startswith(stem)}))
  q.beginGroup('UI/Paths/external_content_dirs');q.remove('');q.endGroup()
  external=[r for r in new_roots if r['origin']=='External']
  q.setValue(prefix+'size',len(external))
  for i,r in enumerate(external,1):
   owners=[values for root,values in previous if Path(r['path']).is_relative_to(Path(root))]
   if len(owners)!=1:raise ValueError('Ambiguous external registration parent')
   for key,value in owners[0].items():q.setValue(prefix+str(i)+'/'+key,r['path'] if key=='path' else value)
  q.sync()
  if q.status()!=QSettings.NoError:raise ValueError('Cannot render Citron storage configuration')
  fresh=settings(path)
  if {k:fresh.value(k) for k in fresh.allKeys() if not k.startswith(prefix)}!=untouched:raise ValueError('Unrelated Citron settings changed')
  return path.read_bytes()

def context(rom,emulator):
 planner=load('gcm-switch-content-action-plan-v01.py');e=load('gcm-uninstall-transaction-v01.py');base=e.safe(rom)
 app=planner.load('gcm-switch-base-identity.py').identify(str(base))['application_id'].upper()
 if emulator=='ryujinx':
  integrity=planner.load('ryujinx-installed-integrity-v02.py');adapter,_=integrity.dependencies()
  old=json.loads(e.safe(integrity.STORE).read_text());current=adapter.snapshot(str(base));key=str(base.resolve(strict=True))
  if old.get('schema')!='GCM_RYUJINX_SHARED_VERIFIED_INVENTORY_V1' or old.get('boot_id')!=boot() or key not in old.get('games',{}):raise ValueError('Current boot game-bound installation evidence required')
  game=old['games'][key]
  if app!=current['application_id'] or integrity.registration_identity(current)!=integrity.registration_identity(game['native_snapshot']):raise ValueError('Game registrations changed; assess before uninstall')
  records=game['records'];all_records=records
  return {'app':app,'records':records,'all_records':all_records,'inventory':old,'store':str(integrity.STORE),'current':current,'rom_key':key}
 if emulator!='citron-emu':raise ValueError('Switch uninstall adapter unavailable')
 integrity=planner.load('citron-installed-integrity.py');catalog=integrity.catalog_module()
 old=json.loads(e.safe(integrity.STORE).read_text());metadata=json.loads(e.safe(catalog.STORE).read_text());now=catalog.capture()
 if old.get('schema')!='GCM_CITRON_GLOBAL_VERIFIED_INVENTORY_V1' or old.get('content_integrity_complete') is not True or metadata.get('schema')!='GCM_CITRON_GLOBAL_METADATA_CATALOG_V1' or old.get('boot_id')!=boot() or metadata.get('boot_id')!=boot() or old['inventory']!=metadata['inventory']:
  raise ValueError('Current matching Citron inventory and catalog required')
 if now['roots']!=old['inventory']['roots'] or set(now['files'])!=set(old['inventory']['files']):raise ValueError('Citron registration file set changed; assess first')
 return {'app':app,'records':[r for r in old['records'] if r['application_id'].upper()==app],
  'all_records':old['records'],'inventory':old,'metadata':metadata,'current':now,'store':str(integrity.STORE),
  'catalog_store':str(catalog.STORE),'config':str(catalog.CONFIG),'config_bytes':catalog.CONFIG.read_bytes()}

def plan_from_context(ctx,emulator,tokens):
 e=load('gcm-uninstall-transaction-v01.py');app=ctx['app'];rows={}
 for r in ctx['records']:
  if r['content_type']!='dlc':continue
  if r.get('application_id','').upper()!=app or not re.fullmatch('[0-9A-Fa-f]{16}',r.get('title_id','')):raise ValueError('Stored DLC game binding disagrees')
  token=r['registered_path'].encode().hex() if emulator=='ryujinx' else 'citron:dlc:'+app+':'+r['title_id']
  row=rows.setdefault(token,{'token':token,'label':r['title_id'],'records':[],'preserved_package':[]})
  row['records'].append(r)
  if emulator=='ryujinx' or r['origin']=='External':row['preserved_package'].append(r.get('registered_path',r.get('path')))
 if tokens is None:tokens=list(rows)
 if not tokens or len(tokens)!=len(set(tokens)) or any(t not in rows for t in tokens):raise ValueError('Explicit unique registered DLC selection required')
 selected=[rows[t] for t in tokens];chosen=[r for row in selected for r in row['records']]
 changes=[];moves=[];retained=[]
 tx=load('gcm-content-removal-transaction-v01.py')
 if emulator=='ryujinx':
  current=ctx['current'];config=current['configuration']['dlc'];before=tx.raw(config['path'])
  if hashlib.sha256(before).hexdigest()!=config['sha256']:raise ValueError('Ryujinx configuration changed')
  wanted={r['registered_path'] for r in chosen};after=[r for r in config['document'] if r['path'] not in wanted]
  if len(config['document'])-len(after)!=len(wanted):raise ValueError('DLC selection and registration differ')
  updated=copy.deepcopy(ctx['inventory']);game=updated['games'][ctx['rom_key']]
  # Remaining evidence must still be current; damaged selected packages may be removed without hashing.
  for path,expected in game['file_identities'].items():
   if path not in wanted and load('gcm-switch-content-action-plan-v01.py').identity(path)!=expected[1:]:raise ValueError('Unselected package changed')
  snapshot=copy.deepcopy(current);snapshot['configuration']['dlc'].update(document=after,sha256=hashlib.sha256(packed(after)).hexdigest())
  snapshot['registered']['dlc']=[r for r in snapshot['registered']['dlc'] if r['path'] not in wanted]
  game['native_snapshot']=snapshot;game['records']=[r for r in game['records'] if r.get('registered_path') not in wanted]
  game['file_identities']={p:v for p,v in game['file_identities'].items() if p not in wanted}
  changes=[{'path':config['path'],'before':before,'after':packed(after)}, {'path':ctx['store'],'before':tx.raw(ctx['store']),'after':packed(updated)}]
 else:
  excluded=set();allfiles=ctx['current']['files'];others=[r for r in ctx['all_records'] if r not in chosen]
  references=set();reference_inodes=set()
  for r in others:
   for p in [r['path']]+[v['path'] for v in r.get('verified_records',[])]:
    references.add(p);reference_inodes.add(tuple(allfiles[p][:2]))
  for r in chosen:
   for p in [r['path']]+[v['path'] for v in r.get('verified_records',[])]:
    e.safe(p)
    if p not in allfiles or not any(Path(p).is_relative_to(Path(root['path'])) and root['origin']==r['origin'] for root in ctx['current']['roots']):raise ValueError('DLC file is outside its registered storage')
   if r['origin']=='External':
    if not e.safe(r['path']).is_relative_to(MANAGED):raise ValueError('Unmanaged external registration needs a dedicated removal adapter')
    excluded.add(r['path']);continue
   for p in [r['path']]+[v['path'] for v in r.get('verified_records',[])]:
    if p in references or tuple(allfiles[p][:2]) in reference_inodes:
     if p==r['path']:raise ValueError('Selected metadata is still referenced by another registration')
     retained.append(p)
    else:moves.append(p)
  moves=sorted(set(moves));roots=narrow_roots(ctx['current']['roots'],excluded,allfiles)
  removed=set(moves)|excluded
  for p,value in allfiles.items():
   if p not in removed and value!=ctx['inventory']['inventory']['files'][p]:raise ValueError('Unselected installed file changed')
  afterconfig=ctx['config_bytes'] if roots==ctx['current']['roots'] else render_citron(Path(ctx['config']),roots)
  newfiles={p:v for p,v in allfiles.items() if p not in moves and any(Path(p).is_relative_to(Path(r['path'])) for r in roots)}
  newcapture={'roots':roots,'files':newfiles}
  def projected(doc):
   result=copy.deepcopy(doc);result['inventory']=newcapture
   result['records']=[r for r in result['records'] if not (r['application_id'].upper()==app and r['content_type']=='dlc' and any(r['title_id']==x['title_id'] for x in chosen))]
   for r in result['records']:
    if r['origin']=='External':
     owners=[x['path'] for x in roots if x['origin']=='External' and Path(r['path']).is_relative_to(Path(x['path']))]
     if not owners:raise ValueError('Unselected registration lost during root narrowing')
     r['registered_root']=max(owners,key=len)
   return result
  changes=[{'path':ctx['config'],'before':ctx['config_bytes'],'after':afterconfig},
   {'path':ctx['catalog_store'],'before':tx.raw(ctx['catalog_store']),'after':packed(projected(ctx['metadata']))},
   {'path':ctx['store'],'before':tx.raw(ctx['store']),'after':packed(projected(ctx['inventory']))}]
 return {'application_id':app,'emulator':emulator,'rows':selected,'moves':moves,'changes':changes,
  'retained_shared_files':sorted(set(retained))}

def plan(rom,emulator,tokens=None):return plan_from_context(context(rom,emulator),emulator,tokens)

def idle():
 for p in Path('/proc').glob('[0-9]*/comm'):
  try:name=p.read_text().strip().casefold()
  except (FileNotFoundError,ProcessLookupError):continue
  if name.startswith(('ryujinx','citron','yuzu')):raise ValueError('Close Switch emulators before uninstalling content')
 for folder in ('global-content-actions','global-install-locally-jobs','global-uninstall-jobs'):
  for p in (ROOT/'state'/folder).rglob('*.json'):
   if p.name.startswith('.'):continue
   job=json.loads(p.read_text())
   if job.get('status')=='RUNNING' and not (folder=='global-uninstall-jobs' and job.get('pid')==os.getpid() and job.get('boot_id')==boot()):
    raise ValueError('Another content operation is running')

def execute(rom,emulator,tokens,confirmed=False):
 if confirmed is not True:raise ValueError('Uninstall confirmation required')
 e=load('gcm-uninstall-transaction-v01.py');tx=load('gcm-content-removal-transaction-v01.py')
 with ExitStack() as stack:
  for path in (ROOT/'state/global-content-actions/.start.lock',SHARED/'state/ryujinx-configuration.lock',ROOT.parent/'step59-citron-backend-v01/configuration-commit.lock'):
   path=e.safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
   stack.callback(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX)
  idle();current=plan(rom,emulator,tokens);folder=e.safe(ROOT/'state/switch-dlc-uninstall-transactions');folder.mkdir(mode=0o700,parents=True,exist_ok=True)
  for path in folder.glob('*.json'):
   if json.loads(path.read_text()).get('status') not in ('COMPLETE','ROLLED_BACK'):raise ValueError('Unfinished Switch uninstall requires recovery assessment')
  txid=uuid.uuid4().hex;movelist=[]
  for i,source in enumerate(current['moves']):
   source=e.safe(source)
   # Park outside every registered storage root, on the original filesystem.
   ctx=context(rom,emulator)
   owners=[Path(r['path']) for r in ctx['current']['roots'] if source.is_relative_to(Path(r['path']))]
   owner=min(owners,key=lambda p:len(p.parts));parking=e.safe(owner.parent/'.gcm-uninstalled'/txid)
   if any(parking.is_relative_to(Path(r['path'])) for r in ctx['current']['roots']):raise ValueError('Quarantine overlaps registered storage')
   parking.mkdir(mode=0o700,parents=True,exist_ok=True)
   movelist.append({'source':str(source),'quarantine':str(parking/(str(i)+'-'+source.name)),'snapshot':e.snapshot(source)})
  def before():
   idle()
   if plan(rom,emulator,tokens)!=current:raise ValueError('Uninstall plan changed before transaction')
  def after():
   idle();planner=load('gcm-switch-content-action-plan-v01.py')
   if emulator=='ryujinx':
    fresh=planner.load('ryujinx-installed-integrity-v02.py').validate(rom)
    if any(r.get('registered_path','').encode().hex() in tokens for r in fresh['records'] if r['content_type']=='dlc'):raise ValueError('Selected Ryujinx DLC remains registered')
   else:
    fresh=planner.load('citron-installed-integrity.py').validate()
    if any('citron:dlc:'+current['application_id']+':'+r['title_id'] in tokens for r in fresh['records'] if r['content_type']=='dlc'):raise ValueError('Selected Citron DLC remains registered')
  journal=folder/(txid+'.json')
  tx.apply(journal,movelist,current['changes'],{'application_id':current['application_id'],'emulator':emulator,'tokens':tokens},before,after)
  print('OK\tUNINSTALLED='+str(len(tokens)));print('JOURNAL:',journal)
  return {'journal':str(journal),'items':len(tokens)}

def provider(emulator):
 emulator={'ryujinx-emu':'ryujinx','citron':'citron-emu'}.get(emulator,emulator)
 if emulator not in ('ryujinx','citron-emu'):raise ValueError('Switch uninstall adapter unavailable')
 return types.SimpleNamespace(load=load,idle=idle,plan=lambda rom,tokens:plan(rom,emulator,tokens),execute=lambda rom,tokens,confirmed=False:execute(rom,emulator,tokens,confirmed))
