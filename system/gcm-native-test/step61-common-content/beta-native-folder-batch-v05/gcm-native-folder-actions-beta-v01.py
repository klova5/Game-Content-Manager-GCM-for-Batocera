"""Source-bound native folder lifecycle for Cemu, Vita and PS3 disc updates.

Prepared source trees are copied/read-back verified. This does not authenticate
packages or decrypt containers. Only roots installed by this provider may be
renamed for enable/disable or uninstall. Originals and quarantine are retained.
"""
import hashlib,importlib.util,json,os,uuid,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PINS={'gcm-beta-filesystem-space-v01.py': 'd5bc6f049146f9262f2c1f9e4648a86ea0341f6ded160cca31e3d9dd5b368a21', 'gcm-beta-installed-tree-copy-v01.py': 'dd5026ddbf58cd2cf2f8aa91db6ccf1d0a7d94daef271e049f7240db14296d40', 'gcm-wiiu-title-metadata-v02.py': 'c25b772bd748f31b4cfd31dcd5f907b0a97ce0f403cb2e051bd02f7c26b94a2f', 'gcm-xml-archive-metadata.py': '9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2', 'gcm-beta-rename-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec', 'gcm-shared-installed-worker-beta-v01.py': '1d51640ca1e38d2382a3748f654b376778fc219d1d987e2c31619f6fac3a7b2b', 'gcm-sfo-metadata.py': '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41', 'gcm-native-folder-inventory-beta-v01.py': 'bdae591705d77a753a973fa2ae7abe9b34f6c410da4139a54af37f0498a3d82d'}

def load(name):
 p=ROOT/name
 if any(v.is_symlink() for v in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Native action dependency changed: '+name)
 s=importlib.util.spec_from_file_location('actions_'+p.stem,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def read_json(path):
 p=load('gcm-shared-installed-worker-beta-v01.py').safe(path)
 if p.stat().st_size>32*1024*1024:raise ValueError('Oversized native action record')
 return json.loads(p.read_text())

class Provider:
 def __init__(self,emulator,common=Path('/userdata/system/gcm-native-test/step61-common-content'),system_root=Path('/userdata')):
  if emulator not in ('cemu','vita3k','rpcs3'):raise ValueError('Native folder action adapter unavailable')
  self.emulator=emulator;self.common=Path(common);self.system_root=Path(system_root)
  self.inventory=load('gcm-native-folder-inventory-beta-v01.py');self.worker=load('gcm-shared-installed-worker-beta-v01.py')
  self.copy=load('gcm-beta-installed-tree-copy-v01.py');self.tx=load('gcm-beta-rename-transaction-v01.py')
  self.state=self.worker.safe(self.common/'state/beta-native-folder-actions')

 def locked(self,fn,*args,**kwargs):
  lock=self.worker.safe(self.common/'state/global-content-actions/.start.lock');lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   # Historical backups do not represent live jobs.
   for folder in ('global-content-actions','global-uninstall-jobs','global-install-locally-jobs'):
    for p in (self.common/'state'/folder).glob('*/state.json'):
     d=read_json(p)
     if d.get('status')=='RUNNING' or d.get('recovery_required') is True:raise ValueError('Content action running or requires recovery assessment')
   recovering = fn.__name__ == '_recover'
   recovering_id = args[1] if recovering else None
   for receipt in (self.state/'items').glob('*/record.json'):
    prior=read_json(receipt)
    if prior.get('pending') and not (recovering and prior.get('item_id')==recovering_id):
     raise ValueError('Pending native transaction requires recovery: '+str(prior.get('item_id')))
   self.require_stopped()
   return fn(*args,**kwargs)
  finally:os.close(fd)

 def require_stopped(self):
  # Refuse content mutations while a native emulator can still use the storage.
  names={'cemu','rpcs3','vita3k'}
  for entry in Path('/proc').iterdir():
   if not entry.name.isdigit():continue
   try:
    words=(entry/'cmdline').read_bytes().split(b'\0')[:1]
    words.append((entry/'comm').read_bytes().strip())
   except FileNotFoundError:continue
   except PermissionError:raise ValueError('Cannot assess running emulator processes')
   if any(Path(w.decode('utf-8','replace')).name.lower() in names for w in words if w):
    raise ValueError('Stop Cemu, RPCS3 and Vita3K before native folder actions')

 def source(self,rom,source,kind):
  root=self.worker.safe(Path(source).absolute());base=self.inventory.identity(rom,self.emulator,self.system_root)
  if kind not in ('update','dlc'):raise ValueError('Update or DLC required')
  native=self.inventory.storage(self.emulator,self.system_root);title=base['application_id']
  if self.emulator=='cemu':
   meta=self.inventory.load('gcm-wiiu-title-metadata-v02.py').read(root/'meta/meta.xml')
   if meta['application_id']!=title or meta['content_type']!=kind:raise ValueError('Cemu content does not match selected game/type')
   target=native/'usr/title'/meta['title_id'][:8].lower()/meta['title_id'][8:].lower()
   content_id=meta['title_id'];version=str(meta['internal_version'])
  else:
   path=root/('sce_sys/param.sfo' if self.emulator=='vita3k' else 'PARAM.SFO')
   meta=self.inventory.load('gcm-sfo-metadata.py').read(self.worker.safe(path));f=meta['fields']
   if f.get('TITLE_ID')!=title:raise ValueError('Content belongs to another game')
   version=f.get('APP_VER','');content_id=f.get('CONTENT_ID',title)
   import re
   if self.emulator=='rpcs3':
    if kind!='update' or f.get('CATEGORY')!='GD':raise ValueError('PS3 folder adapter supports disc updates only')
    old=base['metadata']['fields'].get('APP_VER','')
    if not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',version) or not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',old) or version<=old:raise ValueError('Newer internal PS3 update version required')
    target=native/'game'/title
   else:
    if f.get('CATEGORY')!=('gp' if kind=='update' else 'ac'):raise ValueError('Vita internal content category disagrees')
    if content_id!=title and not re.fullmatch(r'[A-Z]{2}[0-9]{4}-'+title+r'_[0-9]{2}-[A-Z0-9]{16}',content_id):raise ValueError('Vita content ID disagrees')
    if kind=='dlc' and content_id==title:raise ValueError('Vita DLC entitlement required')
    target=native/'ux0'/('patch' if kind=='update' else 'addcont')/title
    if kind=='dlc':target=target/content_id[-16:]
  target=self.worker.safe(target)
  for protected in (root,self.state,Path(rom).absolute()):
   if target==protected or target in protected.parents or protected in target.parents:
    raise ValueError('Native destination overlaps source, selected game or managed evidence')
  b={'schema':'GCM_EXISTING_CONTENT_BINDING_V1','emulator':self.emulator,'application_id':title,'content_type':kind,
     'game_relationship_verified':True,'source_root':str(root),'content_id':content_id,'version':version,
     'identity_evidence':{'base':base['metadata'],'content':meta},'native_root':str(native),'target':str(self.worker.safe(target))}
  return b

 def prepare(self,rom,source,kind,confirmed=False,progress=None):
  if confirmed is not True:raise ValueError('Explicit preparation confirmation required')
  return self.locked(self._prepare,rom,source,kind,progress)

 def _prepare(self,rom,source,kind,progress):
  b=self.source(rom,source,kind);snap=self.worker.snapshot(b['source_root']);total=sum(v[2] for v in snap['files'].values())
  if not snap['files']:raise ValueError('Nonempty native content required')
  item_id=hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest();item=self.state/'items'/item_id
  path=item/'record.json'
  if path.exists():
   old=read_json(path)
   if old.get('source_snapshot')!=snap:raise ValueError('Original content changed; earlier item retained')
   self.valid(old,rom)
   if old['pending']:raise ValueError('Native transaction recovery required')
   if old['state']=='UNINSTALLED':
    old['state']='PREPARED';self.worker.atom(path,old)
   return old
  self.worker.check_space(self.state,total)
  imported=self.copy.import_tree(Path(b['source_root']),self.state/'prepared'/item_id,b,progress)
  if self.source(rom,source,kind)!=b or self.worker.snapshot(b['source_root'])!=snap:raise ValueError('Binding changed; imported evidence retained without activation')
  prepared=Path(imported['managed_root']);record={'schema':'GCM_NATIVE_FOLDER_ITEM_BETA_V1','item_id':item_id,'binding':b,
   'rom':str(Path(rom).absolute()),'prepared_root':str(prepared),'prepared_snapshot':self.worker.snapshot(prepared),
   'manifest':imported['manifest'],'source_snapshot':snap,'state':'PREPARED','package_integrity':'UNASSESSED','pending':None}
  self.worker.atom(path,record);return record

 def path(self,item_id):
  if len(item_id)!=64 or any(c not in '0123456789abcdef' for c in item_id):raise ValueError('Invalid native item ID')
  return self.state/'items'/item_id/'record.json'

 def valid(self,record,rom):
  if record.get('schema')!='GCM_NATIVE_FOLDER_ITEM_BETA_V1' or record['rom']!=str(Path(rom).absolute()):raise ValueError('Native item game mismatch')
  b=record['binding'];current=self.source(rom,b['source_root'],b['content_type'])
  if b!=current:raise ValueError('Native game/content/storage binding changed')
  if self.worker.snapshot(b['source_root'])!=record['source_snapshot']:raise ValueError('Original source content changed')
  if self.worker.snapshot(record['prepared_root'])!=record['prepared_snapshot']:raise ValueError('Prepared files changed')
  return b

 def install(self,rom,item_id,confirmed=False,progress=None):
  if confirmed is not True:raise ValueError('Explicit installation confirmation required')
  return self.locked(self._install,rom,item_id,progress)

 def _install(self,rom,item_id,progress):
  record=read_json(self.path(item_id));b=self.valid(record,rom)
  if record['pending']:raise ValueError('Native transaction recovery required')
  target=self.worker.safe(b['target'])
  # An emulator may remove an adopted update without editing GCM's receipt.
  # Reinstallation is explicit and copies only into an absent destination.
  if record['state']=='ENABLED' and record.get('ownership_origin')=='ADOPTED_EXISTING_UPDATE' and not target.exists():
   record['state']='UNINSTALLED';self.worker.atom(self.path(item_id),record)
  if record['state']=='ENABLED':
   if self.worker.snapshot(target)!=record['native_snapshot']:raise ValueError('Installed files changed')
   return record
  if record['state']=='DISABLED':return self._move(rom,item_id,'enable')
  if target.exists():raise ValueError('Existing native content preserved; no overwrite authorized')
  target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  total=sum(v[2] for v in record['prepared_snapshot']['files'].values());self.worker.check_space(target.parent,total)
  staging=target.parent/('.gcm-stage-'+uuid.uuid4().hex)
  copied=self.copy.import_tree(Path(record['prepared_root']),staging,b,progress)
  if copied['manifest']!=record['manifest']:raise ValueError('Installed staging bytes disagree with preparation')
  self.valid(record,rom)
  return self._commit(record,Path(copied['managed_root']),target,'ENABLED')

 def change(self,rom,item_id,action,confirmed=False,after_move=None):
  if confirmed is not True:raise ValueError('Explicit selection/uninstall confirmation required')
  return self.locked(self._move,rom,item_id,action,after_move)

 def _move(self,rom,item_id,action,after_move=None):
  if action not in ('enable','disable','uninstall'):raise ValueError('Invalid native action')
  record=read_json(self.path(item_id));b=self.valid(record,rom)
  if record['pending']:raise ValueError('Native transaction recovery required')
  target=self.worker.safe(b['target']);native=self.worker.safe(b['native_root'])
  disabled=native/'.gcm-disabled-content'/item_id
  if action=='enable' and record['state']=='ENABLED':
   if self.worker.snapshot(target)!=record['native_snapshot']:raise ValueError('Installed content changed')
   return record
  if action=='disable' and record['state']=='DISABLED':
   if self.worker.snapshot(disabled)!=record['native_snapshot']:raise ValueError('Disabled content changed')
   return record
  if action=='uninstall' and record['state']=='UNINSTALLED':return record
  if record['state'] not in ('ENABLED','DISABLED'):raise ValueError('Installed GCM-owned item required')
  source=target if record['state']=='ENABLED' else disabled
  if self.worker.snapshot(source)!=record['native_snapshot']:raise ValueError('Installed content changed; assess before mutation')
  if action=='enable':dest=target;state='ENABLED'
  elif action=='disable':dest=disabled;state='DISABLED'
  else:dest=native/'.gcm-content-quarantine'/uuid.uuid4().hex/item_id;state='UNINSTALLED'
  dest.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  return self._commit(record,source,dest,state,after_move)

 def _commit(self,record,source,dest,state,after_move=None):
  source=self.worker.safe(source);dest=self.worker.safe(dest)
  if dest.exists():raise ValueError('Native destination already exists')
  before=self.tx.snapshot(source);journal=self.state/'transactions'/(uuid.uuid4().hex+'.json');journal.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  record['pending']={'journal':str(journal),'source':str(source),'destination':str(dest),'next_state':state,'previous_state':record['state'],'source_snapshot':before}
  self.worker.atom(self.path(record['item_id']),record)
  self.tx.apply(journal,[{'source':str(source),'quarantine':str(dest),'snapshot':before}],{'item_id':record['item_id'],'next_state':state},after_move)
  record['state']=state;record['native_snapshot']=self.worker.snapshot(dest);record['pending']=None
  if state=='UNINSTALLED':record.setdefault('quarantines',[]).append(str(dest))
  self.worker.atom(self.path(record['item_id']),record);return record

 def recover(self,rom,item_id):
  return self.locked(self._recover,rom,item_id)

 def _recover(self,rom,item_id):
  record=read_json(self.path(item_id));self.valid(record,rom);pending=record['pending']
  if not pending:return record
  journal=Path(pending['journal'])
  if journal.parent!=self.state/'transactions':raise ValueError('Foreign transaction rejected')
  if not journal.exists():
   if Path(pending['destination']).exists() or not self.tx.matches(self.tx.snapshot(pending['source']),pending['source_snapshot']):
    raise ValueError('Missing journal with ambiguous native paths')
   record['pending']=None;self.worker.atom(self.path(item_id),record);return record
  doc=read_json(journal)
  moves=doc.get('moves',[])
  if (doc.get('metadata')!={'item_id':item_id,'next_state':pending['next_state']}
      or len(moves)!=1 or moves[0].get('source')!=pending['source']
      or moves[0].get('quarantine')!=pending['destination']
      or moves[0].get('snapshot')!=pending['source_snapshot']):
   raise ValueError('Transaction owner or saved move mismatch')
  self.tx.rollback(journal)
  if record.get('native_snapshot') is not None and self.worker.snapshot(pending['source'])!=record['native_snapshot']:
   raise ValueError('Recovery source differs from prior installed state')
  record['state']=pending['previous_state'];record['pending']=None
  self.worker.atom(self.path(item_id),record);return record
