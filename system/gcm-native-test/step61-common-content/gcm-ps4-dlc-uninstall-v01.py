"""PS4 adapter for shared DLC uninstall; packages are always preserved."""
import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tomllib
import uuid

ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
PINS={'gcm-dlc-uninstall-plan-v01.py': '1facc60593c1bb5d725170cd8b1e19ec1961fbdef171224214e9c0ad218e692a', 'gcm-uninstall-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec'}

def load(name):
    path=ROOT/name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[name]:
        raise ValueError('Uninstall dependency changed: '+name)
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def idle():
    for path in Path('/proc').glob('[0-9]*/comm'):
        try: name=path.read_text().strip().lower()
        except (FileNotFoundError,ProcessLookupError,PermissionError):continue
        if 'shadps4' in name:raise ValueError('Close shadPS4 before uninstalling DLC')
    for path in (ROOT/'state/global-content-actions').glob('*/state.json'):
        job=json.loads(path.read_text())
        if job.get('status')=='RUNNING':raise ValueError('A content operation is still running')
    for directory in ('platform-dlc-install-jobs','platform-version-install-jobs'):
        for path in (ROOT/'state'/directory).rglob('*.json'):
            job=json.loads(path.read_text())
            if job.get('status')=='RUNNING':raise ValueError('An installation is still running')

def plan(rom,tokens):
    if not tokens or len(set(tokens))!=len(tokens):
        raise ValueError('Explicit unique installed DLC selection required')
    preview=load('gcm-dlc-uninstall-plan-v01.py')
    result=preview.preview(rom,'shadps4',tokens)
    if result['unfinished_transactions']:
        raise ValueError('Recover unfinished DLC selection transactions first')
    return result

def configure(preview):
    config=preview.safe(Path('/userdata/system/configs/shadps4/user/config.toml'))
    data=config.read_bytes()
    if tomllib.loads(data.decode()).get('GUI',{}).get('addonInstallDir')!=str(preview.ADDONS):
        raise ValueError('Configured DLC directory differs from supported target')
    return hashlib.sha256(data).hexdigest()

def execute(rom,tokens,confirmed=False):
    if confirmed is not True:raise ValueError('Uninstall confirmation required')
    engine=load('gcm-uninstall-transaction-v01.py')
    preview=load('gcm-dlc-uninstall-plan-v01.py')
    initial=plan(rom,tokens);title=initial['application_id']
    config_hash=configure(preview)
    locks=[ROOT/'state/global-content-actions/.start.lock',
           ROOT/'state/ps4-dlc-selection/.lock',
           preview.MANAGED/title/'installed-dlc/.lock']
    with ExitStack() as stack:
        for path in locks:
            engine.safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            stack.callback(os.close,fd);fcntl.flock(fd,fcntl.LOCK_EX)
        idle()
        current=plan(rom,tokens)
        if current!=initial or configure(preview)!=config_hash:
            raise ValueError('Uninstall plan changed; preview again')
        txid=uuid.uuid4().hex
        state=engine.safe(ROOT/'state/dlc-uninstall-transactions')
        state.mkdir(mode=0o700,parents=True,exist_ok=True)
        for path in state.glob('*.json'):
            journal=json.loads(path.read_text())
            if journal.get('status') not in ('COMPLETE','ROLLED_BACK'):
                raise ValueError('Unfinished uninstall requires recovery')
        store_parking=engine.safe(preview.MANAGED/title/'uninstalled-dlc'/txid)
        addon_parking=engine.safe(preview.ADDONS/'.gcm-uninstalled'/txid)
        store_parking.mkdir(mode=0o700,parents=True)
        moves=[]
        for row in current['rows']:
            label=row['label']
            for key,kind in (('enabled_root','enabled'),('stored_root','store'),('record','record')):
                source=engine.safe(row[key])
                if not source.exists():continue
                parking=addon_parking if kind=='enabled' else store_parking
                parking.mkdir(mode=0o700,parents=True,exist_ok=True)
                destination=parking/(label+'-'+kind)
                moves.append({'source':str(source),'quarantine':str(destination),
                              'snapshot':engine.snapshot(source)})
        # Prepared action evidence must not authorize reuse following uninstall.
        selected_sources={r['preserved_package'] for r in current['rows']}
        for folder in ('prepared-dlc-action-manifests','prepared-dlc-file-evidence','global-menu-file-evidence'):
            for path in sorted((ROOT/'state'/folder).glob('*.json')):
                engine.safe(path)
                doc=json.loads(path.read_text())
                def references(value):
                    if isinstance(value,str):return value in selected_sources
                    if isinstance(value,dict):return any(references(v) for v in value.values())
                    if isinstance(value,list):return any(references(v) for v in value)
                    return False
                if references(doc):
                    parking=engine.safe(path.parent/'.gcm-uninstalled'/txid)
                    parking.mkdir(mode=0o700,parents=True,exist_ok=True)
                    moves.append({'source':str(path),'quarantine':str(parking/path.name),
                                  'snapshot':engine.snapshot(path)})
        if configure(preview)!=config_hash:raise ValueError('Configuration changed before uninstall')
        idle()
        journal_path=state/(txid+'.json')
        result=engine.apply(journal_path,moves,{
            'emulator':'shadps4','application_id':title,'rom':str(rom),
            'selected_tokens':tokens,'configuration_sha256':config_hash,
            'packages_preserved':sorted(selected_sources)})
        print('OK\tUNINSTALLED='+str(len(current['rows'])))
        print('JOURNAL:',journal_path)
        print('Packages preserved; quarantined content retained')
        print('Existing failed-job recovery flags unchanged; assessment remains separate')
        return result

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('preview','uninstall'))
    p.add_argument('--rom',required=True)
    p.add_argument('--emulator',required=True,choices=('shadps4',))
    p.add_argument('--token',action='append',default=[])
    p.add_argument('--all',action='store_true')
    p.add_argument('--confirm',action='store_true')
    args=p.parse_args()
    if args.all and args.token:raise ValueError('Choose all or individual tokens')
    tokens=args.token
    if args.all:
        tokens=[r['token'] for r in load('gcm-dlc-uninstall-plan-v01.py').preview(args.rom,'shadps4',[])['rows']]
    if args.action=='preview':
        result=plan(args.rom,tokens)
        print('GAME:',result['application_id'])
        for row in result['rows']:print('UNINSTALL:',row['label'],'PRESERVE:',row['preserved_package'])
        print('ITEMS:',len(result['rows']))
        print('Preview only; no content moved or recovery flags changed')
    else:execute(args.rom,tokens,args.confirm)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR\t'+str(exc).replace('\n',' '))
        raise SystemExit(1)
