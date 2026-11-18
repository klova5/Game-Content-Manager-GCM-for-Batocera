"""Shared menu entry point: metadata reads in foreground; assessment in background."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
SWITCH=ROOT.parent/'step60-shared-switch'
MANAGED=Path('/userdata/system/game-content/ps4')
GAMES=Path('/userdata/roms/ps4')
# Replaced with immutable module pins by the staging builder.
PINS={}
MODULES={'evidence': ('/userdata/system/gcm-native-test/step61-common-content/gcm-global-menu-file-evidence.py', 'a2b6d031d580ce3aff78d63b977178ff12e98ecab76e1d157907b10282cbd1e3'), 'identity': ('/userdata/system/gcm-native-test/step61-common-content/gcm-ps4-base-identity.py', 'f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438'), 'sfo': ('/userdata/system/gcm-native-test/step61-common-content/gcm-sfo-metadata.py', '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41'), 'native': ('/userdata/system/gcm-native-test/step61-common-content/gcm-platform-native-bridge.py', '7263d407168d02de3b55a1db9b7aa85a4f0469f658e8cc4fc124fddc8f6b767c'), 'dlc_configuration': ('/userdata/system/gcm-native-test/step61-common-content/gcm-ps4-dlc-selection.py', 'b7db3388b8bf030b9e458b8891bf2acdb6165c513c2d662ceaa54a46808a626b'), 'platform': ('/userdata/system/gcm-native-test/step61-common-content/gcm-platform-global-backend-unified-v01-prepared-action-v01-file-reuse-v01-item-progress-v01-lifecycle-v01-space-v01.py', 'f55f1cead0482458c0b4b9b48779d1a4295097b6648b59c5bd2c344a8ded2a7e'), 'citron': ('/userdata/system/gcm-native-test/step60-shared-switch/citron-global-backend-measured-v01-switch-routes-v01-space-v01.py', 'f6b5cff0f688faae3d04a4173ed4dc9d32c49b8b24559fe9cddf9f4fa0cc268e'), 'ryujinx': ('/userdata/system/gcm-native-test/step60-shared-switch/ryujinx-global-backend-measured-v01-switch-routes-v01-space-v01.py', 'ccdce1cd8a8f0a6918c01534f21a1a565fe3956ddc7604eb85472ba7ec71de90'), 'dlc_store': ('/userdata/system/gcm-native-test/step61-common-content/gcm-ps4-dlc-version-store-measured-v02-menu-evidence-v01-prepared-action-v01-file-reuse-v01-space-v01.py', '4c5b1a46772079fe5c85fb570794881dd3197efad085de0658071caa5d3702a3'), 'installed_service': ('/userdata/system/gcm-native-test/step61-common-content/gcm-platform-content-service-v03-measured-v01-menu-evidence-v01-space-v01.py', 'b0e67bf54996af67194ea74d0ef2e7342dc799198b5f47739680fb2587dd7332')}

def load(key):
    path,expected=MODULES[key]
    path=Path(path)
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise ValueError('Shared menu dependency changed: '+path.name)
    spec=importlib.util.spec_from_file_location('menu_'+key,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def invoke(key,arguments,timeout=7200):
    path,expected=MODULES[key]
    if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=expected or Path(path).is_symlink():
        raise ValueError('Shared menu provider differs')
    result=subprocess.run([sys.executable,'-B',path,*arguments],capture_output=True,text=True,timeout=timeout)
    if result.returncode or any(line.startswith('ERROR\t') for line in result.stdout.splitlines()):
        raise ValueError((result.stdout or result.stderr)[-1200:])
    return result.stdout.splitlines()

def provider(emulator):
    if emulator in ('shadps4','rpcs3'):return 'platform'
    if emulator in ('citron','citron-emu'):return 'citron'
    if emulator in ('ryujinx','ryujinx-emu'):return 'ryujinx'
    raise ValueError('Menu adapter unavailable')

def clean(value):
    return ''.join(c if ord(c)>=32 and ord(c)!=127 else ' ' for c in str(value))[:150]

def catalog(rom,kind):
    evidence=load('evidence');identity=load('identity').identify(rom)
    title=identity['application_id'];base=Path(identity['metadata_path'])
    if kind not in ('update','dlc'):raise ValueError('Content type required')
    if kind=='dlc':load('dlc_configuration').configuration(evidence)
    directory=evidence.safe(MANAGED/title/('installed-updates' if kind=='update' else 'installed-dlc'))
    source_directory=MANAGED/title/('updates' if kind=='update' else 'dlc')
    records=[];seen=set()
    if directory.exists():
        for path in sorted(directory.glob('*.json')):
            evidence.safe(path);before=evidence.identity(path)
            if before[2]>1024*1024:raise ValueError('Oversized installed record')
            record=json.loads(path.read_text());digest=record.get('package_sha256','')
            schema='GCM_PS4_INSTALLED_UPDATE_VERSION_V1' if kind=='update' else 'GCM_PS4_INSTALLED_DLC_V1'
            if (record.get('schema')!=schema or record.get('application_id')!=title
                or not re.fullmatch('[0-9a-f]{64}',digest) or path.name!=digest+'.json'
                or Path(record['installed_root'])!=directory/digest
                or Path(record['source']).parent!=source_directory
                or evidence.identity(path)!=before):raise ValueError('Invalid installed menu record')
            evidence.safe(record['source']);evidence.safe(record['installed_root'])
            if kind=='update' and not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',record.get('version','')):
                raise ValueError('Invalid installed update version')
            if kind=='dlc':
                label=record.get('entitlement_label','')
                if not re.fullmatch('[A-Z0-9_]{16}',label) or label in seen:
                    raise ValueError('Invalid or duplicate DLC entitlement')
                seen.add(label)
            metadata_path=evidence.safe(Path(record['installed_root'])/'sce_sys/param.sfo')
            if metadata_path.is_file():
                fields=load('sfo').read(metadata_path)['fields']
                if fields.get('TITLE_ID')!=title:raise ValueError('Installed metadata belongs to another game')
                if kind=='update' and (fields.get('APP_VER')!=record['version'] or fields.get('CATEGORY')!='gp'):
                    raise ValueError('Installed update version disagrees with internal metadata')
                if kind=='dlc' and (fields.get('CONTENT_ID')!=record.get('content_id')
                    or not re.fullmatch(r'[A-Z]{2}[0-9]{4}-'+title+r'_00-'+re.escape(label),record.get('content_id',''))):
                    raise ValueError('DLC entitlement disagrees with internal metadata')
            records.append(record)
    return evidence,identity,base,records

def root_valid(evidence,base,record,root,kind,title):
    return evidence.valid(base,record['source'],root,record['package_sha256'],kind,title)

def selected(identity,records,evidence):
    title=identity['application_id'];overlay=evidence.safe(GAMES/(title+'-UPDATE'))
    alternate=evidence.safe(GAMES/(title+'-patch'))
    receipt=evidence.safe(ROOT/'state/ps4-update-selection'/(title+'.json'))
    if alternate.exists():raise ValueError('Alternate update requires reconciliation')
    if not overlay.exists():return (None,False) if receipt.exists() else (None,True)
    try:
        data=json.loads(receipt.read_text());record=data['installed_record']
        transaction=evidence.safe(data['transaction'])
        if transaction.parent!=receipt.parent:raise ValueError('Invalid selection transaction')
        journal=json.loads(transaction.read_text())
        if (data.get('status')!='SELECTED_FILES_VERIFIED' or data.get('overlay')!=str(overlay)
            or data.get('application_id')!=title or record not in records
            or journal.get('status')!='COMPLETE' or journal.get('overlay')!=str(overlay)
            or journal.get('installed_record')!=record):raise ValueError('Invalid selected record')
        return record,True
    except (OSError,ValueError,KeyError,TypeError):return None,False

def states(rom,kind,assess=False):
    evidence,identity,base,records=catalog(rom,kind)
    title=identity['application_id'];output={};complete=True
    chosen,selection_ok=selected(identity,records,evidence) if kind=='update' else (None,True)
    complete=selection_ok
    if kind=='dlc':
        journals=evidence.safe(ROOT/'state/ps4-dlc-selection')
        if journals.exists():
            for path in journals.glob('transaction-*.json'):
                evidence.safe(path)
                journal=json.loads(path.read_text())
                if journal.get('application_id')==title and journal.get('status') not in ('COMPLETE','ROLLED_BACK'):
                    if assess:raise ValueError('Unfinished DLC transaction requires recovery assessment')
                    complete=False
    for record in records:
        stored=Path(record['installed_root'])
        roots=[stored]
        enabled=False
        if kind=='dlc':
            active=evidence.safe(GAMES/'DLC'/title/record['entitlement_label'])
            if active.exists():roots.append(active);enabled=True
        elif chosen==record:roots.append(GAMES/(title+'-UPDATE'))
        checks=[root_valid(evidence,base,record,item,kind,title) for item in roots]
        if assess and not all(checks):
            if kind=='dlc':
                store=load('dlc_store')
                with store.verification_session(base,Path(record['source'])):
                    for item,valid in zip(roots,checks):
                        if not valid:store.verify(base,Path(record['source']),item)
            else:
                service=load('installed_service')
                for item,valid in zip(roots,checks):
                    if not valid:service.verify_installed(base,record['source'],item,'ps4','update',allow_unrelated=False)
            checks=[root_valid(evidence,base,record,item,kind,title) for item in roots]
        valid=all(checks)
        complete=complete and valid
        token=record['source'].encode().hex()
        output[token]=(record,('ENABLED' if enabled else 'DISABLED') if kind=='dlc' else
                       ('ACTIVE' if chosen==record else 'INSTALLED')) if valid else (record,'REGISTERED_UNVERIFIED')
    if not selection_ok or (kind=='dlc' and not complete):
        # An incomplete transaction or invalid root must never imply a complete enablement selection.
        output={token:(record,'REGISTERED_UNVERIFIED') for token,(record,state) in output.items()}
    return evidence,identity,base,output,complete

def queue(rom):
    evidence=load('evidence')
    state=evidence.safe(ROOT/'state/global-menu-assessments');state.mkdir(mode=0o700,parents=True,exist_ok=True)
    key=hashlib.sha256(str(load('identity').identify(rom)['metadata_path']).encode()).hexdigest()
    lock=evidence.safe(state/(key+'.lock'))
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        # Pass the held lock to the child; it remains held until assessment finishes.
        environment=os.environ.copy()
        for name in ('GCM_ACTION_PROGRESS_PATH','GCM_PROGRESS_ITEM','GCM_PROGRESS_LOCAL_PATH','GCM_PROGRESS_LOCAL_NONCE'):
            environment.pop(name,None)
        subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--mode','assess',
                          '--rom',str(rom),'--emulator','shadps4','--lock-fd',str(fd),
                          '--assessment-id',key],
                         stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         start_new_session=True,pass_fds=(fd,),env=environment)
    finally:os.close(fd)

def inventory(rom,kind,assess=False):
    evidence,identity,base,installed,complete=states(rom,kind,assess)
    if not complete and not assess:queue(rom)
    # The native bridge consults current package proofs and internal metadata; no payload hashing.
    native=load('native')
    reports=native.query(rom,'shadps4',[kind])
    report=reports[0]
    digests={record['package_sha256'] for record,state in installed.values()}
    installed_sources={record['source'] for record,state in installed.values()}
    rows=[]
    for item in report['rows']:
        if item['path'] in installed_sources or (item.get('sha256') and item['sha256'] in digests):continue
        verified=item['verification_state']=='VERIFIED_EXTERNAL'
        rows.append('\t'.join(('ROW','AVAILABLE' if verified else 'DETECTED',
                    'VERIFIED_EXTERNAL' if verified else 'UNVERIFIED',clean(Path(item['path']).stem),item['token'])))
    for token,(record,state) in installed.items():
        label='Update '+record['version'] if kind=='update' else Path(record['source']).stem
        rows.append('\t'.join(('ROW','AVAILABLE',state,clean(label),token)))
    if len({row.split('\t')[4] for row in rows})!=len(rows):raise ValueError('Duplicate menu token')
    # Receipt promotion is expected while a background assessment finishes.
    # Accept the latest states only when the catalogue itself is unchanged.
    latest=states(rom,kind,False)
    current=latest[3]
    if {token:record for token,(record,state) in current.items()}!={token:record for token,(record,state) in installed.items()}:
        raise ValueError('Installed catalogue changed during query; refresh required')
    if current!=installed:
        refreshed=[]
        for row in rows:
            fields=row.split('\t')
            if fields[4] in current:fields[2]=current[fields[4]][1]
            refreshed.append('\t'.join(fields))
        rows=refreshed
    if not latest[4] and not assess:queue(rom)
    return rows

def summary(rom):
    evidence,identity,base,updates,update_ok=states(rom,'update')
    _,_,_,dlc,dlc_ok=states(rom,'dlc')
    active=[r for r,s in updates.values() if s=='ACTIVE']
    if len(active)>1:raise ValueError('Ambiguous selected update')
    if not update_ok:update='UNASSESSED'
    elif active:update=active[0]['version']+' ACTIVE'
    else:
        metadata=load('sfo').read(base)
        update=str(metadata['fields'].get('APP_VER','UNKNOWN'))+' (BASE)'
    count=sum(s=='ENABLED' for r,s in dlc.values())
    text=str(count)+' ENABLED' if dlc_ok else 'UNASSESSED'
    addons=evidence.safe(GAMES/'DLC'/identity['application_id'])
    labels={r['entitlement_label'] for r,s in dlc.values()}
    unknown=sum(evidence.safe(p).is_dir() and p.name not in labels for p in addons.iterdir()) if addons.exists() else 0
    if unknown:text+=' / '+str(unknown)+' UNASSESSED'
    if not update_ok or not dlc_ok:queue(rom)
    return ['SUMMARY\t'+update+'\t'+text]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',required=True)
    parser.add_argument('--rom',required=True)
    parser.add_argument('--emulator',required=True)
    parser.add_argument('--type',choices=('update','dlc'),default='update')
    parser.add_argument('--refresh-evidence',action='store_true')
    parser.add_argument('--lock-fd',type=int)
    parser.add_argument('--assessment-id')
    args,extra=parser.parse_known_args()
    key=provider(args.emulator)
    if args.mode=='assess':
        if args.emulator!='shadps4' or extra:raise ValueError('Unsupported assessment scope')
        if args.lock_fd is not None:os.fstat(args.lock_fd)
        state_path=None
        if args.assessment_id:
            if not re.fullmatch('[0-9a-f]{64}',args.assessment_id):raise ValueError('Invalid assessment identifier')
            state_path=load('evidence').safe(ROOT/'state/global-menu-assessments'/(args.assessment_id+'.json'))
        def record_status(status,error=''):
            if state_path is None:return
            import tempfile
            fd,name=tempfile.mkstemp(prefix='.assessment-',dir=state_path.parent)
            try:
                with os.fdopen(fd,'w') as stream:
                    json.dump({'status':status,'rom':args.rom,'boot_id':load('evidence').boot(),
                               'error':error,'pid':os.getpid()},stream)
                    stream.flush();os.fsync(stream.fileno())
                os.replace(name,state_path)
            finally:
                if os.path.exists(name):os.unlink(name)
        record_status('RUNNING')
        try:
            for kind in ('update','dlc'):states(args.rom,kind,True)
        except Exception as exc:
            record_status('ERROR',str(exc)[:1200]);raise
        record_status('PASS')
        lines=['STATUS\tMENU_EVIDENCE_CURRENT']
    elif args.emulator=='shadps4' and args.mode in ('summary','list','capabilities'):
        if extra:raise ValueError('Unexpected menu query arguments')
        if args.mode=='summary':lines=summary(args.rom)
        elif args.mode=='list':lines=inventory(args.rom,args.type,args.refresh_evidence)
        else:
            update_ok=states(args.rom,'update')[4];dlc_ok=states(args.rom,'dlc')[4]
            if not update_ok or not dlc_ok:queue(args.rom)
            lines=['CAPABILITY\tCOMMIT_AVAILABLE_DLC\t'+str(int(dlc_ok)),
                   'CAPABILITY\tSELECT_UPDATE\t'+str(int(update_ok))]
    elif args.emulator=='rpcs3' and args.mode in ('summary','list'):
        if extra:raise ValueError('Unexpected PS3 menu query arguments')
        lines=load('native').render(args.rom,'rpcs3',args.type if args.mode=='list' else None)
    else:
        arguments=['--mode',args.mode,'--rom',args.rom,'--emulator',args.emulator,*extra]
        if args.mode!='summary':arguments+=['--type',args.type]
        lines=invoke(key,arguments)
    if args.mode=='list' and args.type=='dlc':
        prepared_lines=[]
        for line in lines:
            fields=line.split('\t')
            if len(fields)==5 and fields[:3]==['ROW','AVAILABLE','VERIFIED_EXTERNAL']:
                fields[1]='DETECTED'
                line='\t'.join(fields)
            prepared_lines.append(line)
        lines=prepared_lines
    for line in lines:print(line)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR\t'+str(exc).replace('\n',' ').replace('\t',' ')[:1200]);sys.exit(1)
