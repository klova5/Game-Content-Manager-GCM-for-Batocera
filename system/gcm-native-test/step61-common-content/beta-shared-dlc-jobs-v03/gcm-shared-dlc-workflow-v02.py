"""Existing Verify Selected DLC row backed by the complete shared preparation job."""
import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent.parent
JOB_NAME='beta-shared-dlc-jobs-v03/gcm-shared-dlc-action-job-v02.py'
JOB_SHA256='431750667874277b0f2e6e8e0792c4283bdb5b4bfebdad4d0c696065afc10fb6'

def dependency():
    path=ROOT/JOB_NAME
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=JOB_SHA256:
        raise ValueError('Shared Verify/Prepare job dependency changed')
    spec=importlib.util.spec_from_file_location('dlc_prepare_job',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def convert(text):
    fields={}
    for line in text.splitlines():
        if line.startswith('ERROR\t'):raise ValueError(line[6:])
        if line.startswith('ACTION\t'):
            parts=line.split('\t',2)
            if len(parts)!=3 or parts[1] in fields:raise ValueError('Invalid preparation status reply')
            fields[parts[1]]=parts[2]
    status=fields.get('STATUS')
    if status=='NOT_STARTED':return ['BATCH\tSTATUS\tNOT_STARTED']
    if status not in ('RUNNING','PASS','ERROR'):raise ValueError('Preparation status missing')
    identifier=fields.get('ID','')
    if len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('Invalid preparation job ID')
    total=int(fields['TOTAL']);complete=int(fields['COMPLETE']);percent=int(fields['PERCENT'])
    if not 0<=complete<=total<=4096 or not -1<=percent<=100:raise ValueError('Invalid measured preparation counts')
    if status=='PASS' and complete!=total:raise ValueError('Incomplete preparation cannot report success')
    output=['BATCH\tSTATUS\t'+status,'BATCH\tID\t'+identifier,
            'BATCH\tTOTAL\t'+str(total),'BATCH\tCOMPLETE\t'+str(complete),
            'BATCH\tPASSED\t'+str(complete),'BATCH\tFAILED\t'+str(int(status=='ERROR')),
            'BATCH\tPHASE\t'+fields.get('PHASE',''),
            'BATCH\tPERCENT\t'+str(percent),'BATCH\tRECOVERY_REQUIRED\t'+fields.get('RECOVERY_REQUIRED','NO')]
    if status=='ERROR':output.append('BATCH\tFAILURE\tDLC preparation\t'+fields.get('ERROR','Preparation failed'))
    return output

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=('start','status'))
    parser.add_argument('--rom',required=True)
    parser.add_argument('--emulator',required=True,choices=('shadps4','citron','citron-emu','ryujinx','ryujinx-emu'))
    parser.add_argument('--tokens',default='')
    parser.add_argument('--operation',choices=('prepare','apply'),default='prepare')
    parser.add_argument('--confirm',action='store_true')
    args=parser.parse_args();job=dependency();output=io.StringIO()
    with redirect_stdout(output):
        if args.action=='start':
            job.start(SimpleNamespace(rom=args.rom,emulator=args.emulator,operation=args.operation,tokens=args.tokens,confirm=args.confirm))
        else:
            if args.tokens or args.confirm:raise ValueError('Status lookup does not accept a selection or confirmation')
            job.latest(SimpleNamespace(rom=args.rom,emulator=args.emulator,operation=args.operation))
    for line in convert(output.getvalue()):print(line)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR\t'+str(exc).replace('\n',' ').replace('\t',' ')[:1200]);sys.exit(1)
