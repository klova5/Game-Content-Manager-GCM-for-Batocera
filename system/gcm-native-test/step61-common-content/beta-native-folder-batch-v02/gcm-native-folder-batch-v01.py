"""Isolated native folder jobs; shared menu wire protocol, original files retained."""
import argparse, contextlib, fcntl, hashlib, importlib.util, json, os, re
import subprocess, sys, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent
COMMON=ROOT.parent
SYSTEM=Path('/userdata')
PROVIDER_SHA='3ee492e59dc6f8ce33189e6ad74d4d1a08398db51cafa13babefd91a7552aa93'
SCHEMA='GCM_NATIVE_FOLDER_JOB_V1'
OPS=('prepare','install','apply','uninstall','enable','disable','recover','scan-fast','scan-deep','scan-both')

def module():
 path=ROOT/'gcm-native-folder-actions-beta-v01.py'
 if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=PROVIDER_SHA:raise ValueError('Native action provider changed')
 spec=importlib.util.spec_from_file_location('folder_actions',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def provider(emulator):return module().Provider(emulator,COMMON,SYSTEM)
def safe(p):return module().load('gcm-shared-installed-worker-beta-v01.py').safe(p)
def atom(p,d):return module().load('gcm-shared-installed-worker-beta-v01.py').atom(p,d)
def read(p):return module().read_json(p)
def jobs():return COMMON/'state/beta-native-folder-jobs'
def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def owner(pid):
 try:
  parts=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
  return None if parts[0]=='Z' else parts[19]
 except (FileNotFoundError,ProcessLookupError):return None

@contextlib.contextmanager
def lock(path,blocking=True):
 path=safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX if blocking else fcntl.LOCK_EX|fcntl.LOCK_NB);yield
 finally:os.close(fd)

def scope(rom,emulator,kind):
 if kind not in ('update','dlc'):raise ValueError('Content type required')
 p=provider(emulator);identity=p.inventory.identity(rom,emulator,SYSTEM)
 return {'rom':str(safe(Path(rom).absolute())),'emulator':emulator,'kind':kind,'identity':identity,
         'native_root':str(p.inventory.storage(emulator,SYSTEM))}

def key(s):return hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest()
def index_path(s):return COMMON/'state/beta-native-folder-index'/(key(s)+'.json')
def record_id(token):
 if not re.fullmatch(r'folder:[0-9a-f]{64}',token):raise ValueError('Native prepared item token required')
 return token[7:]

def parse_tokens(raw):
 result=raw.split(',') if raw else []
 if len(result)>32 or len(result)!=len(set(result)):raise ValueError('Up to 32 unique selections required')
 if any(len(t)>16384 or not t for t in result):raise ValueError('Invalid content selection')
 return result

def external(s,token):
 doc=read(index_path(s))
 if doc.get('scope')!=s:raise ValueError('Scan belongs to a stale game/storage binding')
 found=[r for r in doc['candidates'] if r['token']==token]
 if len(found)!=1:raise ValueError('Selection no longer present in native index')
 row=found[0];p=provider(s['emulator'])
 if p.source(s['rom'],row['source'],s['kind'])!=row['binding']:raise ValueError('Selected source binding changed')
 return row

def receipts(s):
 p=provider(s['emulator']);result=[]
 for file in (p.state/'items').glob('*/record.json'):
  r=read(file)
  if r.get('rom')!=s['rom'] or r.get('binding',{}).get('emulator')!=s['emulator'] or r.get('binding',{}).get('content_type')!=s['kind']:continue
  if file!=p.path(r['item_id']):raise ValueError('Native receipt path disagrees')
  result.append(r)
 return result

def inventory(s):
 p=provider(s['emulator']);rows=[];used=set();owned_targets=set()
 for r in receipts(s):
  b=p.valid(r,s['rom']);state=r['state']

  interrupted=False
  for jobfile in jobs().glob('*/state.json'):
   old=read(jobfile)
   if old.get('scope')==s and ('folder:'+r['item_id']) in old.get('recovery_items',old.get('tokens',[])):
    stopped=old['status']!='RUNNING' or not old.get('process_start') or owner(old.get('pid'))!=old['process_start']
    if old.get('recovery_required') or (stopped and old.get('mutation_started') and old['status']!='PASS'):interrupted=True
  if r['pending'] or interrupted:state='RECOVERY_REQUIRED'
  elif state in ('ENABLED','DISABLED'):
   path=Path(b['target']) if state=='ENABLED' else Path(b['native_root'])/'.gcm-disabled-content'/r['item_id']
   if p.worker.snapshot(path)!=r['native_snapshot']:state='ASSESSMENT_REQUIRED'
  if state=='UNINSTALLED':continue
  used.add(b['source_root']);owned_targets.add(b['target'])
  # Existing menu labels do not imply publisher package authentication.
  rows.append({'section':'AVAILABLE','state':state,'label':Path(b['source_root']).name+' '+str(b['version']),
               'token':'folder:'+r['item_id']})
 native=p.inventory.inventory(s['rom'],s['emulator'],s['kind'],SYSTEM)
 for row in native['rows']:
  if row['source_root'] not in owned_targets:
   rows.append({'section':'AVAILABLE','state':'REGISTERED_UNVERIFIED',
                'label':Path(row['source_root']).name+' '+str(row['version']),'token':row['token']})
 path=index_path(s)
 if path.exists():
  doc=read(path)
  if doc.get('scope')!=s:raise ValueError('Native index binding changed')
  for row in doc['candidates']:
   if row['source'] in used:continue
   try:external(s,row['token'])
   except (ValueError,OSError):continue
   rows.append({'section':'DETECTED','state':'UNVERIFIED','label':Path(row['source']).name,'token':row['token']})
 if len(rows)>100:raise ValueError('More than 100 matching folder items; refine scan roots')
 return rows

def idle(recovery=False):
 for folder in ('global-content-actions','global-uninstall-jobs','global-install-locally-jobs'):
  parent=COMMON/'state'/folder
  for path in list(parent.glob('*/state.json'))+list(parent.glob('*.json')):
   d=read(path)
   if d.get('status')=='RUNNING' or d.get('recovery_required'):raise ValueError('Existing content job active or awaiting recovery')
 p=provider('cemu')
 for path in (p.state/'items').glob('*/record.json'):
  if read(path).get('pending') and not recovery:raise ValueError('Native folder recovery required before other actions')


def validate(s,operation,tokens):
 p=provider(s['emulator'])
 if operation=='prepare':
  if not tokens:raise ValueError('Select detected folders first')
  return [external(s,t) for t in tokens]
 records={('folder:'+r['item_id']):r for r in receipts(s)}
 if operation=='apply':
  if s['kind']!='dlc':raise ValueError('Shared selection Apply is for DLC')
  if any(t not in records for t in tokens):raise ValueError('Unknown available DLC selection')
  plans=[r for r in records.values() if r['state']!='UNINSTALLED']
 else:
  if not tokens:raise ValueError('Select available folders first')
  if any(t not in records for t in tokens):raise ValueError('Unknown available folder token')
  plans=[records[t] for t in tokens]
 targets=set()
 for r in plans:
  p.valid(r,s['rom'])
  if r['pending'] and operation!='recover':raise ValueError('Native item recovery required')
  if operation=='uninstall' and r['state'] not in ('ENABLED','DISABLED'):raise ValueError('Only installed items can be uninstalled')
  if operation!='recover' and r['state'] in ('ENABLED','DISABLED'):
   current=Path(r['binding']['target']) if r['state']=='ENABLED' else Path(r['binding']['native_root'])/'.gcm-disabled-content'/r['item_id']
   if p.worker.snapshot(current)!=r['native_snapshot']:raise ValueError('Owned native files changed before batch')
  desired=operation in ('install','enable') or (operation=='apply' and ('folder:'+r['item_id']) in tokens)
  if desired:
   target=safe(r['binding']['target'])
   if str(target) in targets:raise ValueError('Multiple selected folders claim one native destination')
   targets.add(str(target))
   if target.exists() and r['state']!='ENABLED':raise ValueError('Existing native destination preserved; version replacement is not implemented')
 return plans

def location(job_id):
 if not re.fullmatch('[0-9a-f]{32}',job_id):raise ValueError('Invalid native job ID')
 return safe(jobs()/job_id/'state.json')

def same_owner(d,c):
 return all(c.get(k)==d.get(k) for k in ('schema','job_id','nonce','pid','process_start','boot_id','scope','operation','tokens'))

def save_owned(d):
 path=location(d['job_id'])
 with lock(path.parent/'.state.lock'):
  current=read(path)
  if not same_owner(d,current) or current['status']!='RUNNING':raise ValueError('Native job progress ownership changed')
  if current.get('cancel_requested'):d['cancel_requested']=True
  d['updated_epoch']=time.time();atom(path,d)

def progress(d,phase,done,total):
 if not 0<=done<=total:raise ValueError('Invalid measured native progress')
 d.update(phase=phase,bytes_complete=done,bytes_total=total,percent=100*done//total if total else -1)
 save_owned(d)

def scan_roots(s):
 # Deep scan adds mounted storage; never traverses configuration, saves or evidence.
 return [SYSTEM/'roms',Path('/media')]

def scan(d):
 s=d['scope'];p=provider(s['emulator']);roots=scan_roots(s);found={};visited=0;seen=set();partial=[]
 def problem(error):partial.append(str(error)[:240])
 for mode,top in [(mode,top) for mode in (('fast','deep') if d['operation']=='scan-both' else (d['operation'][5:],)) for top in roots]:
  if not top.exists():continue
  safe(top)
  d['phase']=mode.upper()+' SCAN '+str(top);save_owned(d)
  for directory,dirs,files in os.walk(top,followlinks=False,onerror=problem):
   visited+=1
   if visited>200000:partial.append('Directory limit reached');break
   path=safe(directory)
   if read(location(d['job_id'])).get('cancel_requested'):raise InterruptedError('Scan cancelled; previous index preserved')
   dirs[:]=[n for n in sorted(dirs) if not n.startswith('.') and not (path/n).is_symlink()]
   depth=len(path.relative_to(top).parts)
   if mode=='fast' and depth>=5:dirs[:]=[]
   if depth>=32:dirs[:]=[];partial.append('Scan depth limit reached')
   source=None
   if s['emulator']=='cemu' and path.name=='meta' and 'meta.xml' in files:source=path.parent
   elif s['emulator']=='vita3k' and path.name=='sce_sys' and 'param.sfo' in files:source=path.parent
   elif s['emulator']=='rpcs3' and 'PARAM.SFO' in files:source=path
   if source is not None and str(source) not in seen:
    seen.add(str(source))
    try:b=p.source(s['rom'],source,s['kind'])
    except (ValueError,OSError):b=None
    if b is not None:
     token='external:'+hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest()
     found[token]={'token':token,'source':str(source),'binding':b}
     if len(found)>100:raise ValueError('More than 100 matching folders; previous index preserved')
   if visited%50==0:progress(d,mode.upper()+' SCAN '+str(top),0,0)
  if visited>200000:break
 if scope(s['rom'],s['emulator'],s['kind'])!=s:raise ValueError('Game/storage changed during scan')
 atom(index_path(s),{'scope':s,'candidates':list(found.values()),'directories':visited,'partial':partial,'created_epoch':time.time()})
 d.update(scan_directories=visited,scan_matches=len(found),scan_warnings=partial)


def worker(job_id):
 path=location(job_id)
 with lock(COMMON/'state/global-content-actions/.start.lock'):
  d=read(path)
  if not d.get('process_start') or d['pid']!=os.getpid() or d['process_start']!=owner(os.getpid()) or d['boot_id']!=boot() or d['status']!='RUNNING':raise ValueError('Native worker ownership changed')
  try:
   idle(d['operation']=='recover')
   s=d['scope'];p=provider(s['emulator']);p.require_stopped()
   if scope(s['rom'],s['emulator'],s['kind'])!=s:raise ValueError('Job binding changed before execution')
   if d['operation'].startswith('scan-'):scan(d)
   else:
    plans=validate(s,d['operation'],d['tokens']);d['total']=len(plans)
    d['recovery_items']=['folder:'+r['item_id'] for r in plans if 'item_id' in r];save_owned(d)
    # Preflight every selected item before the first mutation. Each provider move is journaled.
    for item in plans:
     p.require_stopped();operation=d['operation']
     d['label']=Path(item['source']).name if operation=='prepare' else Path(item['binding']['source_root']).name
     d['percent']=-1;d['phase']='APPLYING CHANGES' if operation=='apply' else operation.upper();save_owned(d)
     cb=lambda phase,done,total:progress(d,phase,done,total)
     if operation=='prepare':p._prepare(s['rom'],item['source'],s['kind'],cb)
     else:
      identifier=item['item_id'];d['mutation_started']=True;save_owned(d)
      if operation=='install':p._install(s['rom'],identifier,cb)
      elif operation=='recover':p._recover(s['rom'],identifier)
      elif operation=='apply':
       desired=('folder:'+identifier) in d['tokens']
       if desired:p._install(s['rom'],identifier,cb)
       elif item['state']=='ENABLED':p._move(s['rom'],identifier,'disable')
      else:p._move(s['rom'],identifier,operation)
     d['complete']+=1;save_owned(d)
   if d['operation']=='recover':
    for oldpath in jobs().glob('*/state.json'):
     prior=read(oldpath)
     if prior['job_id']==job_id:continue
     if prior.get('scope')==s and set(prior.get('recovery_items',prior.get('tokens',[]))).issubset(set(d['tokens'])):
      if prior['status']=='RUNNING' and prior.get('process_start') and owner(prior.get('pid'))==prior['process_start']:continue
      prior.update(status='ERROR',phase='RECOVERY ASSESSED',recovery_required=False,mutation_started=False)
      atom(oldpath,prior)
   warnings=d.get('scan_warnings',[])
   d.update(status='PARTIAL' if warnings else 'PASS',phase='SCAN INCOMPLETE' if warnings else 'COMPLETE',percent=100,
            error='; '.join(warnings[:4])[:1200] if warnings else '')
  except Exception as exc:
   pending=any(read(f).get('pending') for f in (provider(d['scope']['emulator']).state/'items').glob('*/record.json'))
   d.update(status='ERROR',phase='CANCELLED' if isinstance(exc,InterruptedError) else 'STOPPED',error=(str(exc)+'; '+str(d['complete'])+'/'+str(d['total'])+' items completed')[:1200],recovery_required=bool(pending or d.get('mutation_started')))
  save_owned(d)
 return 0 if d['status']=='PASS' else 1

def start(s,operation,tokens,confirmed):
 if not confirmed:raise ValueError('Explicit action confirmation required')
 if operation not in OPS:raise ValueError('Unsupported native folder action')
 with lock(COMMON/'state/global-content-actions/.start.lock',blocking=False):
  idle(operation=='recover')
  for path in jobs().glob('*/state.json'):
   old=read(path)
   if old['status']=='RUNNING' and old['boot_id']==boot() and owner(old.get('pid'))==old.get('process_start') and old.get('process_start') is not None:raise ValueError('Native folder job already active')
   if (old.get('recovery_required') or (old['status']=='RUNNING' and old.get('mutation_started'))) and operation!='recover':raise ValueError('Native job requires recovery assessment')
  if not operation.startswith('scan-'):validate(s,operation,tokens)
  job_id=uuid.uuid4().hex;path=location(job_id);path.parent.mkdir(mode=0o700,parents=True)
  d={'schema':SCHEMA,'job_id':job_id,'nonce':uuid.uuid4().hex,'scope':s,'operation':operation,'tokens':tokens,
     'boot_id':boot(),'pid':None,'process_start':None,'status':'RUNNING','phase':'QUEUED','label':'',
     'total':len(tokens),'complete':0,'percent':-1,'error':'','recovery_required':False,
     'created_epoch':time.time(),'mutation_started':False}
  atom(path,d)
  try:
   child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'worker','--job-id',job_id],
      stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
   d.update(pid=child.pid,process_start=owner(child.pid))
   if not d['process_start']:raise ValueError('Cannot establish worker process ownership')
   atom(path,d)
  except Exception as exc:
   d.update(status='ERROR',error=str(exc));atom(path,d);raise
 return d

def status(s,operation,job_id=None,selected=None):
 if job_id:d=read(location(job_id))
 else:
  found=[read(p) for p in jobs().glob('*/state.json')]
  found=[d for d in found if d.get('scope')==s and (d.get('operation')==operation or (operation=='scan' and d.get('operation','').startswith('scan-'))) and (selected is None or d.get('tokens')==selected)]
  if not found:return None
  d=max(found,key=lambda x:x['created_epoch'])
 if d.get('schema')!=SCHEMA or (s is not None and d['scope']!=s):raise ValueError('Native job scope mismatch')
 if d['status']=='RUNNING' and (d['boot_id']!=boot() or not d.get('process_start') or owner(d['pid'])!=d['process_start']):
  # Status is observational: preserve durable evidence and report interruption.
  d=dict(d,status='ERROR',phase='INTERRUPTED',error='Worker interrupted; inspect retained receipts',recovery_required=d['mutation_started'])
 return d

def clean(v):return ''.join(c if ord(c)>=32 and ord(c)!=127 else ' ' for c in str(v))[:1200]
def emit(d,wire='BATCH'):
 if d is None:print(wire+'\tSTATUS\tNOT_STARTED');return
 fields={'OPERATION':d['operation'],'ELAPSED_SECONDS':int(time.time()-d['created_epoch']),'STATUS':d['status'],'ID':d['job_id'],'JOB_ID':d['job_id'],'PHASE':d['phase'],'LABEL':d['label'],
  'TOTAL':d['total'],'COMPLETE':d['complete'],'PERCENT':d['percent'],'ERROR':d['error'],
  'RECOVERY_REQUIRED':'YES' if d['recovery_required'] else 'NO'}
 if wire=='INSTALL':fields['STATUS']='INSTALLED' if d['status']=='PASS' else d['status']
 for k,v in fields.items():print(wire+'\t'+k+'\t'+clean(v))
 if d['error']:print('BATCH\tFAILURE\tNative folder action\t'+clean(d['error']))

def menu(args):
 s=scope(args.rom,args.emulator,args.type)
 if args.mode=='list':
  for row in inventory(s):
   state=row['state']
   if state=='PREPARED':state='DISABLED' if args.type=='dlc' else 'FOLDER_PREPARED'
   elif args.type=='update':state={'ENABLED':'ACTIVE','DISABLED':'INSTALLED'}.get(state,state)
   print('\t'.join(('ROW',row['section'],state,clean(row['label']),row['token'])))
 elif args.mode=='summary':
  result=[]
  for kind in ('update','dlc'):
   rows=inventory(scope(args.rom,args.emulator,kind));result.append(str(sum(r['state']=='ENABLED' for r in rows))+' ACTIVE')
  print('SUMMARY\t'+'\t'.join(result))
 elif args.mode=='capabilities':
  print('CAPABILITY\tCOMMIT_AVAILABLE_DLC\t'+str(int(args.emulator!='rpcs3')))
  print('CAPABILITY\tSELECT_UPDATE\t1')
 elif args.mode=='select-update':
  d=start(s,'enable',parse_tokens(args.token),args.confirm)
  while d['status']=='RUNNING':
   time.sleep(0.2);d=status(s,'enable',d['job_id'])
  if d['status']!='PASS':raise ValueError(d['error'])
  print('OK\tCONFIGURED_UPDATE_SELECTED')
 elif args.mode=='install-job':
  d=start(s,'install',parse_tokens(args.token),True) if args.action=='start' else status(s,'install',selected=parse_tokens(args.token))
  emit(d,'INSTALL')
 else:raise ValueError('Unsupported native folder menu operation')

def main():
 p=argparse.ArgumentParser();p.add_argument('action',nargs='?',choices=('start','status','worker','cancel','capabilities'))
 p.add_argument('--rom');p.add_argument('--emulator');p.add_argument('--type',choices=('update','dlc'),default='update')
 p.add_argument('--mode');p.add_argument('--operation',choices=OPS+('scan',));p.add_argument('--tokens',default='');p.add_argument('--token',default='')
 p.add_argument('--action',dest='menu_action');p.add_argument('--job-id');p.add_argument('--confirm',action='store_true');p.add_argument('--wire',default='BATCH',choices=('BATCH','ACTION','INSTALL'))
 args=p.parse_args()
 if args.action=='worker':return worker(args.job_id)
 if args.mode:
  args.action=args.menu_action;menu(args);return
 if args.action=='capabilities':print('CAPABILITY\tUNINSTALL_DLC\t'+str(int(args.emulator in ('cemu','vita3k'))));return
 s=scope(args.rom,args.emulator,args.type) if args.rom else None
 if args.action=='start':emit(start(s,args.operation,parse_tokens(args.tokens),args.confirm),args.wire)
 elif args.action=='status':emit(status(s,args.operation,args.job_id,parse_tokens(args.tokens) if args.tokens else None),args.wire)
 elif args.action=='cancel':
  path=location(args.job_id)
  with lock(path.parent/'.state.lock'):
   d=read(path)
   if not d['operation'].startswith('scan-') or d['status']!='RUNNING':raise ValueError('Only running scans can be cancelled')
   d['cancel_requested']=True;atom(path,d)
  emit(d,args.wire)
 else:raise ValueError('Native folder command required')
if __name__=='__main__':
 try:sys.exit(main())
 except Exception as exc:print('ERROR\t'+clean(exc));sys.exit(1)
