"""Native installed-update discovery for additional GCM emulator routes.
Primary layouts: Xenia util/xex2_info.h + vfs/devices/disc_image_device.cc;
Azahar core/file_sys/{ncch_container.h,title_metadata.h}. No emulator execution.
"""
import configparser,hashlib,json,os,re,struct,tomllib
from pathlib import Path
ALIASES={'xenia-canary':'xenia','xenia-edge':'xenia','xenia_canary':'xenia','xenia_edge':'xenia',
 'citra':'azahar','citra-emu':'azahar','azahar-libretro':'azahar','libretro-azahar':'azahar',
 'dolphin-emu':'dolphin','PPSSPP':'ppsspp'}
EXTRA=('xenia','azahar','dolphin','ppsspp')
CAPABILITIES={'xenia':{'update':True,'formats':['STFS title-update files'],'game':['XEX2','XDVDFS ISO','XBLA STFS']},
 'azahar':{'update':True,'formats':['installed update content/TMD directories','decrypted CIA'],'game':['NCCH','NCSD','CIA']},
 'dolphin':{'update':False,'reason':'No independent game-update format is identified by this provider. Base revisions, system titles and DLC are not treated as updates.'},
 'ppsspp':{'update':False,'reason':'No independently identifiable installed game update was found. Base games, savedata, firmware and game-specific DLC are not treated as updates.'}}
def canonical(e):return ALIASES.get(e,e)
def ident(p):
 s=Path(p).stat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
def bounded(m,p,maximum=4*1024*1024):
 p=m.safe(p);before=ident(p)
 if before[2]>maximum:raise ValueError('Metadata exceeds size limit')
 data=p.read_bytes()
 if ident(p)!=before:raise ValueError('Metadata changed while reading')
 return data

def xex_info(data,limit):
 if len(data)<24 or data[:4]!=b'XEX2':raise ValueError('XEX2 base executable required')
 header_size,count=struct.unpack_from('>I',data,8)[0],struct.unpack_from('>I',data,20)[0]
 if not 1<=count<=4096 or 24+8*count>len(data) or header_size>limit:raise ValueError('Invalid XEX optional-header bounds')
 found=[]
 for i in range(count):
  key,offset=struct.unpack_from('>II',data,24+8*i)
  if key==0x40006:
   if offset<24+8*count or offset+24>min(len(data),header_size):raise ValueError('Execution-info outside XEX metadata')
   media,version,base,title=struct.unpack_from('>IIII',data,offset)
   if not title:raise ValueError('Missing Xbox title ID')
   found.append({'application_id':f'{title:08X}','media_id':f'{media:08X}','version':version,'base_version':base})
 if len(found)!=1:raise ValueError('Exactly one XEX execution-info record required')
 return found[0]

def xbox_game(m,rom):
 source=m.safe(Path(rom).absolute());launcher=None
 if source.suffix.lower()=='.xbox360':
  text=bounded(m,source,65536).decode('utf-8-sig').splitlines()
  if not text:raise ValueError('Empty Xbox launcher')
  relative=Path(text[0].strip().replace('\\','/'))
  if relative.is_absolute() or '..' in relative.parts:raise ValueError('Xbox launcher escapes game directory')
  launcher={'path':str(source),'sha256':hashlib.sha256(bounded(m,source,65536)).hexdigest()};source=m.safe(source.parent/relative)
 if source.is_dir():source=m.safe(source/'default.xex')
 before=ident(source)
 with source.open('rb') as stream:
  prefix=stream.read(min(before[2],1024*1024))
  if prefix[:4]==b'XEX2':result=xex_info(prefix,before[2])
  elif prefix[:4] in (b'CON ',b'LIVE',b'PIRS'):
   meta=m.load(m.COMMON/'gcm-xbox-content-metadata.py').read(source)
   if meta['raw_content_type'] not in (0x000D0000,0x00007000):raise ValueError('Selected Xbox container is not a base game')
   result={'application_id':meta['title_id'],'media_id':meta['media_id'],'version':meta['internal_version'],'base_version':meta['base_version']}
  else:
   # Xenia's GDFX image offsets and directory binary-tree layout.
   result=None
   for origin in (0,0xfb20,0x20600,0x2080000,0xfd90000):
    if origin+32*2048+28>before[2]:continue
    stream.seek(origin+32*2048);header=stream.read(28)
    if header[:20]!=b'MICROSOFT*XBOX*MEDIA':continue
    sector,size=struct.unpack_from('<II',header,20)
    if not 14<=size<=32*1024*1024 or origin+sector*2048+size>before[2]:raise ValueError('XDVDFS root bounds invalid')
    stream.seek(origin+sector*2048);directory=stream.read(size);todo=[0];seen=set();entries=[]
    while todo:
     pos=todo.pop()*4
     if pos in seen or len(seen)>65536:raise ValueError('Cyclic or oversized XDVDFS directory')
     seen.add(pos)
     if pos+14>len(directory):raise ValueError('XDVDFS entry outside directory')
     left,right,start,length,flags,n=struct.unpack_from('<HHIIBB',directory,pos)
     if pos+14+n>len(directory):raise ValueError('Truncated XDVDFS name')
     if left:todo.append(left)
     if right:todo.append(right)
     name=directory[pos+14:pos+14+n].decode('ascii','strict')
     if name.lower()=='default.xex' and not flags&0x10:entries.append((start,length))
    if len(entries)!=1:raise ValueError('Unique default.xex required in Xbox image')
    start,length=entries[0]
    if origin+start*2048+length>before[2]:raise ValueError('XEX extent exceeds image')
    stream.seek(origin+start*2048);result=xex_info(stream.read(min(length,1024*1024)),length);break
   if result is None:raise ValueError('XEX2, Xbox 360 STFS base or XDVDFS image required')
 if ident(source)!=before:raise ValueError('Selected Xbox game changed during identification')
 return {**result,'rom':str(Path(rom).absolute()),'path':str(source),'stat_identity':before,'launcher':launcher}

def ctr_game(m,rom):
 source=m.safe(Path(rom).absolute());before=ident(source)
 with source.open('rb') as stream:
  data=stream.read(0x200)
  if len(data)!=0x200:raise ValueError('Truncated 3DS base metadata')
  if data[0x100:0x104]==b'NCSD':
   start,size=struct.unpack_from('<II',data,0x120)
   if not start or not size or (start+size)*512>before[2]:raise ValueError('3DS main partition out of bounds')
   stream.seek(start*512);data=stream.read(0x200)
  if data[0x100:0x104]==b'NCCH':title=struct.unpack_from('<Q',data,0x118)[0]
  elif source.suffix.lower()=='.cia':title=int(m.load(m.COMMON/'gcm-cia-metadata-v02.py').read(source)['title_id'],16)
  else:raise ValueError('3DS NCCH, NCSD or CIA base metadata required')
 if title>>32!=0x00040000:raise ValueError('Selected 3DS file is not a base application')
 if ident(source)!=before:raise ValueError('Selected 3DS game changed during reading')
 return {'application_id':f'{title:016X}','path':str(source),'rom':str(source),'stat_identity':before}

def title_metadata(m,path):
 data=bounded(m,path);sig=struct.unpack_from('>I',data)[0] if len(data)>=4 else 0
 body={0x10000:0x240,0x10001:0x140,0x10002:0x80,0x10003:0x240,0x10004:0x140,0x10005:0x80}.get(sig)
 if body is None or len(data)<body+0x9c4:raise ValueError('Unsupported 3DS TMD signature or bounds')
 title=struct.unpack_from('>Q',data,body+0x4c)[0];version,count=struct.unpack_from('>HH',data,body+0x9c)
 if not 1<=count<=4096 or len(data)!=body+0x9c4+48*count:raise ValueError('Invalid 3DS content-record table')
 records=[];seen=set()
 for i in range(count):
  offset=body+0x9c4+48*i;cid,index,flags,size=struct.unpack_from('>IHHQ',data,offset)
  if cid in seen or not size:raise ValueError('Invalid or duplicate 3DS content entry')
  seen.add(cid);records.append({'id':f'{cid:08x}','size':size,'sha256':data[offset+16:offset+48].hex(),'flags':flags})
 return {'title_id':f'{title:016X}','version':version,'records':records,'path':str(path),'sha256':hashlib.sha256(data).hexdigest()}

def ctr_roots(m):
 roots=[m.SYSTEM/'saves/3ds/azahar-emu/sdmc',m.SYSTEM/'saves/3ds/citra-emu/sdmc',m.SYSTEM/'saves/3ds/Azahar/sdmc']
 for name in ('azahar-emu','citra-emu','citra'):
  path=m.SYSTEM/'system/configs'/name/'qt-config.ini'
  if not path.is_file():continue
  ini=configparser.ConfigParser(interpolation=None,strict=True);ini.read_string(bounded(m,path,1024*1024).decode('utf-8-sig'))
  for section in ini.sections():
   if ini.has_option(section,'use_custom_storage') and not ini.getboolean(section,'use_custom_storage'):continue
   for key,value in ini.items(section):
    if key.lower().replace('\\','/') in ('sdmc_directory','sdmc_path','paths/sdmc_directory'):
     if not Path(value).is_absolute():raise ValueError('Absolute 3DS SDMC directory required')
     # Explicit custom roots take precedence over defaults.
     roots=[m.safe(value)]
 return list(dict.fromkeys(m.safe(r) for r in roots))

def xbox_roots(m):
 roots=[m.SYSTEM/'saves/xbox360']
 candidates=[]
 for name in ('Xenia','xenia'):
  config=m.SYSTEM/'system/configs'/name;candidates.extend((config/'xenia.config.toml',config/'xenia-canary.config.toml',config/'xenia-edge.config.toml'))
 # Batocera's Wine variants store their configuration with the portable executable.
 wine=m.SYSTEM/'system/wine-bottles/xbox360'
 candidates.extend((wine/'xenia/xenia.config.toml',wine/'xenia-canary/xenia-canary.config.toml'))
 for path in candidates:
  if not path.is_file():continue
  doc=tomllib.loads(bounded(m,path,1024*1024).decode('utf-8-sig'));value=doc.get('Storage',{}).get('content_root')
  if value:
   if not Path(value).is_absolute():raise ValueError('Absolute Xenia content_root required')
   roots=[m.safe(value)]
 return list(dict.fromkeys(m.safe(r) for r in roots))

def identify(m,rom,e):
 e=canonical(e)
 if e=='xenia':return xbox_game(m,rom)
 if e=='azahar':return ctr_game(m,rom)
 # For no independent update model, never reinterpret a base or save as an update.
 p=m.safe(Path(rom).absolute())
 return {'application_id':'NO_SEPARATE_UPDATE_MODEL','path':str(p),'stat_identity':ident(p)}

def collect(m,rom,e):
 e=canonical(e);base=identify(m,rom,e);app=base['application_id'];rows=[]
 if not CAPABILITIES[e]['update']:return rows
 if e=='xenia':
  for root in xbox_roots(m):
   for profile in (root,root/'0000000000000000'):
    for title in (app,app.lower()):
     folders=[profile/title/name for name in ('000B0000','000b0000')]
     for file in sorted({f for folder in folders if folder.is_dir() for f in folder.iterdir()}):
      file=m.safe(file)
      if not file.is_file():continue
      meta=m.load(m.COMMON/'gcm-xbox-content-metadata.py').read(file)
      if meta['raw_content_type']!=0xB0000 or meta['title_id']!=app or meta['media_id']!=base['media_id']:raise ValueError('Xbox update header disagrees with selected game/media')
      if meta['base_version'] not in (base['version'],base['base_version']):raise ValueError('Xbox update base version does not match executable')
      m.add(rows,app+' '+str(meta['internal_version']),[file],evidence={'base':base,'metadata':meta,'native_root':str(root),'emulator':e})
 else:
  for root in ctr_roots(m):
   parent=root/'Nintendo 3DS'
   if not parent.is_dir():continue
   for id0 in sorted(parent.iterdir()):
    if not re.fullmatch('[0-9a-fA-F]{32}',id0.name):continue
    m.safe(id0)
    for id1 in sorted(id0.iterdir()):
     if not re.fullmatch('[0-9a-fA-F]{32}',id1.name):continue
     m.safe(id1);folder=id1/'title/0004000e'/app[8:].lower()/'content'
     if not folder.is_dir():continue
     tmds=sorted(folder.glob('*.tmd'))
     if len(tmds)!=1:raise ValueError('Exactly one current 3DS update TMD required')
     meta=title_metadata(m,tmds[0]);expected='0004000E'+app[8:]
     if meta['title_id']!=expected:raise ValueError('3DS update title does not match selected game')
     for record in meta['records']:
      payload=m.safe(folder/(record['id']+'.app'))
      if not payload.is_file() or payload.stat().st_size!=record['size']:raise ValueError('Installed 3DS update payload missing or truncated')
     # Only the content directory is owned; neighboring save data stays untouched.
     m.add(rows,app+' '+str(meta['version']),[folder],evidence={'base':base,'metadata':meta,'native_root':str(root),'emulator':e})
 unique={}
 for row in rows:
  key=tuple(row['moves'])
  if key not in unique:unique[key]=row
 return list(unique.values())

def authorize_destination(m,rom,e,record,path):
 path=m.safe(path);base=identify(m,rom,e);app=base['application_id']
 if record.get('application_id')!=app:raise ValueError('Managed update belongs to another game')
 if e=='azahar':
  for root in ctr_roots(m):
   try:parts=path.relative_to(root/'Nintendo 3DS').parts
   except ValueError:continue
   if len(parts)==6 and all(re.fullmatch('[0-9a-fA-F]{32}',p) for p in parts[:2]) and parts[2:] == ('title','0004000e',app[8:].lower(),'content'):return
 elif e=='xenia':
  for root in xbox_roots(m):
   for profile in (root,root/'0000000000000000'):
    if path.parent.name.upper()=='000B0000' and path.parent.parent in (profile/app,profile/app.lower()) and path.name not in ('.','..'):return
 raise ValueError('Imported update destination is outside current native storage')
