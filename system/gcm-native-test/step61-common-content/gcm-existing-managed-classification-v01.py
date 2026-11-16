"""Classify existing emulator content against game-bound managed records and receipts."""
import json
from pathlib import Path
import re

def classify(managed,title,base,row,evidence):
    kind=row['content_type'];directory=evidence.safe(Path(managed)/'ps4'/title/('installed-dlc' if kind=='dlc' else 'installed-updates'))
    schema='GCM_PS4_INSTALLED_DLC_V1' if kind=='dlc' else 'GCM_PS4_INSTALLED_UPDATE_VERSION_V1'
    matched=False
    for path in sorted(directory.glob('*.json')) if directory.exists() else []:
        evidence.safe(path)
        if path.stat().st_size>1024*1024:raise ValueError('Oversized managed content record')
        before=evidence.identity(path);record=json.loads(path.read_text());digest=record.get('package_sha256','')
        if (record.get('schema')!=schema or record.get('application_id')!=title
                or not re.fullmatch('[0-9a-f]{64}',digest) or path.name!=digest+'.json'
                or Path(record['installed_root'])!=directory/digest
                or Path(record['source']).parent!=Path(managed)/'ps4'/title/('dlc' if kind=='dlc' else 'updates')):
            raise ValueError('Managed content record location or game identity disagrees')
        evidence.safe(record['installed_root']);evidence.safe(record['source'])
        same=(record.get('content_id')==row['content_id']) if kind=='dlc' else record.get('version')==row['version']
        if not same:continue
        matched=True
        stored=evidence.valid(base,record['source'],record['installed_root'],digest,kind,title)
        current=evidence.valid(base,record['source'],row['source_root'],digest,kind,title)
        if evidence.identity(path)!=before:raise ValueError('Managed record changed during classification')
        if stored and current:
            return {'managed_status':'MANAGED_FILES_VERIFIED','install_locally_available':False,
                    'state':'ENABLED' if kind=='dlc' else 'FILES_VERIFIED',
                    'managed_root':record['installed_root'],'installed_verification_available':True}
    if matched:
        return {'managed_status':'MANAGED_RECORD_UNASSESSED','install_locally_available':False,
                'installed_verification_available':True}
    return {'managed_status':'EXTERNAL_INSTALLED','installed_verification_available':True}
