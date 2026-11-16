"""Selected existing-content verification; delegates every payload operation to pinned providers."""
import argparse,contextlib,hashlib,importlib.util,io,json,os,re,subprocess,sys,time
from pathlib import Path
COMMON=Path('/userdata/system/gcm-native-test/step61-common-content')
PINS={'beta-global-update-v01/gcm-global-update-workflow-v01.py': 'dd7dedef3dd55ec6a941f96cf518acc28a2375717bdd7885bcdc5bfe6a606593', 'beta-managed-delete-v01/gcm-managed-delete-v01.py': 'aa5f2bd124dec01ffb2db8d0e52d3fee98f66ddda31ab1e08cc860ad3e430ca4', 'gcm-global-install-locally-job-v02.py': 'b31210c436b32db9ed365c89b930fe5a7ac6fcacf17cefc3bd515569ebaface7', 'beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py': '2f9c1bf01577888ebe78202b365add28d66bd25760b2ce61e4ea3d154b595102', 'beta-shared-dlc-jobs-v02/gcm-shared-dlc-workflow-v02.py': '94989006c2872f60705f26da5ac0407664809f5dbfb40fadb6369413c9691bd1', 'beta-selected-verification-v03/gcm-existing-verification-queue-v01.py': 'bcd13917a9abe4f7418b3d663ae7690a23ff291ef6da39a85585aaf06f49238e', 'gcm-platform-native-verification.py': '16072c75340c638a7e93f028bd6b7e9c261e17c173b2e35878199f0786acc3c5', '../step60-shared-switch/gcm-switch-verification-batch.py': 'ccec67b43d67778ed74722954bd2382be9655a0c2d172c9c96796fe00c350215', 'beta-global-update-v01/gcm-global-update-jobs-v01.py': 'abe7556525f6af8d06cdadcef8e08c8fc9de8c12c5af455cc20f947a9e2404f0', 'beta-dlc-stage-separation-v02/gcm-dlc-stage-workflow-v01.py': 'e3cc6789caf08c5f0950b1a47030739e0aef29bcc67e99b04beb004a857750d5'}
def load(relative):
 p=COMMON/relative
 if any(q.is_symlink() for q in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[relative]:raise ValueError('Verification dependency changed: '+relative)
 spec=importlib.util.spec_from_file_location('selected_'+p.stem,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def dlc_identity():return load('beta-dlc-stage-separation-v02/gcm-dlc-stage-workflow-v01.py')
def workflow():return load('beta-global-update-v01/gcm-global-update-workflow-v01.py')
def deletion():return load('beta-managed-delete-v01/gcm-managed-delete-v01.py')
def fingerprint(rom,e,kind,row):
 raw=json.dumps({'rom':str(Path(rom).absolute()),'emulator':e,'kind':kind,'row':row},sort_keys=True,separators=(',',':'))
 return 'verify:'+hashlib.sha256(raw.encode()).hexdigest()
def candidates(w,m,rom,e,kind):
 if kind=='update':
  m.allow_native_assessment=True
  try:rows=m.collect(rom,e)
  finally:m.allow_native_assessment=False
 else:
  d=deletion();rows=d.menu_rows(m,d.scope(m,rom,e,kind));rows=[r for r in rows if r['state'] in ('EXISTING_EXTERNAL','UNVERIFIED','VERIFIED_EXTERNAL')]
 if kind=='dlc' and e in ('ryujinx','citron-emu'):
  identity=dlc_identity();rows=[r for r in rows if r['state']!='EXISTING_EXTERNAL' and not identity.checked(rom,e,r['token']) and (lambda path:path is not None and path.lower().endswith('.nsp'))(deletion().source_path(r['token']))]
 result=[]
 for row in rows:
  token=fingerprint(rom,e,kind,row)
  if any(r['token']==token for r in result):raise ValueError('Duplicate unverified inventory row')
  result.append({'token':token,'row':row,'label':row['label']})
 return result

def selected(rows,tokens,maximum=512):
 if not tokens or maximum is not None and len(tokens)>maximum or len(tokens)!=len(set(tokens)):raise ValueError('Select unique items within the supported update limit')
 table={r['token']:r for r in rows}
 if any(t not in table for t in tokens):raise ValueError('Unverified selection changed; reopen the list')
 return [table[t]['row'] for t in tokens]

def import_selected(w,m,rom,e,tokens):
 original=m.collect
 def once(bound_rom,bound_e):
  # This read executes inside the original importer's admission/configuration locks.
  m.collect=original
  if str(Path(bound_rom).absolute())!=str(Path(rom).absolute()) or m.canonical(bound_e)!=e:raise ValueError('Unverified game scope changed')
  rows=original(bound_rom,bound_e)
  choices=[{'token':fingerprint(rom,e,'update',row),'row':row} for row in rows]
  return selected(choices,tokens)
 m.collect=once
 try:return w.import_updates(m,rom,e,True)
 finally:m.collect=original

def fields(text):
 result={}
 for line in text.splitlines():
  p=line.split('\t',2)
  if len(p)==3 and p[0]=='BATCH':result[p[1]]=p[2]
  if line.startswith('ERROR\t'):raise ValueError(line[6:])
 return result

def call(path,args):
 if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Unsafe verification route')
 relative=str(path.relative_to(COMMON))
 if hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[relative]:raise ValueError('Verification route changed')
 result=subprocess.run([sys.executable,'-B',str(path),*args],capture_output=True,text=True,timeout=120)
 if result.returncode:raise ValueError(result.stdout[:1200] or result.stderr[:1200] or 'Verification command failed')
 return result.stdout

def wait_job(path,base,tokens,timeout=14400,confirmed=True):
 reply=fields(call(path,['start',*base,'--tokens',','.join(tokens)]+(['--confirm'] if confirmed else [])))
 identifier=reply.get('ID')
 if reply.get('STATUS')!='RUNNING' or not identifier:raise ValueError('Selected verification did not start')
 deadline=time.monotonic()+timeout
 interval=0.1
 while time.monotonic()<deadline:
  reply=fields(call(path,['status',*base]))
  if reply.get('ID')!=identifier:raise ValueError('Selected verification job identity changed')
  if reply.get('STATUS')=='PASS':return
  if reply.get('STATUS')!='RUNNING':raise ValueError(reply.get('FAILURE') or reply.get('ERROR') or 'Verification stopped; assess recovery')
  time.sleep(interval);interval=min(1.0,interval*1.5)
 raise ValueError('Verification continues or needs recovery; check status before retrying')

def verify(w,m,rom,e,kind,tokens,confirmed):
 if confirmed is not True:raise ValueError('Explicit verification confirmation required')
 e=m.canonical(e);rom=str(m.safe(Path(rom).absolute()))
 if kind=='dlc' and e in ('ryujinx','citron-emu'):
  rows=selected(candidates(w,m,rom,e,kind),tokens,maximum=None) if tokens and tokens[0].startswith('verify:') else [{'token':t} for t in tokens]
  values=[r['token'] for r in rows];count=dlc_identity().verify(rom,e,values,confirmed);d=deletion();d.verified(m,d.scope(m,rom,e,kind),values);return count
 if kind=='update':
  results=import_selected(w,m,rom,e,tokens)
  if any(r['state']=='REVIEW_REQUIRED' for r in results):raise ValueError('Some selected updates require review: '+'; '.join(r.get('note',r['label']) for r in results if r['state']=='REVIEW_REQUIRED'))
 else:
  rows=selected(candidates(w,m,rom,e,kind),tokens)
  groups=[('local',[r['token'] for r in rows if r['state']=='EXISTING_EXTERNAL']),('package',[r['token'] for r in rows if r['state']!='EXISTING_EXTERNAL'])]
  for group,values in groups:
   if not values:continue
   if group=='local':relative='beta-selected-verification-v03/gcm-existing-verification-queue-v01.py';base=['--rom',rom,'--emulator',e]
   elif e in ('cemu','rpcs3','vita3k'):
    relative='beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py';base=['--rom',rom,'--emulator',e,'--type','dlc','--operation','prepare']
   else:relative='beta-shared-dlc-jobs-v02/gcm-shared-dlc-workflow-v02.py';base=['--rom',rom,'--emulator',e,'--operation','prepare']
   # Each established provider receives its own supported batch size.
   batch_size=512 if group=='local' else 32
   for offset in range(0,len(values),batch_size):wait_job(COMMON/relative,base,values[offset:offset+batch_size])
 d=deletion();d.verified(m,d.scope(m,rom,e,kind),[])
 return len(tokens)

def verify_detected(w,m,rom,e,kind,tokens,confirmed):
 if confirmed is not True:raise ValueError('Explicit detected verification confirmation required')
 if kind=='dlc' and m.canonical(e) in ('ryujinx','citron-emu'):
  count=dlc_identity().verify(rom,e,tokens,confirmed);d=deletion();d.verified(m,d.scope(m,rom,e,kind),tokens);return count
 if not tokens or len(tokens)>512 or len(tokens)!=len(set(tokens)):raise ValueError('Select between 1 and 512 unique detected items')
 e=m.canonical(e);rom=str(m.safe(Path(rom).absolute()));before=m.tree(rom)
 base=['--rom',rom,'--emulator',e]
 confirmation=True
 if e in ('cemu','rpcs3','vita3k'):
  relative='beta-native-folder-batch-v06/gcm-native-folder-batch-v01.py';base+=['--type',kind,'--operation','prepare']
 elif kind=='dlc' and e in ('shadps4','ryujinx','citron-emu'):
  relative='beta-shared-dlc-jobs-v02/gcm-shared-dlc-workflow-v02.py';base+=['--operation','prepare']
 elif e=='shadps4' and kind=='update':
  relative='gcm-platform-native-verification.py';base+=['--type','update'];confirmation=False
 elif e in ('ryujinx','citron-emu') and kind=='update':
  relative='../step60-shared-switch/gcm-switch-verification-batch.py';base+=['--type','update'];confirmation=False
 elif e in ('xenia','azahar') and kind=='update':
  relative='beta-global-update-v01/gcm-global-update-jobs-v01.py';base+=['--type','update','--operation','prepare']
 else:raise ValueError('Detected verification provider is not connected for this scope')
 for offset in range(0,len(tokens),32):
  if m.tree(rom)!=before:raise ValueError('Selected base changed during detected verification')
  wait_job(COMMON/relative,base,tokens[offset:offset+32],confirmed=confirmation)
 d=deletion();d.verified(m,d.scope(m,rom,e,kind),tokens)
 return len(tokens)

def local_status(rom,e):
 m=load('gcm-global-install-locally-job-v02.py');path=m.report(rom,e)
 with m.lock(m.JOBS/'.start.lock'):
  if not path.exists():return 'BATCH\tSTATUS\tNOT_STARTED\n'
  job=json.loads(m.safe(path).read_text())
  if job.get('schema')!='GCM_GLOBAL_INSTALL_LOCALLY_JOB_V1' or job.get('rom')!=str(Path(rom).absolute()) or job.get('emulator')!=e:raise ValueError('Local-copy status binding changed')
  if job.get('status')=='RUNNING' and (job.get('boot_id')!=m.boot() or not job.get('pid') or m.owner(job['pid'])!=job.get('process_start') or m.owner(job['pid']) is None):
   # Let the original status path assess interruption; never manufacture a live owner.
   interrupted=True
  else:interrupted=False
  output=io.StringIO()
  with contextlib.redirect_stdout(output):m.emit(job)
  values=job.get('tokens',[])
  if not isinstance(values,list) or any(not isinstance(v,str) or '\n' in v or '\t' in v for v in values):raise ValueError('Invalid local-copy token record')
  result=output.getvalue()+'BATCH\tMATCH_TOKENS\t'+','.join(values)+'\n'
 if interrupted:return call(COMMON/'gcm-global-install-locally-job-v02.py',['status','--rom',rom,'--emulator',e])
 return result

def menu_overlay(rom,e,kind,text):
 w=workflow();m=w.provider();e=m.canonical(e);rom=str(m.safe(Path(rom).absolute()))
 if kind=='dlc' and e in ('ryujinx','citron-emu'):text=dlc_identity().overlay(rom,e,text)
 # An empty/unrelated tombstone store cannot hide any selected-game rows.
 # Only matching entries need the original full identity/snapshot lookup.
 for path in (m.COMMON/'state/deleted-available-entries').glob('*.json'):
  doc=json.loads(m.safe(path).read_text());scope=doc.get('scope',{})
  if scope.get('rom')==rom and scope.get('emulator')==e and scope.get('kind')==kind and doc.get('entries'):
   d=deletion();return d.overlay(m,d.scope(m,rom,e,kind),text)
 return text

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('list','verify','verify-detected','local-status','overlay'));p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True);p.add_argument('--type',choices=('update','dlc'),default='update');p.add_argument('--tokens',default='');p.add_argument('--confirm',action='store_true');p.add_argument('--inventory-base64',default='');a=p.parse_args()
 if a.action=='overlay':
  import base64
  print(menu_overlay(a.rom,a.emulator,a.type,base64.b64decode(a.inventory_base64,validate=True).decode()),end='');return
 if a.action=='local-status':print(local_status(a.rom,a.emulator),end='');return
 w=workflow();m=w.provider();e=m.canonical(a.emulator)
 if a.action=='list':
  for r in candidates(w,m,a.rom,e,a.type):print('\t'.join(('ROW','AVAILABLE','READY',m.clean(r['label']),r['token'])))
 elif a.action=='verify-detected':print('OK\tVERIFIED='+str(verify_detected(w,m,a.rom,e,a.type,a.tokens.split(',') if a.tokens else [],a.confirm)))
 else:print('OK\tVERIFIED='+str(verify(w,m,a.rom,e,a.type,a.tokens.split(',') if a.tokens else [],a.confirm)))
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc).replace('\n',' ').replace('\t',' ')[:2000]);sys.exit(1)
