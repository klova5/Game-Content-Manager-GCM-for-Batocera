"""Native metadata-based installed inventory. No payload reads or writes.

Supports extracted Cemu titles, RPCS3 disc folders and installed update SFOs,
Vita extracted/installed SFO layouts, PSP extracted disc and installed SFO
layouts. Other formats must use their own native container adapter.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parent
PINS={'gcm-sfo-metadata.py': '282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41', 'gcm-wiiu-title-metadata-v02.py': 'c25b772bd748f31b4cfd31dcd5f907b0a97ce0f403cb2e051bd02f7c26b94a2f', 'gcm-xml-archive-metadata.py': '9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2', 'gcm-native-game-resolver-beta-v01.py': 'f71c3823b525b47d440c9505d9af5bb2e303a88720b2811687cdeaecfeeaffee'}
ALIASES={'rpcs3':'ps3','cemu':'wiiu','vita3k':'psvita','ppsspp':'psp'}


def safe(p):
 p=Path(p)
 if not p.is_absolute() or '..' in p.parts or any(q.is_symlink() for q in (p,*p.parents)):
  raise ValueError('Native metadata path must be absolute and nonsymlink')
 return p

def load(name):
 p=safe(ROOT/name)
 if hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Native reader changed: '+name)
 spec=importlib.util.spec_from_file_location('beta_'+p.stem,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def text(p,limit=1024*1024):
 p=safe(p)
 if p.stat().st_size>limit:raise ValueError('Oversized native metadata')
 before=p.stat();raw=p.read_bytes();after=p.stat()
 if (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
  raise ValueError('Native metadata changed')
 return raw.decode('utf-8-sig')

def xml(path):
 data=text(path)
 if '<!DOCTYPE' in data.upper() or '<!ENTITY' in data.upper():raise ValueError('XML declarations rejected')
 return ET.fromstring(data)

def storage(emulator,system_root=Path('/userdata')):
 home=safe(Path(system_root))/'system/configs'
 saves=Path(system_root)/'saves'
 if emulator=='rpcs3':
  p=home/'rpcs3/vfs.yml'
  if p.exists():
   settings=[]
   for line in text(p).splitlines():
    match=re.fullmatch(r'''\s*["']?/dev_hdd0/["']?\s*:\s*(.*?)\s*''',line)
    if match:settings.append(match.group(1).strip().strip('"\''))
   if len(settings)!=1:raise ValueError('Exactly one RPCS3 HDD VFS mapping required')
   return safe(settings[0])
  return safe(saves/'ps3/rpcs3/dev_hdd0')
 if emulator=='cemu':
  p=home/'cemu/settings.xml'
  if p.exists():
   values=[e.text.strip() for e in xml(p).iter('mlc_path') if e.text and e.text.strip()]
   if len(values)>1:raise ValueError('Ambiguous Cemu MLC path')
   if values:return safe(values[0])
  return safe(saves/'wiiu')
 if emulator=='vita3k':
  p=home/'vita3k/config.yml'
  if p.exists():
   values=[]
   for line in text(p).splitlines():
    match=re.fullmatch(r'\s*pref-path\s*:\s*(.*?)\s*',line)
    if match:values.append(match.group(1).strip('"\''))
   if len(values)>1:raise ValueError('Ambiguous Vita preference path')
   if values and values[0]:return safe(values[0])
  return safe(saves/'psvita')
 if emulator=='ppsspp':
  p=home/'ppsspp/PSP/SYSTEM/ppsspp.ini'
  if p.exists():
   values=re.findall(r'^\s*MemStickDirectory\s*=\s*(.*?)\s*$',text(p),re.M)
   values=[v.strip().strip('"') for v in values if v.strip()]
   if len(values)==1:return safe(values[0])
   if len(values)>1:raise ValueError('Ambiguous PSP memory-stick path')
  raise ValueError('PPSSPP memory-stick setting is not available; no save directory guessed')
 raise ValueError('Native folder inventory adapter unavailable')

def identity(rom,emulator,system_root=Path('/userdata')):
 if emulator not in ALIASES:raise ValueError('Native folder identity adapter unavailable')
 p=safe(Path(rom).absolute())
 if emulator in ('rpcs3','vita3k','ppsspp'):
  native=storage(emulator,system_root) if emulator=='vita3k' or p.suffix.lower()=='.psn' else Path(system_root)
  result=load('gcm-native-game-resolver-beta-v01.py').extended(p,emulator,
      load('gcm-sfo-metadata.py'),native,load('gcm-xml-archive-metadata.py'))
  if result is not None:return result
 if emulator=='cemu':
  if p.is_dir():meta=p/'meta/meta.xml'
  elif p.name=='meta.xml':meta=p
  elif p.suffix.lower()=='.rpx' and p.parent.name=='code':meta=p.parent.parent/'meta/meta.xml'
  else:raise ValueError('Cemu extracted title folder or RPX required')
  result=load('gcm-wiiu-title-metadata-v02.py').read(meta)
  if result['content_type']!='application':raise ValueError('Selected Cemu title must be a base game')
  return {'application_id':result['application_id'],'system':'wiiu','metadata':result}
 if emulator=='rpcs3':meta=p/'PS3_GAME/PARAM.SFO' if p.is_dir() else p
 elif emulator=='vita3k':meta=p/'sce_sys/param.sfo' if p.is_dir() else p
 else:meta=p/'PSP_GAME/PARAM.SFO' if p.is_dir() else p
 if meta.name.lower()!='param.sfo':raise ValueError('Extracted native SFO required for this beta format')
 report=load('gcm-sfo-metadata.py').read(safe(meta));fields=report['fields']
 key='DISC_ID' if emulator=='ppsspp' else 'TITLE_ID';title=fields.get(key,'')
 if not isinstance(title,str) or not re.fullmatch(r'[A-Z]{4}[0-9]{5}',title):raise ValueError('Valid internal game ID required')
 categories={'rpcs3':{'DG'},'vita3k':{'gd'},'ppsspp':{'UG','EG'}}
 if fields.get('CATEGORY') not in categories[emulator]:raise ValueError('Native base-game category required')
 return {'application_id':title,'system':ALIASES[emulator],'metadata':report}

def sfo_row(root,emulator,base,kind):
 path=root/('sce_sys/param.sfo' if emulator=='vita3k' else 'PARAM.SFO')
 if not path.is_file():return None
 report=load('gcm-sfo-metadata.py').read(safe(path));f=report['fields']
 key='DISC_ID' if emulator=='ppsspp' else 'TITLE_ID'
 if f.get(key)!=base['application_id']:return None
 categories={'rpcs3':{'update':{'GD'},'dlc':set()},'vita3k':{'update':{'gp'},'dlc':{'ac'}},'ppsspp':{'update':set(),'dlc':set()}}
 if f.get('CATEGORY') not in categories[emulator][kind]:return None
 if emulator=='rpcs3':
  old=base['metadata']['fields'].get('APP_VER','');new=f.get('APP_VER','')
  if not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',str(old)) or not re.fullmatch(r'[0-9]{2}\.[0-9]{2}',str(new)) or new<=old:return None
 if emulator=='vita3k':
  cid=f.get('CONTENT_ID','')
  if cid and not re.fullmatch(r'[A-Z]{2}[0-9]{4}-'+re.escape(base['application_id'])+r'_[0-9]{2}-[A-Z0-9]{16}',cid):
   raise ValueError('Vita content ID does not match selected game')
  if kind=='dlc' and (not cid or cid[-16:]!=root.name):raise ValueError('Vita entitlement and native folder disagree')
 # No DLC authorization is inferred from save-data SFOs or directory names.
 return {'content_type':kind,'content_id':f.get('CONTENT_ID',base['application_id']),
         'version':f.get('APP_VER',''),'source_root':str(root),'metadata':report}

def inventory(rom,emulator,kind,system_root=Path('/userdata')):
 if kind not in ('update','dlc'):raise ValueError('Update or DLC required')
 base=identity(rom,emulator,system_root);native=storage(emulator,system_root);rows=[]
 if emulator=='cemu':
  high='0005000e' if kind=='update' else '0005000c';low=base['application_id'][8:].lower()
  parent=native/'usr/title'
  # Case-sensitive disks may contain native title components in either hex case.
  roots=[parent/h/l for h in {high,high.upper()} for l in {low,low.upper()}]
  for root in sorted(set(roots)):
   path=root/'meta/meta.xml'
   if not path.is_file():continue
   r=load('gcm-wiiu-title-metadata-v02.py').read(safe(path))
   if r['application_id']!=base['application_id'] or r['content_type']!=kind:raise ValueError('Installed Cemu title metadata disagrees with native path')
   rows.append({'content_type':kind,'content_id':r['title_id'],'version':r['internal_version'],'source_root':str(root),'metadata':r})
 else:
  title=base['application_id']
  if emulator=='rpcs3':roots=[native/'game'/title] if kind=='update' else []
  elif emulator=='vita3k':
   parent=native/'ux0'/('patch' if kind=='update' else 'addcont')/title
   roots=[parent] if kind=='update' else sorted(parent.iterdir()) if parent.is_dir() else []
  else:roots=sorted((native/'PSP/GAME').iterdir()) if (native/'PSP/GAME').is_dir() else []
  for root in roots:
   safe(root)
   if root.is_dir():
    row=sfo_row(root,emulator,base,kind)
    if row:rows.append(row)
 seen=set()
 for row in rows:
  if row['source_root'] in seen:raise ValueError('Duplicate native content root')
  seen.add(row['source_root']);row['token']='native-existing:'+row['source_root'].encode().hex()
  row['state']='REGISTERED_UNVERIFIED';row['section']='AVAILABLE'
  row['binding']={'schema':'GCM_EXISTING_CONTENT_BINDING_V1','emulator':emulator,
   'application_id':base['application_id'],'content_type':kind,'game_relationship_verified':True,
   'identity_evidence':{'base':base['metadata'],'content':row['metadata']},'source_root':row['source_root']}
 if identity(rom,emulator,system_root)!=base or storage(emulator,system_root)!=native:raise ValueError('Selected game metadata or native storage changed during discovery')
 return {'schema':'GCM_NATIVE_FOLDER_INVENTORY_BETA_V1','application_id':base['application_id'],
         'emulator':emulator,'system':base['system'],'native_root':str(native),'rows':rows,
         'payload_hashes_verified':False,'installation_authorized':False}

def resolve(rom,emulator,kind,token,system_root=Path('/userdata')):
 doc=inventory(rom,emulator,kind,system_root)
 matches=[r for r in doc['rows'] if r['token']==token]
 if len(matches)!=1:raise ValueError('Content no longer uniquely bound to current game')
 return matches[0]['binding']

def main():
 p=argparse.ArgumentParser();p.add_argument('--rom',required=True);p.add_argument('--emulator',required=True,choices=tuple(ALIASES));p.add_argument('--type',choices=('update','dlc'),required=True)
 a=p.parse_args();print(json.dumps(inventory(a.rom,a.emulator,a.type),indent=2))
if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
