"""Owned background transport for shared existing-content imports."""
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
import time
import uuid

ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
JOBS=ROOT/'state/global-install-locally-jobs'
PROVIDER_SHA='a56f7248d12dfe71c244ac605866b2ca04f43dda4f00abc25b1bad35803a19c4'

def safe(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('Symlink job path rejected')
    return path

def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def owner(pid):
    try:return Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()[19]
    except (FileNotFoundError,ProcessLookupError):return None

def load_provider():
    path=safe(ROOT/'gcm-global-install-locally-v02.py')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=PROVIDER_SHA:raise ValueError('Shared Install Locally provider changed')
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
            'COMPLETE':len(job['tokens']) if job['status']=='PASS' else len(job.get('completed_tokens',[])),
            'PHASE':job.get('phase',''),'PERCENT':100 if job['status']=='PASS' else job.get('percent',-1),
            'RECOVERY_REQUIRED':'YES' if job.get('recovery_required') else 'NO'}
    for key,value in fields.items():print('BATCH\t'+key+'\t'+str(value))
    if job.get('error'):print('BATCH\tFAILURE\tInstall Locally\t'+job['error'].replace('\n',' ').replace('\t',' '))

def start(args):
    if not args.confirm:raise ValueError('Install Locally confirmation required')
    tokens=args.tokens.split(',') if args.tokens else []
    if not tokens or len(tokens)>512 or len(tokens)!=len(set(tokens)):raise ValueError('Explicit unique existing-content tokens required')
    provider=load_provider().provider(args.emulator)
    # Native import providers remain single-item APIs. Validate the first item
    # at admission and each item again inside its original execution path.
    provider.plan(args.rom,[tokens[0]])
    path=report(args.rom,args.emulator)
    with lock(JOBS/'.start.lock'):
        for prior in JOBS.glob('*.json'):
            old=json.loads(safe(prior).read_text())
            if old.get('status')=='RUNNING' or old.get('recovery_required'):
                raise ValueError('Local copy running or awaiting recovery assessment')
        # Existing content starts use this lock; adapter reacquires it for the full transaction.
        with lock(ROOT/'state/global-content-actions/.start.lock'):
            provider.idle()
            job={'schema':'GCM_GLOBAL_INSTALL_LOCALLY_JOB_V1','job_id':uuid.uuid4().hex,
                 'rom':str(Path(args.rom).absolute()),'emulator':args.emulator,'tokens':tokens,
                 'status':'RUNNING','phase':'QUEUED','boot_id':boot(),'pid':None,
                 'process_start':None,'recovery_required':False,'error':'','completed_tokens':[],'complete_count':0}
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
            raise ValueError('Local copy worker ownership changed')
        job['phase']='COPYING';save(path,job)
    try:
        last=[0.0]
        def progress(phase,completed,total):
            now=time.monotonic()
            if now-last[0]<0.25 and completed!=total:return
            last[0]=now;item_percent=int(completed*100/total) if total else 0
            overall=int((len(job.get('completed_tokens',[]))*100+item_percent)/len(job['tokens']))
            job.update(phase=phase,percent=overall)
            with lock(JOBS/'.start.lock'):save(path,job)
        adapter=load_provider().provider(args.emulator)
        for index,token in enumerate(job['tokens']):
            job.update(current_index=index,phase='COPYING ITEM '+str(index+1)+'/'+str(len(job['tokens'])))
            with lock(JOBS/'.start.lock'):save(path,job)
            # Each call retains the provider's own locks, fresh registration/source
            # checks, copy readback verification and idempotent receipt publication.
            item_started=time.monotonic()
            adapter.execute(job['rom'],[token],True,progress=progress)
            job.setdefault('item_seconds',[]).append(round(time.monotonic()-item_started,3))
            job.setdefault('completed_tokens',[]).append(token)
            job['complete_count']=len(job['completed_tokens'])
            with lock(JOBS/'.start.lock'):
                current=json.loads(path.read_text())
                if current['job_id']!=job['job_id']:raise ValueError('Local copy job replaced')
                save(path,job)
    except Exception as exc:
        job.update(status='ERROR',phase='LOCAL COPY FAILED',error=str(exc),recovery_required=True)
    else:job.update(status='PASS',phase='COMPLETE')
    with lock(JOBS/'.start.lock'):
        current=json.loads(path.read_text())
        if current['job_id']!=job['job_id']:raise ValueError('Local copy job replaced')
        save(path,job)

def status(args):
    path=report(args.rom,args.emulator)
    with lock(JOBS/'.start.lock'):
        if not path.exists():print('BATCH\tSTATUS\tNOT_STARTED');return
        job=json.loads(safe(path).read_text())
        if args.tokens and job.get('tokens')!=args.tokens.split(','):
            print('BATCH\tSTATUS\tNOT_STARTED');return
        if job.get('schema')!='GCM_GLOBAL_INSTALL_LOCALLY_JOB_V1':raise ValueError('Invalid Install Locally job record')
        if job['status']=='RUNNING' and (job['boot_id']!=boot() or not job.get('pid') or
                owner(job['pid'])!=job.get('process_start') or owner(job['pid']) is None):
            job.update(status='ERROR',phase='INTERRUPTED',recovery_required=True,error='Local copy worker stopped; assess import before retry')
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
