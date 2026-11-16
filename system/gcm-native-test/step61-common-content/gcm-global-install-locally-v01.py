"""Shared Install Locally action: import existing files without changing emulator state."""
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import types
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
MANAGED=Path('/userdata/system/game-content')
PINS={'gcm-installed-content-copy-v01.py': 'dd5026ddbf58cd2cf2f8aa91db6ccf1d0a7d94daef271e049f7240db14296d40', 'gcm-existing-installed-content-v02.py': '421950ac01e2e8a632f90d9910d0da40f072e4d7d4f6672e1e43bfdc368e3a19', 'gcm-uninstall-transaction-v01.py': '1ee99982bc125d9a39309640cd3281015dfbf4db6826b7790ed9aab1318d9eec'}

def load(name):
    path=ROOT/name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[name]:
        raise ValueError('Install Locally dependency changed: '+name)
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def inventory(rom,emulator):
    return load('gcm-existing-installed-content-v02.py').discover(rom,emulator)

def local_copy(row,doc):
    copier=load('gcm-installed-content-copy-v01.py')
    parent=copier.safe(MANAGED/'ps4'/doc['application_id']/'imported-installed'/row['content_type'])
    for path in sorted(parent.glob('*/record.json')) if parent.exists() else []:
        copier.safe(path)
        if path.stat().st_size>64*1024*1024:raise ValueError('Oversized imported record')
        record=json.loads(path.read_text())
        if (record.get('schema')!='GCM_IMPORTED_INSTALLED_CONTENT_V1'
            or record.get('import_ready') is not True
            or record.get('boot_id')!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or record.get('original_root')!=row['source_root']
            or record.get('binding')!=row['binding']):continue
        managed=copier.safe(record['managed_root'])
        if managed!=path.parent/'files' or path.parent.parent!=parent:raise ValueError('Imported record location disagrees')
        if copier.tree(Path(row['source_root']))==record['source_snapshot'] and copier.tree(managed)==record['managed_snapshot']:
            return record
    return None

def resolve(rom,emulator,token):
    if not isinstance(token,str) or not token.startswith('existing:'):raise ValueError('Existing-content token required')
    encoded=token[9:]
    if not encoded or len(encoded)>8192 or len(encoded)%2 or any(c not in '0123456789abcdef' for c in encoded):raise ValueError('Invalid existing-content token')
    path=bytes.fromhex(encoded).decode('utf-8')
    load('gcm-installed-content-copy-v01.py').safe(path)
    doc=inventory(rom,emulator)
    rows=[r for r in doc['rows'] if r['source_root']==path]
    if len(rows)!=1:raise ValueError('Installed content is no longer uniquely registered for this game')
    row=rows[0]
    if not row['install_locally_available']:raise ValueError('Content already managed or requires installed-file assessment')
    return doc,row

def idle():
    for folder in ('global-content-actions','global-uninstall-jobs'):
        for path in (ROOT/'state'/folder).rglob('*.json'):
            if path.name.startswith('.'):continue
            doc=json.loads(path.read_text())
            if doc.get('status')=='RUNNING':raise ValueError('Another content operation is running')

def execute_locked(rom,tokens,confirmed=False,progress=None):
    if confirmed is not True or len(tokens)!=1:raise ValueError('Confirm one installed-content import')
    doc,row=resolve(rom,'shadps4',tokens[0])
    existing=local_copy(row,doc)
    if existing:return existing
    copier=load('gcm-installed-content-copy-v01.py')
    parent=MANAGED/'ps4'/doc['application_id']/'imported-installed'/row['content_type']
    record=copier.import_tree(Path(row['source_root']),parent,row['binding'],progress)
    record_path=Path(record['managed_root']).parent/'record.json'
    current_doc,current_row=resolve(rom,'shadps4',tokens[0])
    if current_row['binding']!=row['binding']:
        raise ValueError('Game binding changed; copied content retained but not published as ready')
    if copier.tree(Path(row['source_root']))!=record['source_snapshot']:
        raise ValueError('Original installation changed before import publication')
    record['managed_snapshot']=copier.tree(Path(record['managed_root']))
    record['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    record['import_ready']=True
    copier.atomic_json(record_path,record)
    return record

def execute(rom,tokens,confirmed=False,progress=None):
    engine=load('gcm-uninstall-transaction-v01.py')
    path=engine.safe(ROOT/'state/global-content-actions/.start.lock')
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);idle()
        return execute_locked(rom,tokens,confirmed,progress)
    finally:os.close(fd)

def plan(rom,tokens):
    if len(tokens)!=1:raise ValueError('Select one existing-content item')
    return resolve(rom,'shadps4',tokens[0])

def provider(emulator):
    if emulator!='shadps4':raise ValueError('Install Locally adapter not connected: '+emulator)
    return types.SimpleNamespace(load=load,plan=plan,idle=idle,execute=execute)
