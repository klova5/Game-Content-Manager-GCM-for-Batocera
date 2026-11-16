"""Shared update scan/import jobs. Process-start ownership and atomic index publication."""
import argparse,contextlib,fcntl,hashlib,importlib.util,json,os,re,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SCHEMA='GCM_SHARED_UPDATE_JOB_V1'
def workflow():
 manifest=ROOT/'manifest.json'
 if any(p.is_symlink() for p in (manifest,*manifest.parents)):raise ValueError('Unsafe provider manifest')
 pins=json.loads(manifest.read_text())['sha256'];path=ROOT/'gcm-global-update-workflow-v01.py'
 if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=pins[path.name]:raise ValueError('Shared update workflow changed')
 spec=importlib.util.spec_from_file_location('global_workflow',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def scope(m,rom,e,kind='update'):
 if kind!='update':raise ValueError('This job manages updates only')
 e=m.canonical(e);p=workflow().profile_module()
 return {'rom':str(m.safe(Path(rom).absolute())),'emulator':e,'kind':kind,'identity':p.identify(m,rom,e)}
def index_path(m,s):return m.COMMON/'state/global-update-index'/(hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest()+'.json')
def read_index(m,s):
 path=index_path(m,s)
 if not path.exists():return {'scope':s,'candidates':[]}
 doc=m.read(path)
 if doc.get('scope')!=s:raise ValueError('Update scan belongs to a stale game binding')
 return doc

def jobs(m):return m.COMMON/'state/beta-native-folder-jobs'
def location(m,identifier):
 if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid update job ID')
 return m.safe(jobs(m)/identifier/'state.json')
def owner(pid):
 try:
  fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split();return None if fields[0]=='Z' else fields[19]
 except (OSError,ValueError):return None
def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def atom(m,path,doc):
 path=m.safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);m.transaction().atomic(path,m.packed(doc))
@contextlib.contextmanager
def state_lock(path):
 fd=os.open(path.parent/'.state.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX);yield
 finally:os.close(fd)
def save(m,d):
 path=location(m,d['job_id'])
 with state_lock(path):
  old=m.read(path)
  if old.get('cancel_requested'):d['cancel_requested']=True
  if any(old.get(k)!=d.get(k) for k in ('schema','job_id','nonce','pid','process_start','scope','operation','tokens')):raise ValueError('Update worker ownership changed')
  atom(m,path,d)
def report(m,d,phase,done=0,total=0):
 d.update(phase=phase,complete=done,total=total,percent=int(100*done/total) if total else -1);save(m,d)
def cancelled(m,d):
 if m.read(location(m,d['job_id'])).get('cancel_requested'):raise InterruptedError('Scan cancelled; previous index retained')

def scan_roots(m,e,mode):
 n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM
 n.SYSTEM_ALIASES.update({'xenia':('xbox360','xenia'),'azahar':('3ds','azahar','citra'),'dolphin':('wii','dolphin'),'ppsspp':('psp','ppsspp')})
 roots=n.mounted_storage();result=[]
 for root in roots:
  for p in (n.fast_roots_for_mount(root,e) if mode=='fast' else [root]):
   if p not in result:result.append(p)
 return result

def candidate(m,s,path):
 p=workflow().profile_module();e=s['emulator'];base=s['identity'];path=m.safe(path)
 if e=='xenia':
  with path.open('rb') as stream:magic=stream.read(4)
  if magic not in (b'CON ',b'LIVE',b'PIRS'):return None
  meta=m.load(m.COMMON/'gcm-xbox-content-metadata.py').read(path)
  if meta['raw_content_type']!=0xB0000 or meta['title_id']!=base['application_id'] or meta['media_id']!=base['media_id'] or meta['base_version'] not in (base['version'],base['base_version']):return None
 elif e=='azahar':
  if path.suffix.lower()!='.cia':return None
  meta=m.load(m.COMMON/'gcm-cia-metadata-v02.py').read(path)
  if meta['content_type']!='update' or meta['title_id'][8:]!=base['application_id'][8:]:return None
 else:return None
 binding={'path':str(path),'stat_identity':p.ident(path),'metadata':meta,'scope':s}
 token='external:'+hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest()
 return {'token':token,'source':str(path),'binding':binding,'label':path.name+' '+str(meta['internal_version'])}

def scan(m,d):
 w=workflow();s=d['scope'];e=s['emulator'];found={};seen=set();visited=0;warnings=[]
 modes=('fast','deep') if d['operation']=='scan-both' else (d['operation'][5:],)
 if not w.profile_module().CAPABILITIES[e]['update']:modes=()
 for mode in modes:
  for top in scan_roots(m,e,mode):
   m.safe(top);report(m,d,mode.upper()+' SCAN '+str(top));cancelled(m,d)
   def problem(exc):warnings.append(str(exc)[:200])
   for directory,dirs,files in os.walk(top,followlinks=False,onerror=problem):
    cancelled(m,d);path=m.safe(directory);visited+=1
    if visited>200000:raise ValueError('Directory limit reached; previous index retained')
    depth=len(path.relative_to(top).parts)
    dirs[:]=[name for name in sorted(dirs) if not name.startswith('.') and not (path/name).is_symlink() and not (path==m.SYSTEM and name in ('system','saves','screenshots','bios','music','cheats'))]
    if depth>=32:dirs[:]=[];warnings.append('Scan depth limit reached')
    for name in files:
     source=path/name
     if source.is_symlink() or str(source) in seen:continue
     seen.add(str(source))
     try:row=candidate(m,s,source)
     except (ValueError,OSError):continue
     if row:found[row['token']]=row
     if len(found)>100:raise ValueError('More than 100 update candidates; previous index retained')
    if visited%50==0:report(m,d,mode.upper()+' SCAN '+str(top),visited,0)
 # Always enumerate the selected game's retained copies and uninstall journals;
 # recovery stores are never traversed as ordinary storage.
 retained=m.retained_journals(s['rom'],e);local=w.catalog(m,s['rom'],e)
 if scope(m,s['rom'],e)!=s:raise ValueError('Selected game changed before scan completion')
 cancelled(m,d)
 doc={'scope':s,'candidates':list(found.values()),'local_copy_tokens':[r['token'] for r in local],
      'retained_tokens':[r['token'] for r in retained],'directories':visited,'warnings':warnings,'created_epoch':time.time()}
 # Serialize final cancellation check with index publication.
 with state_lock(location(m,d['job_id'])):
  cancelled(m,d);atom(m,index_path(m,s),doc)
 d.update(scan_matches=len(found)+len(local)+len(retained),scan_warnings=warnings)

def select_candidate(m,s,token):
 doc=read_index(m,s);rows=[r for r in doc['candidates'] if r['token']==token]
 if len(rows)!=1:raise ValueError('Selected update is not in this game scan index')
 current=candidate(m,s,rows[0]['source'])
 if current!=rows[0]:raise ValueError('Selected update changed; scan again')
 return current

def prepare_candidates(m,s,tokens):
 w=workflow();p=w.profile_module();e=s['emulator'];selected=[select_candidate(m,s,t) for t in tokens]
 if not selected:raise ValueError('Select detected updates first')
 with m.lock():
  m.idle()
  for row in selected:
   if select_candidate(m,s,row['token'])!=row:raise ValueError('Stale update selection')
   if e=='xenia':
    roots=p.xbox_roots(m)
    if len(roots)!=1:raise ValueError('Multiple Xenia content roots; import an installed update to bind its storage first')
    destination=roots[0]/'0000000000000000'/s['identity']['application_id']/'000B0000'/Path(row['source']).name
    metadata=row['binding']['metadata'];source=m.safe(row['source']);snap=m.tree(source)
    record={'schema':'GCM_EXISTING_UPDATE_ARCHIVE_V1','rom':s['rom'],'emulator':e,'application_id':s['identity']['application_id'],
     'label':row['label'],'native_evidence':{'base':s['identity'],'metadata':metadata,'native_root':str(roots[0]),'emulator':e},
     'registration_evidence':{},'original_token':row['token'],'originals':{str(destination):snap},'groups':[],'local_copy_verified':True,'package_authentication':'UNASSESSED','created_epoch':time.time()}
    copy_package_archive(m,w,record,source,destination)
   elif e=='azahar':prepare_cia(m,w,s,row)
 return {'status':'PASS','count':len(tokens)}

def copy_package_archive(m,w,record,source,destination):
 folder=w.archive_folder(m);folder.mkdir(mode=0o700,parents=True,exist_ok=True);identifier=hashlib.sha256(json.dumps({'scope':record['rom'],'source':str(source),'snapshot':m.tree(source)},sort_keys=True).encode()).hexdigest();dest=folder/identifier
 if (dest/'record.json').exists():w.check_archive(m,m.read(dest/'record.json'),dest/'record.json');return
 temp=folder/('.import-'+uuid.uuid4().hex);temp.mkdir(mode=0o700)
 try:
  n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;n.provider('cemu').worker.check_space(folder,source.stat().st_size)
  expected=w.digest(m,source);before=m.tree(source);local=temp/source.name
  with source.open('rb') as src,local.open('xb') as out:
   remaining=source.stat().st_size
   while remaining:
    b=src.read(min(4*1024*1024,remaining))
    if not b:raise ValueError('Source truncated during local import')
    out.write(b);remaining-=len(b)
   if src.read(1):raise ValueError('Source grew during import')
   out.flush();os.fsync(out.fileno())
  if w.digest(m,local)!=expected or m.tree(source)!=before:raise ValueError('External update changed during local import')
  record['groups']=[{'original':str(destination),'local':source.name,'directory':False,'sha256':expected,'snapshot':m.tree(local)}]
  atom(m,temp/'record.json',record);os.rename(temp,dest)
 finally:
  if temp.exists():
   import shutil;shutil.rmtree(temp)

def prepare_cia(m,w,s,row):
 p=w.profile_module();source=m.safe(row['source']);proof=m.load(m.COMMON/'gcm-cia-payload-verify-v02.py').verify(source);meta=row['binding']['metadata']
 if proof['title_id']!=meta['title_id'] or proof['content_type']!='update':raise ValueError('CIA proof does not match selected update')
 users=[]
 for root in p.ctr_roots(m):
  for id0 in (root/'Nintendo 3DS').glob('*'):
   if not re.fullmatch('[0-9a-fA-F]{32}',id0.name):continue
   for id1 in id0.glob('*'):
    if re.fullmatch('[0-9a-fA-F]{32}',id1.name):users.append((root,m.safe(id1)))
 if len(users)!=1:raise ValueError('Exactly one configured 3DS SDMC user required; run or configure the emulator first')
 root,user=users[0];destination=user/'title/0004000e'/s['identity']['application_id'][8:].lower()/'content'
 folder=w.archive_folder(m);folder.mkdir(mode=0o700,parents=True,exist_ok=True);identifier=hashlib.sha256(json.dumps(row['binding'],sort_keys=True).encode()).hexdigest();dest=folder/identifier
 if (dest/'record.json').exists():w.check_archive(m,m.read(dest/'record.json'),dest/'record.json');return
 temp=folder/('.cia-'+uuid.uuid4().hex);local=temp/'content';local.mkdir(mode=0o700,parents=True)
 try:
  n=m.native();n.COMMON=m.COMMON;n.SYSTEM=m.SYSTEM;n.provider('cemu').worker.check_space(folder,sum(r['size'] for r in meta['records']))
  entries=[]
  with source.open('rb') as stream:
   start,size=meta['sections']['tmd'];stream.seek(start);data=stream.read(size);(local/'00000000.tmd').write_bytes(data)
   entries.append({'path':'00000000.tmd','kind':'file','size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
   for content,verified in zip(meta['records'],proof['verified_records']):
    path=local/(content['content_id']+'.app');stream.seek(verified['offset']);remaining=content['size'];h=hashlib.sha256()
    with path.open('xb') as out:
     while remaining:
      block=stream.read(min(4*1024*1024,remaining))
      if not block:raise ValueError('CIA content truncated')
      out.write(block);h.update(block);remaining-=len(block)
     out.flush();os.fsync(out.fileno())
    if h.hexdigest()!=content['sha256']:raise ValueError('CIA payload changed during local copy')
    entries.append({'path':path.name,'kind':'file','size':content['size'],'sha256':h.hexdigest()})
  if select_candidate(m,s,row['token'])!=row:raise ValueError('CIA changed before import publication')
  manifest={'schema':'GCM_IMPORTED_INSTALLED_FILES_V1','entries':entries};w.verify_copy(m,local,manifest)
  record={'schema':'GCM_EXISTING_UPDATE_ARCHIVE_V1','rom':s['rom'],'emulator':'azahar','application_id':s['identity']['application_id'],
   'label':row['label'],'native_evidence':{'base':s['identity'],'metadata':meta,'native_root':str(root),'emulator':'azahar'},
   'registration_evidence':{},'original_token':row['token'],'originals':{str(destination):{}},
   'groups':[{'original':str(destination),'local':'content','directory':True,'manifest':manifest,'snapshot':m.tree(local)}],
   'local_copy_verified':True,'package_authentication':'UNASSESSED','created_epoch':time.time()}
  atom(m,temp/'record.json',record);os.rename(temp,dest)
 finally:
  if temp.exists():
   import shutil;shutil.rmtree(temp)

def start(m,s,operation,tokens,confirmed):
 if confirmed is not True:raise ValueError('Explicit update job confirmation required')
 if operation not in ('scan-fast','scan-deep','scan-both','prepare'):raise ValueError('Unsupported shared update job')
 if len(tokens)>32 or len(tokens)!=len(set(tokens)):raise ValueError('Select up to 32 unique updates')
 with m.lock():
  m.idle()
  if operation=='prepare':
   for token in tokens:select_candidate(m,s,token)
  identifier=uuid.uuid4().hex;path=location(m,identifier);path.parent.mkdir(mode=0o700,parents=True)
  d={'schema':SCHEMA,'job_id':identifier,'nonce':uuid.uuid4().hex,'scope':s,'operation':operation,'tokens':tokens,'pid':None,'process_start':None,'boot_id':boot(),
   'created_epoch':time.time(),'status':'RUNNING','phase':'QUEUED','label':'','complete':0,'total':0,'percent':-1,'error':'','recovery_required':False,'cancel_requested':False}
  atom(m,path,d)
  try:
   child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'worker','--job-id',identifier],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
   d.update(pid=child.pid,process_start=owner(child.pid))
   deadline=time.monotonic()+1.0
   while not d['process_start'] and child.poll() is None and time.monotonic()<deadline:
    time.sleep(0.01);d['process_start']=owner(child.pid)
   if not d['process_start']:raise ValueError('Cannot establish update worker ownership')
   atom(m,path,d)
  except Exception as exc:d.update(status='ERROR',error=str(exc));atom(m,path,d);raise
 return d

def status(m,s,operation,identifier=None,tokens=None):
 candidates=[m.read(location(m,identifier))] if identifier else [m.read(p) for p in jobs(m).glob('*/state.json')]
 candidates=[d for d in candidates if d.get('schema')==SCHEMA and d.get('scope')==s and (d['operation']==operation or operation=='scan' and d['operation'].startswith('scan-')) and (tokens is None or d['tokens']==tokens)]
 if not candidates:return None
 d=max(candidates,key=lambda d:d['created_epoch'])
 if d['status']=='RUNNING' and (d.get('boot_id')!=boot() or not d.get('process_start') or owner(d['pid'])!=d['process_start']):d={**d,'status':'ERROR','phase':'INTERRUPTED','error':'Worker interrupted; copied content and the previous index are preserved'}
 return d

def worker(m,identifier):
 # Parent publishes PID/start-time while holding this same admission lock.
 lock=m.safe(m.COMMON/'state/global-content-actions/.start.lock');fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX);d=m.read(location(m,identifier))
 finally:os.close(fd)
 if d.get('schema')!=SCHEMA or d.get('pid')!=os.getpid() or d.get('process_start')!=owner(os.getpid()) or d.get('boot_id')!=boot():raise ValueError('Stale update worker ownership')
 m._own_job={'path':str(location(m,identifier)),'nonce':d['nonce']};m.progress=lambda phase,done,total:report(m,d,phase,done,total)
 try:
  if scope(m,d['scope']['rom'],d['scope']['emulator'])!=d['scope']:raise ValueError('Selected game changed before worker execution')
  if d['operation'].startswith('scan-'):
   with m.lock():m.idle();scan(m,d)
  else:prepare_candidates(m,d['scope'],d['tokens'])
  d.update(status='PASS',phase='COMPLETE',percent=100)
 except Exception as exc:d.update(status='ERROR',phase='CANCELLED' if isinstance(exc,InterruptedError) else 'STOPPED',error=str(exc)[:1200])
 save(m,d)

def recover_job(m,identifier,confirmed=False):
 if confirmed is not True:raise ValueError('Explicit job recovery confirmation required')
 with m.lock():
  path=location(m,identifier)
  with state_lock(path):
   d=m.read(path)
   if d.get('schema')!=SCHEMA or d.get('operation') not in ('scan-fast','scan-deep','scan-both','prepare'):raise ValueError('Unknown shared update job')
   if d.get('boot_id')==boot() and d.get('process_start') and owner(d.get('pid'))==d['process_start']:raise ValueError('The update worker is still running')
   d.update(status='ERROR',phase='RECOVERY ASSESSED',error='Interrupted worker cleared; previous index and copied files preserved',recovery_required=False);atom(m,path,d)
 return d

def emit(d):
 if d is None:print('BATCH\tSTATUS\tNOT_STARTED');return
 fields={'STATUS':d['status'],'ID':d['job_id'],'JOB_ID':d['job_id'],'OPERATION':d['operation'],'PHASE':d['phase'],'ELAPSED_SECONDS':int(time.time()-d['created_epoch']),
 'TOTAL':d['total'],'COMPLETE':d['complete'],'PERCENT':d['percent'],'LABEL':d['label'],'ERROR':d['error'],'RECOVERY_REQUIRED':'NO'}
 for key,value in fields.items():print('BATCH\t'+key+'\t'+str(value).replace('\t',' ').replace('\n',' '))
 if d['error']:print('BATCH\tFAILURE\tUpdate operation\t'+d['error'].replace('\n',' '))
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('start','status','worker','cancel','recover'));p.add_argument('--rom');p.add_argument('--emulator');p.add_argument('--type',default='update');p.add_argument('--operation',default='prepare');p.add_argument('--tokens',default='');p.add_argument('--confirm',action='store_true');p.add_argument('--job-id');a=p.parse_args();w=workflow();m=w.provider()
 if a.action=='worker':worker(m,a.job_id);return
 if a.action=='recover':emit(recover_job(m,a.job_id,a.confirm));return
 if a.action=='cancel':
  path=location(m,a.job_id)
  with state_lock(path):
   d=m.read(path)
   if d.get('schema')!=SCHEMA or not d['operation'].startswith('scan-'):raise ValueError('Only shared update scans can be cancelled')
   d['cancel_requested']=True;atom(m,path,d)
  print('OK\tCANCEL REQUESTED');return
 s=scope(m,a.rom,a.emulator,a.type);tokens=a.tokens.split(',') if a.tokens else []
 emit(start(m,s,a.operation,tokens,a.confirm) if a.action=='start' else status(m,s,a.operation,a.job_id,tokens if a.operation=='prepare' else None))
if __name__=='__main__':
 try:main()
 except (OSError,ValueError,KeyError) as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
