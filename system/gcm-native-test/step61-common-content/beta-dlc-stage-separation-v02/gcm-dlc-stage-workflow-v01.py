"""Switch DLC identity admission, separate from copy/register and activation."""
import argparse,base64,contextlib,fcntl,hashlib,importlib.util,io,json,os,re,sys,tempfile
from pathlib import Path
COMMON=Path('/userdata/system/gcm-native-test/step61-common-content');SWITCH=COMMON.parent/'step60-shared-switch'
STORE=COMMON/'state/dlc-identity-admission-v01.json';LOCK=STORE.with_suffix('.lock');SCHEMA='GCM_DLC_IDENTITY_ADMISSION_V1'
PINS={'base':(SWITCH/'gcm-switch-base-identity-measured-v01.py','99f68705f60da1217d88bb5253cd2e25f31be49fff59f30d6b3e6bd809a5579a'),'metadata':(COMMON/'beta-dlc-stage-separation-v02/gcm-v35-nsp-identity-dlcid-v01.py','b8b8d24528728d7506bf951e47bed7752a52231306eaf44a78d20fbd0002fb92'),'install':(COMMON/'beta-shared-dlc-jobs-v02/gcm-shared-dlc-workflow-v02.py','896fbab8308ee28d53698d1257e18cb0129f1525715bc91700a402c60cc06268')}
def safe(p):
 p=Path(p)
 if any(q.is_symlink() for q in (p,*p.parents)):raise ValueError('Unsafe DLC identity path')
 return p

def module(key):
 p,digest=PINS[key];safe(p)
 if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('DLC stage dependency changed: '+p.name)
 spec=importlib.util.spec_from_file_location('dlc_stage_'+key,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def canonical(e):
 aliases={'citron':'citron-emu','ryujinx-emu':'ryujinx'};e=aliases.get(e,e)
 if e not in ('ryujinx','citron-emu'):raise ValueError('DLC identity adapter not connected')
 return e

def identity(p):
 p=safe(p)
 if not p.is_file():raise ValueError('DLC/base file unavailable')
 s=p.stat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def source(token):
 t=token[9:] if token.startswith('existing:') else token
 if not re.fullmatch('(?:[0-9a-fA-F]{2})+',t):raise ValueError('Invalid DLC package token')
 p=Path(bytes.fromhex(t).decode())
 if not p.is_absolute() or p.suffix.lower()!='.nsp':raise ValueError('DLC package identity adapter requires an NSP')
 safe(p);return p

def key(rom,e,token):return hashlib.sha256((str(rom)+'\0'+canonical(e)+'\0'+str(source(token))).encode()).hexdigest()

def read():
 safe(STORE)
 if not STORE.exists():return {'schema':SCHEMA,'records':{}}
 data=json.loads(STORE.read_text())
 if data.get('schema')!=SCHEMA or not isinstance(data.get('records'),dict):raise ValueError('Invalid DLC admission store')
 return data

def valid(record,rom,e,token):
 try:return (isinstance(record,dict) and record.get('rom')==str(rom) and record.get('emulator')==canonical(e) and record.get('source')==str(source(token)) and record.get('content_type')=='dlc' and record.get('identity_match') is True and record.get('full_payload_integrity_verified') is False and record.get('base_identity')==identity(rom) and record.get('source_identity')==identity(source(token)))
 except (OSError,ValueError,TypeError):return False

def checked(rom,e,token,data=None):
 try:
  rom=safe(Path(rom).absolute());record=(read() if data is None else data)['records'].get(key(rom,e,token));return record if valid(record,rom,e,token) else None
 except (OSError,ValueError,UnicodeError):return None

@contextlib.contextmanager
def lock():
 safe(LOCK);LOCK.parent.mkdir(mode=0o700,parents=True,exist_ok=True);fd=os.open(LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX);yield
 finally:os.close(fd)

def save(data):
 safe(STORE);fd,name=tempfile.mkstemp(prefix='.dlc-identities-',dir=STORE.parent)
 try:
  with os.fdopen(fd,'w') as f:json.dump(data,f);f.write('\n');f.flush();os.fsync(f.fileno())
  os.chmod(name,0o600);os.replace(name,STORE)
  fd=os.open(STORE.parent,os.O_RDONLY|os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(name):os.unlink(name)

def verify(rom,e,tokens,confirmed):
 if confirmed is not True:raise ValueError('Confirm DLC identity verification')
 if not tokens or len(tokens)!=len(set(tokens)):raise ValueError('Select unique DLC packages')
 # There is no item-count cap or provider chunking: one selection, one receipt commit.
 e=canonical(e);rom=safe(Path(rom).absolute());before=identity(rom);base=module('base').identify(rom)
 if base.get('content_type')!='application':raise ValueError('Highlighted ROM must be a base application')
 app=base['application_id'];reader=module('metadata')
 with lock():
  data=read();planned=[]
  for token in tokens:
   p=source(token)
   if p==rom or p.samefile(rom):raise ValueError('Base game is not DLC')
   current=identity(p);old=data['records'].get(key(rom,e,token))
   if valid(old,rom,e,token) and old.get('application_id')==app:record=old
   else:
    info=reader.identify(p)
    if (info.get('identified') is not True or info.get('content_type')!='dlc' or info.get('application_id')!=app or identity(p)!=current):raise ValueError('DLC does not belong to highlighted game or changed: '+p.name)
    record={'rom':str(rom),'emulator':e,'source':str(p),'application_id':app,'title_id':info['title_id'],'internal_version':info['internal_version'],'content_type':'dlc','identity_match':True,'full_payload_integrity_verified':False,'base_identity':before,'source_identity':current}
   planned.append((key(rom,e,token),record))
  if identity(rom)!=before or any(not valid(record,rom,e,token) for (_,record),token in zip(planned,tokens)):raise ValueError('DLC identity selection changed before commit')
  for k,record in planned:data['records'][k]=record
  save(data)
 return len(tokens)

def overlay(rom,e,text):
 data=read();lines=[]
 for line in text.splitlines():
  f=line.split('\t')
  if len(f)==5 and f[0]=='ROW' and (f[1]=='DETECTED' or f[1]=='AVAILABLE' and f[2] in ('UNVERIFIED','VERIFIED_EXTERNAL','IDENTITY_VERIFIED')):
   if checked(rom,e,f[4],data):f[1:3]=['AVAILABLE','IDENTITY_VERIFIED']
   elif f[1]=='AVAILABLE' and f[2]=='VERIFIED_EXTERNAL':f[2]='IDENTITY_VERIFIED'
   line='\t'.join(f)
  lines.append(line)
 return '\n'.join(lines)+'\n'

def install(rom,e,token,confirmed):
 if confirmed is not True:raise ValueError('Confirm Install DLC')
 # Recheck identity before delegating to the unchanged guarded full verifier,
 # managed copy, registration transaction and durable job provider.
 verify(rom,e,[token],True)
 workflow=module('install');job=workflow.dependency();out=io.StringIO()
 with contextlib.redirect_stdout(out):job.start(type('Args',(),dict(rom=rom,emulator=e,operation='prepare',tokens=token,confirm=True))())
 return '\n'.join(workflow.convert(out.getvalue()))+'\n'

def install_status(rom,e,token):
 workflow=module('install');job=workflow.dependency();out=io.StringIO()
 with contextlib.redirect_stdout(out):job.latest(type('Args',(),dict(rom=rom,emulator=e,operation='prepare'))())
 identifier=''
 for line in out.getvalue().splitlines():
  if line.startswith('ACTION\tID\t'):identifier=line.split('\t',2)[2]
 if not identifier:return 'BATCH\tSTATUS\tNOT_STARTED\n'
 state=job.read(identifier)
 if state.get('tokens')!=[token]:return 'BATCH\tSTATUS\tNOT_STARTED\n'
 return '\n'.join(workflow.convert(out.getvalue()))+'\n'

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('verify','install','install-status'));p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True);p.add_argument('--tokens',default='');p.add_argument('--confirm',action='store_true');a=p.parse_args()
 if a.action=='verify':print('OK\tIDENTITY_VERIFIED='+str(verify(a.rom,a.emulator,a.tokens.split(',') if a.tokens else [],a.confirm)))
 elif a.action=='install':print(install(a.rom,a.emulator,a.tokens,a.confirm),end='')
 else:print(install_status(a.rom,a.emulator,a.tokens),end='')
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc).replace('\n',' ').replace('\t',' ')[:1200]);raise SystemExit(1)
