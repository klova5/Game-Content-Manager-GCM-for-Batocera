"""Background job transport for the shared uninstall menu action."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
JOBS=ROOT/'state/global-uninstall-jobs'
PROVIDER_SHA='85f51a5d41f8499407ce58cfc62e2dcacf6d7faab9b9e837359dc1216e3875bc'

def safe(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Symlink job path rejected')
    return path

def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def owner(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError):return None

def load_provider():
    path=safe(ROOT/'gcm-global-uninstall-v03.py')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=PROVIDER_SHA:raise ValueError('Shared uninstall provider changed')
    spec=importlib.util.spec_from_file_location('global_uninstall',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def save(path,data):
    engine=load_provider().provider(data['emulator']).load('gcm-uninstall-transaction-v01.py')
    engine.save(path,data)

@contextmanager
def lock(path):
    safe(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);yield
    finally:os.close(fd)

def report(rom,emulator):
    key=hashlib.sha256((str(Path(rom).absolute())+'\0'+emulator).encode()).hexdigest()
    return safe(JOBS/(key+'.json'))

def emit(job):
    fields={'STATUS':job['status'],'ID':job['job_id'],'TOTAL':len(job['tokens']),
            'COMPLETE':len(job['tokens']) if job['status']=='PASS' else 0,
            'PHASE':job.get('phase',''),'PERCENT':100 if job['status']=='PASS' else -1,
            'RECOVERY_REQUIRED':'YES' if job.get('recovery_required') else 'NO'}
    for key,value in fields.items():print('BATCH\t'+key+'\t'+str(value))
    if job.get('error'):print('BATCH\tFAILURE\tUninstall\t'+job['error'].replace('\n',' ').replace('\t',' '))

def start(args):
    if not args.confirm:raise ValueError('Uninstall confirmation required')
    tokens=args.tokens.split(',') if args.tokens else []
    if not tokens or len(tokens)!=len(set(tokens)):raise ValueError('Explicit unique DLC tokens required')
    provider=load_provider().provider(args.emulator)
    provider.plan(args.rom,tokens)
    path=report(args.rom,args.emulator)
    with lock(JOBS/'.start.lock'):
        for prior in JOBS.glob('*.json'):
            old=json.loads(safe(prior).read_text())
            if old.get('status')=='RUNNING' or old.get('recovery_required'):
                raise ValueError('Uninstall running or awaiting recovery assessment')
        # Existing content starts use this lock; adapter reacquires it for the full transaction.
        with lock(ROOT/'state/global-content-actions/.start.lock'):
            provider.idle()
            job={'schema':'GCM_GLOBAL_UNINSTALL_JOB_V1','job_id':uuid.uuid4().hex,
                 'rom':str(Path(args.rom).absolute()),'emulator':args.emulator,'tokens':tokens,
                 'status':'RUNNING','phase':'QUEUED','boot_id':boot(),'pid':None,
                 'process_start':None,'recovery_required':False,'error':''}
            save(path,job)
            try:
                child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'worker',
                    '--rom',job['rom'],'--emulator',args.emulator,'--job-id',job['job_id']],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    start_new_session=True,close_fds=True)
                job.update(pid=child.pid,process_start=owner(child.pid));save(path,job)
            except Exception as exc:
                job.update(status='ERROR',phase='START FAILED',error=str(exc));save(path,job);raise
    emit(job)

def worker(args):
    path=report(args.rom,args.emulator)
    with lock(JOBS/'.start.lock'):
        job=json.loads(path.read_text())
        if (job['job_id']!=args.job_id or job['status']!='RUNNING' or job['boot_id']!=boot()
                or job['pid']!=os.getpid() or job['process_start']!=owner(os.getpid())):
            raise ValueError('Uninstall worker ownership changed')
        job['phase']='UNINSTALLING';save(path,job)
    try:
        load_provider().provider(args.emulator).execute(job['rom'],job['tokens'],True)
    except Exception as exc:
        job.update(status='ERROR',phase='UNINSTALL FAILED',error=str(exc),recovery_required=True)
    else:job.update(status='PASS',phase='COMPLETE')
    with lock(JOBS/'.start.lock'):
        current=json.loads(path.read_text())
        if current['job_id']!=job['job_id']:raise ValueError('Uninstall job replaced')
        save(path,job)

def status(args):
    path=report(args.rom,args.emulator)
    with lock(JOBS/'.start.lock'):
        if not path.exists():print('BATCH\tSTATUS\tNOT_STARTED');return
        job=json.loads(safe(path).read_text())
        if job.get('schema')!='GCM_GLOBAL_UNINSTALL_JOB_V1':raise ValueError('Invalid uninstall job record')
        if job['status']=='RUNNING' and (job['boot_id']!=boot() or not job.get('pid') or
                owner(job['pid'])!=job.get('process_start') or owner(job['pid']) is None):
            job.update(status='ERROR',phase='INTERRUPTED',recovery_required=True,error='Uninstall worker stopped; assess journal before retry')
            save(path,job)
    emit(job)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=('start','status','worker'))
    p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True)
    p.add_argument('--tokens',default='');p.add_argument('--job-id');p.add_argument('--confirm',action='store_true')
    args=p.parse_args()
    {'start':start,'status':status,'worker':worker}[args.action](args)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR\t'+str(exc).replace('\n',' '));raise SystemExit(1)
