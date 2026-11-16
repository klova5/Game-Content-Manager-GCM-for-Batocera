"""Existing emulator content discovery; metadata only, without payload hashing."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tomllib
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
MANAGED=Path('/userdata/system/game-content')
PINS={'gcm-ps4-base-identity.py': 'f379faaf240e203a41ff7b53c5c6b41139f7427fbc978b774e131d8d90566438', 'gcm-sfo-metadata.py': '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41', 'gcm-installed-content-copy-v01.py': 'dd5026ddbf58cd2cf2f8aa91db6ccf1d0a7d94daef271e049f7240db14296d40', 'gcm-existing-managed-classification-v01.py': 'd7f75efbb9ca82925b5406de5aace7ba36801b65e8382fc13764e96de844f227', 'gcm-global-menu-file-evidence.py': 'a2b6d031d580ce3aff78d63b977178ff12e98ecab76e1d157907b10282cbd1e3'}

def load(name):
    path=ROOT/name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[name]:
        raise ValueError('Existing-content dependency changed: '+name)
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def ps4(rom):
    copier=load('gcm-installed-content-copy-v01.py')
    game=load('gcm-ps4-base-identity.py').identify(rom)
    title=game['application_id'];reader=load('gcm-sfo-metadata.py')
    if not re.fullmatch(r'CUSA[0-9]{5}',title):raise ValueError('Internal PS4 game identity required')
    base=reader.read(game['metadata_path']);rows=[]
    def candidate(path,kind):
        path=copier.safe(path)
        if not path.exists():return
        metadata=path/'sce_sys/param.sfo'
        if not metadata.is_file():return
        copier.safe(metadata);sfo=reader.read(metadata);fields=sfo['fields']
        if fields.get('TITLE_ID')!=title:return
        if kind=='dlc':
            content=fields.get('CONTENT_ID','')
            if not re.fullmatch(r'[A-Z]{2}[0-9]{4}-'+title+r'_00-[A-Z0-9_]{16}',content):return
            if path.name!=content[-16:]:return
        else:
            content=title+':'+str(fields.get('APP_VER',''))
            if fields.get('CATEGORY')!='gp' or not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',str(fields.get('APP_VER',''))):return
        if reader.read(metadata)!=sfo:raise ValueError('Installed metadata changed during discovery')
        rows.append({'section':'AVAILABLE','state':'REGISTERED_UNVERIFIED',
            'content_type':kind,'version':fields.get('APP_VER'),'content_id':content,
            'source_root':str(path),'original_preserved':True,
            'install_locally_available':MANAGED not in path.parents,
            'package_integrity':'UNASSESSED',
            'binding':{'schema':'GCM_EXISTING_CONTENT_BINDING_V1','emulator':'shadps4',
                       'application_id':title,'content_type':kind,'content_id':content,
                       'game_relationship_verified':True,'basis':'INTERNAL_METADATA',
                       'base_metadata':base,'installed_metadata':sfo}})
    config=copier.safe(Path('/userdata/system/configs/shadps4/user/config.toml'))
    config_bytes=config.read_bytes();settings=tomllib.loads(config_bytes.decode())
    addon=settings.get('GUI',{}).get('addonInstallDir')
    if not isinstance(addon,str) or not addon:raise ValueError('Configured DLC directory required')
    directory=copier.safe(Path(addon)/title)
    if directory.exists():
        for path in sorted(directory.iterdir()):
            copier.safe(path)
            if path.is_dir():candidate(path,'dlc')
    # Only recognized separate-update siblings are candidates. A merged base is not an update-only payload.
    base_dir=copier.safe(Path(game['metadata_path']).parent.parent)
    for suffix in ('-UPDATE','-patch'):candidate(base_dir.parent/(title+suffix),'update')
    if reader.read(game['metadata_path'])!=base or config.read_bytes()!=config_bytes:
        raise ValueError('Game identity or emulator configuration changed during discovery')
    classifier=load('gcm-existing-managed-classification-v01.py')
    evidence=load('gcm-global-menu-file-evidence.py')
    for row in rows:row.update(classifier.classify(MANAGED,title,game['metadata_path'],row,evidence))
    return {'schema':'GCM_EXISTING_INSTALLED_INVENTORY_V1','emulator':'shadps4',
            'application_id':title,'rows':rows,'complete_storage_inventory':False,
            'merged_base_import_supported':False,'changes_performed':False}

ADAPTERS={'shadps4':ps4}

def discover(rom,emulator):
    if emulator not in ADAPTERS:raise ValueError('Existing-content discovery adapter not connected: '+emulator)
    return ADAPTERS[emulator](rom)

def main():
    p=argparse.ArgumentParser();p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True)
    args=p.parse_args();doc=discover(args.rom,args.emulator)
    print('GAME:',doc['application_id'])
    for row in doc['rows']:
        print('AVAILABLE:',row['content_type'],row['content_id'])
        print('  ROOT:',row['source_root'])
        print('  STATE:',row['state'],'MANAGED:',row['managed_status'],'INSTALL LOCALLY:',row['install_locally_available'])
    print('ITEMS:',len(doc['rows']))
    print('Metadata inspection only; no payload verification, copy or registration changes')
if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
