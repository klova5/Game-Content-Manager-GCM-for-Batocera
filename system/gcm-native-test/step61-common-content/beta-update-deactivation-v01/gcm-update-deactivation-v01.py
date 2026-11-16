"""Deactivate updates without uninstalling their managed files or registrations."""
import argparse,contextlib,hashlib,importlib.util,json,os,re,time,uuid
from pathlib import Path
COMMON=Path('/userdata/system/gcm-native-test/step61-common-content')
WORKFLOW_SHA='dd7dedef3dd55ec6a941f96cf518acc28a2375717bdd7885bcdc5bfe6a606593'
PINS={'step61-common-content/gcm-platform-global-backend-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-lifecycle-v01-space-v01-lookup-v01.py': '60674337130b16e42bc4f851ad3f7ada84b64d5a7b64fcccfa2c0a31b7031d6e', 'step60-shared-switch/citron-installed-rows-measured-v01.py': 'c6e0536e81ae6841afbe205301f3a212963af76cd4da12fe3a8c29f3190d846e', 'step60-shared-switch/citron-content-plan.py': 'aae0d5039b57b6a497d4415a0268bcb69195fe218e7adef52ad6e7f194be6b56', 'step59-citron-backend-v01/citron-config-preview.py': '48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3'}
def load(path,expected):
 path=Path(path)
 if any(p.is_symlink() for p in (path,*path.parents)) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Activation dependency changed: '+path.name)
 spec=importlib.util.spec_from_file_location('activation_'+path.stem,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def workflow():return load(COMMON/'beta-global-update-v01/gcm-global-update-workflow-v01.py',WORKFLOW_SHA)
def component(m,relative):return load(m.COMMON.parent/relative,PINS[relative])
def folder(m):return m.COMMON/'state/global-content-actions'
def state(m,identifier):
 if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid activation job ID')
 return m.safe(folder(m)/identifier/'state.json')
def owner(pid):
 try:
  fields=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split();return None if fields[0]=='Z' else fields[19]
 except (OSError,ValueError,IndexError):return None
def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def save(m,path,doc):path.parent.mkdir(mode=0o700,parents=True,exist_ok=True);m.transaction().atomic(path,m.packed(doc))
@contextlib.contextmanager
def operation(m,rom,e,token,action):
 with m.lock(),m.compatibility_locks(e):
  m.idle();identifier=uuid.uuid4().hex;path=state(m,identifier)
  d={'schema':'GCM_UPDATE_ACTIVATION_JOB_V1','job_id':identifier,'rom':str(m.safe(Path(rom).absolute())),'emulator':e,'token':token,'operation':action,'status':'RUNNING','recovery_required':True,'pid':os.getpid(),'process_start':owner(os.getpid()),'boot_id':boot(),'nonce':uuid.uuid4().hex,'created_epoch':time.time(),'base_snapshot':m.tree(rom)}
  save(m,path,d);m._own_job={'path':str(path),'nonce':d['nonce']}
  try:
   yield path.parent/'transaction.json',d
   d.update(status='PASS',recovery_required=False);save(m,path,d)
  except BaseException as exc:
   journal=path.parent/'transaction.json';pending=journal.exists() and m.read(journal).get('status') not in ('COMPLETE','ROLLED_BACK')
   d.update(status='ERROR',recovery_required=pending,error=str(exc)[:1200]);save(m,path,d);raise
  finally:m._own_job=None

def ryu_plan(m,rom,token):
 current=m.ryu_snapshot(rom);config=current['configuration']['updates'];before=config['document'];chosen=before.get('selected')
 if not chosen or chosen.encode().hex()!=token:raise ValueError('Selected update is no longer active')
 if chosen not in before.get('paths',[]):raise ValueError('Active update registration is inconsistent')
 after=json.loads(json.dumps(before));after['selected']=None
 # Only selection changes. Every registered package path and all other settings remain.
 return current,[{'path':config['path'],'before':m.transaction().raw(config['path']),'after':m.packed(after)}]
PS4='step61-common-content/gcm-platform-global-backend-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-lifecycle-v01-space-v01-lookup-v01.py'
def ps4_plan(m,rom,token,identifier):
 p=component(m,PS4);store,identity,records=p.versions(rom);selected=p.selected_digest(store,identity,records)
 matches=[r for r in records if r['package_sha256']==selected and r['source'].encode().hex()==token]
 if len(matches)!=1:raise ValueError('Selected PS4 update is no longer the active verified version')
 receipt=m.COMMON/'state/ps4-update-selection'/(identity['application_id']+'.json');r=m.read(receipt);overlay=m.safe(r['overlay'])
 if r.get('installed_record')!=matches[0]:raise ValueError('Active PS4 selection receipt changed')
 moves=[]
 for path in (overlay,receipt):
  parking=m.safe(path.parent/'.gcm-deactivated-updates'/identifier);parking.mkdir(mode=0o700,parents=True,exist_ok=True)
  moves.append({'source':str(path),'quarantine':str(parking/path.name),'snapshot':m.tree(path)})
 return (identity,records,selected),moves
CITRON_ROWS='step60-shared-switch/citron-installed-rows-measured-v01.py'
CITRON_PLAN='step60-shared-switch/citron-content-plan.py'
CITRON_EDITOR='step59-citron-backend-v01/citron-config-preview.py'
def citron_plan(m,rom,token):
 n=component(m,CITRON_ROWS);planner=component(m,CITRON_PLAN);editor=component(m,CITRON_EDITOR)
 app,records=n.records(rom,'update');title=format(int(app,16)+0x800,'016X');old=n.disabled_keys(app);title_keys=n.disabled_keys(title)
 selected=planner.resolver().resolve(records,old,title_keys,inventory_complete=True)
 if selected is None or n.update_token(selected)!=token:raise ValueError('Selected Citron update is no longer active')
 desired=list(old)
 if 'Update' not in desired:desired.append('Update')
 if planner.resolver().resolve(records,desired,title_keys,inventory_complete=True) is not None:raise ValueError('Native settings cannot deactivate this update')
 before=m.transaction().raw(n.CONFIG);expected,rendered=editor.render_disabled(n.CONFIG,int(app,16),desired)
 if hashlib.sha256(before).hexdigest()!=expected:raise ValueError('Citron settings changed during planning')
 evidence={'application_id':app,'records':records,'title_disabled':title_keys,'disabled':old,'base':m.tree(rom)}
 return evidence,[{'path':str(n.CONFIG),'before':before,'after':rendered}],(n,planner,app,records,title_keys)

def extra_plan(m,rom,e,token,identifier):
 w=workflow();p=w.profile_module();rows=m.collect(rom,e)
 if len(rows)!=1 or rows[0]['token']!=token:raise ValueError('Exactly one identified installed update required; refresh the game inventory')
 row=rows[0];base=p.identify(m,rom,e);moves=[]
 for value in row['moves']:
  src=m.safe(value);p.authorize_destination(m,rom,e,{'application_id':base['application_id']},src)
  parking=m.safe(src.parent/'.gcm-deactivated-updates'/identifier);parking.mkdir(mode=0o700,parents=True,exist_ok=True)
  moves.append({'source':str(src),'quarantine':str(parking/src.name),'snapshot':m.tree(src)})
 return {'base':base,'row':row},moves

def disabled_rows(m,rom,e):
 result=[];rom=str(Path(rom).absolute());e=m.canonical(e)
 if e not in ('xenia','azahar'):return result
 p=workflow().profile_module();base=p.identify(m,rom,e)
 for path in folder(m).glob('*/state.json'):
  d=m.read(path)
  if d.get('schema')!='GCM_UPDATE_ACTIVATION_JOB_V1' or d.get('rom')!=rom or d.get('emulator')!=e or d.get('status')=='RUNNING' or d.get('recovery_required'):continue
  journal=path.parent/'transaction.json'
  if not journal.exists():continue
  j=m.read(journal)
  if j.get('status')!='COMPLETE':continue
  if d.get('selection_evidence',{}).get('base')!=base:raise ValueError('Deactivated update game/storage binding changed')
  for move in j['moves']:
   src=m.safe(move['source']);park=m.safe(move['quarantine']);p.authorize_destination(m,rom,e,{'application_id':base['application_id']},src)
   if src.exists() or not park.exists() or not m.engine().matches(m.tree(park),move['snapshot'],renamed=True):raise ValueError('Deactivated update files changed or their destination is occupied')
  result.append({'section':'AVAILABLE','state':'DEACTIVATED_UPDATE','label':d['selection_evidence']['row']['label'],'token':'deactivated:'+d['job_id']})
 return result

def activate(m,rom,e,token,confirmed=False):
 if confirmed is not True:raise ValueError('Explicit activation confirmation required')
 e=m.canonical(e);rom=str(Path(rom).absolute())
 with m.lock(),m.compatibility_locks(e):
  m.idle();matches=[r for r in disabled_rows(m,rom,e) if r['token']==token]
  if len(matches)!=1:raise ValueError('Select this game deactivated update')
  path=state(m,token.split(':',1)[1]);d=m.read(path);journal=path.parent/'transaction.json'
  # The removal engine reverses the completed parking transaction without deleting files.
  d.update(status='RUNNING',operation='activate',recovery_required=True,pid=os.getpid(),process_start=owner(os.getpid()),boot_id=boot(),nonce=uuid.uuid4().hex);save(m,path,d)
  try:
   m.transaction().recover(journal);d.update(status='PASS',phase='ACTIVATED',recovery_required=False);save(m,path,d)
  except BaseException as exc:
   d.update(status='ERROR',recovery_required=True,error=str(exc));save(m,path,d);raise
 return {'status':'PASS','message':'UPDATE ACTIVATED'}

def deactivate(m,rom,e,token,confirmed=False,after_step=None):
 if confirmed is not True:raise ValueError('Explicit deactivation confirmation required')
 e=m.canonical(e);rom=str(m.safe(Path(rom).absolute()))
 if not token:raise ValueError('Select an active update')
 if e not in ('shadps4','ryujinx','citron-emu','xenia','azahar'):raise ValueError('Use this emulator native disable operation')
 with operation(m,rom,e,token,'deactivate') as (journal,job):
  if e=='ryujinx':
   evidence,changes=ryu_plan(m,rom,token);moves=[]
   def check():
    m.idle()
    if ryu_plan(m,rom,token)[0]!=evidence:raise ValueError('Game or active registration changed')
   def after():
    new=m.ryu_snapshot(rom)
    if new['configuration']['updates']['document'].get('selected') is not None:raise ValueError('Update remains selected')
    expected=json.loads(json.dumps(evidence['configuration']['updates']['document']));expected['selected']=None
    if new['configuration']['updates']['document']!=expected or new['configuration']['dlc']!=evidence['configuration']['dlc'] or new['base_stat_identity']!=evidence['base_stat_identity']:raise ValueError('Unrelated registration or base changed')
  elif e=='shadps4':
   evidence,moves=ps4_plan(m,rom,token,job['job_id']);changes=[]
   def check():
    m.idle()
    if ps4_plan(m,rom,token,job['job_id'])[0]!=evidence:raise ValueError('PS4 active selection changed')
   def after():
    p=component(m,PS4);store,identity,records=p.versions(rom)
    if (identity,records)!=(evidence[0],evidence[1]) or p.selected_digest(store,identity,records) is not None:raise ValueError('PS4 deactivation post-check failed')
  elif e in ('xenia','azahar'):
   evidence,moves=extra_plan(m,rom,e,token,job['job_id']);changes=[]
   def check():
    m.idle()
    if extra_plan(m,rom,e,token,job['job_id'])[0]!=evidence:raise ValueError('Native installed update changed')
   def after():
    if m.collect(rom,e):raise ValueError('Native update is still present after deactivation')
  else:
   evidence,changes,context=citron_plan(m,rom,token);moves=[]
   def check():
    m.idle()
    if citron_plan(m,rom,token)[0]!=evidence:raise ValueError('Citron active selection changed')
   def after():
    n,p,app,records,title_keys=context
    if n.records(rom,'update')!=(app,records) or n.disabled_keys(format(int(app,16)+0x800,'016X'))!=title_keys or m.tree(rom)!=evidence['base']:raise ValueError('Citron base or installed inventory changed')
    if p.resolver().resolve(records,n.disabled_keys(app),title_keys,inventory_complete=True) is not None:raise ValueError('Citron update remains active')
  job['selection_evidence']=evidence;save(m,journal.parent/'state.json',job)
  m.transaction().apply(journal,moves,changes,{'purpose':'DEACTIVATE_UPDATE','rom':rom,'emulator':e,'token':token},check_before=check,check_after=after,after_step=after_step)
 return {'status':'PASS','message':'UPDATE DEACTIVATED; LOCAL FILES AND REGISTRATIONS PRESERVED'}

def recovery_rows(m,rom,e):
 rows=[];rom=str(Path(rom).absolute());e=m.canonical(e)
 for path in folder(m).glob('*/state.json'):
  d=m.read(path)
  if d.get('schema')=='GCM_UPDATE_ACTIVATION_JOB_V1' and d.get('rom')==rom and d.get('emulator')==e and d.get('recovery_required'):
   rows.append({'section':'AVAILABLE','state':'ACTIVATION_RECOVERY_REQUIRED','label':'Interrupted update deactivation','token':'activation-job:'+d['job_id']})
 return rows

def recover(m,rom,e,token,confirmed=False):
 if confirmed is not True:raise ValueError('Explicit activation recovery confirmation required')
 if not token.startswith('activation-job:'):raise ValueError('Select a pending activation recovery')
 e=m.canonical(e);rom=str(Path(rom).absolute());path=state(m,token.split(':',1)[1])
 with m.lock(),m.compatibility_locks(e):
  d=m.read(path)
  if d.get('schema')!='GCM_UPDATE_ACTIVATION_JOB_V1' or d.get('rom')!=rom or d.get('emulator')!=e:raise ValueError('Activation recovery game binding changed')
  if d.get('boot_id')==boot() and d.get('process_start') and owner(d.get('pid'))==d['process_start']:raise ValueError('Activation worker is still active')
  if m.tree(rom)!=d.get('base_snapshot'):raise ValueError('Selected game changed before recovery')
  journal=path.parent/'transaction.json'
  if journal.exists():
   j=m.read(journal)
   if j.get('metadata')!={'purpose':'DEACTIVATE_UPDATE','rom':rom,'emulator':e,'token':d['token']}:raise ValueError('Activation journal binding differs')
  original=json.loads(json.dumps(d));d.update(status='RUNNING',recovery_required=True,pid=os.getpid(),process_start=owner(os.getpid()),boot_id=boot(),nonce=uuid.uuid4().hex);save(m,path,d)
  m._own_job={'path':str(path),'nonce':d['nonce']}
  try:
   m.idle()
   if journal.exists() and m.read(journal)['status'] not in ('COMPLETE','ROLLED_BACK'):m.transaction().recover(journal)
   d.update(status='ERROR',recovery_required=False,phase='RECOVERY ASSESSED');save(m,path,d)
  except BaseException:save(m,path,original);raise
  finally:m._own_job=None
 return {'status':'PASS','message':'DEACTIVATION RECOVERY ASSESSED; REFRESH UPDATE INVENTORY'}

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('deactivate','activate','recover','list'));p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True);p.add_argument('--token',default='');p.add_argument('--confirm',action='store_true');a=p.parse_args();m=workflow().provider()
 if a.action=='list':
  rows=recovery_rows(m,a.rom,a.emulator)+disabled_rows(m,a.rom,a.emulator)
  if m.canonical(a.emulator) in ('xenia','azahar'):
   installed=m.collect(a.rom,a.emulator)
   if len(installed)==1:rows.append({'section':'AVAILABLE','state':'ACTIVE','label':installed[0]['label'],'token':installed[0]['token']})
  for r in rows:print('\t'.join(('ROW',r['section'],r['state'],r['label'],r['token'])))
 else:print('OK\t'+(deactivate(m,a.rom,a.emulator,a.token,a.confirm) if a.action=='deactivate' else activate(m,a.rom,a.emulator,a.token,a.confirm) if a.action=='activate' else recover(m,a.rom,a.emulator,a.token,a.confirm))['message'])
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
