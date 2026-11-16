"""Native launcher/container identity. Metadata only; never installs or executes ROMs.
ISO directory layout follows ECMA-119. Platform folders retain their own parsers.
"""
import hashlib,os,re,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PS3_READER_SHA='f8741b41b64ce50fd2d754b88893a7e1227564680e789cf0f0ed90a81971330a'

def safe(p):
 p=Path(p)
 if not p.is_absolute() or '..' in p.parts or any(v.is_symlink() for v in (p,*p.parents)):raise ValueError('Absolute nonsymlink game path required')
 return p

def ident(p):
 s=safe(p).stat()
 return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns]

def bounded_text(p,limit=65536):
 before=ident(p)
 if before[2]>limit:raise ValueError('Oversized native launcher')
 raw=p.read_bytes()
 if ident(p)!=before:raise ValueError('Launcher changed while reading')
 return raw.decode('utf-8-sig'),{'path':str(p),'stat_identity':before,'sha256':hashlib.sha256(raw).hexdigest()}

def iso_sfo(path,parts):
 path=safe(path);before=ident(path);sector=2048
 def u32(raw,offset):
  a,b=struct.unpack_from('<I',raw,offset)[0],struct.unpack_from('>I',raw,offset+4)[0]
  if a!=b:raise ValueError('ISO endian fields disagree')
  return a
 def record(raw):
  if len(raw)<34 or raw[0]!=len(raw) or raw[1]!=0 or raw[26] or raw[27] or raw[25]&0x80:
   raise ValueError('Unsupported ISO directory record')
  n=raw[32]
  if n==0 or 33+n>len(raw):raise ValueError('Invalid ISO file identifier')
  extent,size=u32(raw,2),u32(raw,10)
  seq=struct.unpack_from('<H',raw,28)[0]
  if seq!=1 or seq!=struct.unpack_from('>H',raw,30)[0]:raise ValueError('Multi-volume ISO unsupported')
  return {'extent':extent,'size':size,'directory':bool(raw[25]&2),'name':raw[33:33+n]}
 with path.open('rb') as stream:
  def read(offset,size,limit):
   if not 0<=size<=limit or offset<0 or offset+size>before[2]:raise ValueError('ISO metadata extent outside bounds')
   stream.seek(offset);data=stream.read(size)
   if len(data)!=size:raise ValueError('Truncated ISO metadata')
   return data
  primary=None
  for n in range(16,80):
   block=read(n*sector,sector,sector)
   if block[1:6]!=b'CD001' or block[6]!=1:raise ValueError('Unsupported ISO volume descriptors')
   if block[0]==1:
    if struct.unpack_from('<H',block,128)[0]!=sector or struct.unpack_from('>H',block,130)[0]!=sector:raise ValueError('Unsupported ISO sector size')
    primary=block;break
   if block[0]==255:break
  if primary is None:raise ValueError('ISO primary volume descriptor missing')
  volume=u32(primary,80)
  if volume*sector>before[2] or volume<20:raise ValueError('ISO declared volume exceeds file')
  current=record(primary[156:156+primary[156]])
  for index,component in enumerate(parts):
   if not current['directory']:raise ValueError('ISO parent is not a directory')
   data=read(current['extent']*sector,current['size'],8*1024*1024);pos=0;found=[]
   while pos<len(data):
    length=data[pos]
    if length==0:pos=((pos//sector)+1)*sector;continue
    if pos+length>len(data) or pos%sector+length>sector:raise ValueError('ISO directory record crosses boundary')
    item=record(data[pos:pos+length]);pos+=length
    if item['name'] in (b'\x00',b'\x01'):continue
    name=item['name'].decode('ascii').split(';',1)[0].upper()
    if name==component.upper():found.append(item)
   if len(found)!=1:raise ValueError('Exactly one internal ISO '+component+' required')
   current=found[0]
  if current['directory']:raise ValueError('ISO SFO is a directory')
  raw=read(current['extent']*sector,current['size'],4*1024*1024)
 if ident(path)!=before:raise ValueError('ISO changed during metadata reading')
 return raw,{'path':str(path),'stat_identity':before,'metadata_sha256':hashlib.sha256(raw).hexdigest(),'metadata_source':'internal-ISO9660-SFO'}

def native_result(emulator,report,selector=None):
 f=report['fields'];key='DISC_ID' if emulator=='ppsspp' else 'TITLE_ID';title=f.get(key,'')
 if not re.fullmatch('[A-Z]{4}[0-9]{5}',str(title)):raise ValueError('Valid internal native title ID required')
 categories={'rpcs3':{'DG','HG'},'vita3k':{'gd'},'ppsspp':{'UG','EG'}}
 if f.get('CATEGORY') not in categories[emulator]:raise ValueError('Selected metadata is not a native base game')
 if selector:report=dict(report,launcher_evidence=selector)
 return {'application_id':title,'system':{'rpcs3':'ps3','vita3k':'psvita','ppsspp':'psp'}[emulator],'metadata':report}

def checked_ps3_reader():
 import importlib.util
 path=safe(ROOT/'gcm-ps3-base-identity.py')
 if hashlib.sha256(path.read_bytes()).hexdigest()!=PS3_READER_SHA:raise ValueError('PS3 ISO reader dependency changed')
 spec=importlib.util.spec_from_file_location('native_ps3_archive_reader',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def archive_ps3(path,sfo):
 m=checked_ps3_reader();before=ident(path)
 if hashlib.sha256(m.TOOL.read_bytes()).hexdigest()!=m.TOOL_SHA:raise ValueError('Native archive executable changed')
 listing=m.bounded([str(m.TOOL),'l','-slt',str(path),'PS3_GAME/PARAM.SFO','game/*/PARAM.SFO','PARAM.SFO'],4*1024*1024).decode('utf-8','replace')
 candidates=[]
 for block in re.split(r'\r?\n\r?\n',listing):
  match=re.search(r'(?m)^Path = (.+?)\r?$',block)
  if not match:continue
  name=match.group(1)
  if not (name=='PS3_GAME/PARAM.SFO' or name=='PARAM.SFO' or re.fullmatch(r'game/[A-Z]{4}[0-9]{5}/PARAM.SFO',name)):continue
  size=re.search(r'(?m)^Size = ([0-9]+)\r?$',block)
  if size and 20<=int(size.group(1))<=4*1024*1024:candidates.append((name,int(size.group(1))))
 if len(candidates)!=1:raise ValueError('Exactly one native PS3 base SFO required in image')
 name,size=candidates[0];raw=m.bounded([str(m.TOOL),'x','-so',str(path),name],4*1024*1024)
 if len(raw)!=size or ident(path)!=before or hashlib.sha256(m.TOOL.read_bytes()).hexdigest()!=m.TOOL_SHA:raise ValueError('Native image or archive reader changed')
 return {'path':str(path),'stat_identity':before,'metadata_sha256':hashlib.sha256(raw).hexdigest(),'fields':sfo.parse(raw),'metadata_source':'internal-native-image-SFO'}

def extended(rom,emulator,sfo,storage,zip_reader):
 p=safe(Path(rom).absolute());suffix=p.suffix.lower()
 if emulator in ('rpcs3','ppsspp') and p.is_file() and suffix=='.iso':
  parts=('PS3_GAME','PARAM.SFO') if emulator=='rpcs3' else ('PSP_GAME','PARAM.SFO')
  try:raw,meta=iso_sfo(p,parts)
  except ValueError:
   if emulator!='rpcs3':raise
   return native_result(emulator,archive_ps3(p,sfo))
  meta['fields']=sfo.parse(raw);return native_result(emulator,meta)
 if emulator=='rpcs3' and p.is_file() and suffix=='.squashfs':return native_result(emulator,archive_ps3(p,sfo))
 if emulator=='rpcs3' and p.is_file() and suffix=='.psn':
  text,selector=bounded_text(p);ids=[line.strip().upper() for line in text.splitlines() if line.strip()]
  if not ids or any(not re.fullmatch('[A-Z]{4}[0-9]{5}',v) for v in ids) or len(set(ids))!=1:raise ValueError('Unambiguous PSN launcher title required')
  root=safe(storage/'game'/ids[0]);report=sfo.read(safe(root/'PARAM.SFO'))
  if report['fields'].get('TITLE_ID')!=ids[0] or report['fields'].get('CATEGORY')!='HG':raise ValueError('PSN launcher and installed native base SFO disagree')
  if ident(p)!=selector['stat_identity']:raise ValueError('PSN launcher changed')
  return native_result(emulator,report,selector)
 if emulator=='rpcs3' and p.is_file() and p.name.upper()=='EBOOT.BIN' and p.parent.name.upper()=='USRDIR':
  return native_result(emulator,sfo.read(safe(p.parent.parent/'PARAM.SFO')))
 if emulator=='rpcs3' and p.is_dir() and (p/'PARAM.SFO').is_file():return native_result(emulator,sfo.read(safe(p/'PARAM.SFO')))
 if emulator=='vita3k' and p.is_file() and p.name.lower()!='param.sfo':
  # This mirrors Batocera's installed-game selector, then cross-checks the native SFO.
  ids=re.findall(r'\[([A-Z]{4}[0-9]{5})\]',p.stem)
  if len(ids)>1:raise ValueError('Ambiguous Vita launcher selector')
  if ids:
   meta=safe(storage/'ux0/app'/ids[0]/'sce_sys/param.sfo')
   if meta.is_file():
    before=ident(p);report=sfo.read(meta)
    if report['fields'].get('TITLE_ID')!=ids[0]:raise ValueError('Vita launcher selector and installed SFO disagree')
    if ident(p)!=before:raise ValueError('Vita launcher changed')
    return native_result(emulator,report,{'path':str(p),'stat_identity':before,'selection_policy':'Batocera-installed-title-selector'})
  if suffix in ('.zip','.vpk'):return native_result(emulator,zip_reader.read_zip_sfo(p))
  raise ValueError('Vita launcher needs its matching installed native app metadata')
 return None
