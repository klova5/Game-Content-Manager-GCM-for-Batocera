"""Report allocatable space for the current worker without changing reservations."""
from collections import namedtuple
import json
import os
from pathlib import Path
import re
import shutil
import sys

Usage=namedtuple('usage','total used free')

def _text(path):return Path(path).read_text()
def _stat(path):return os.stat(path)
def _unescape(value):return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),value)

def _filesystem(path):
 path=Path(path).resolve(strict=True);device=_stat(path).st_dev;choices=[]
 for order,line in enumerate(_text('/proc/self/mountinfo').splitlines()):
  left,sep,right=line.partition(' - ')
  if not sep:continue
  fields=left.split();tail=right.split()
  if len(fields)<6 or len(tail)<3:continue
  mount=Path(_unescape(fields[4]))
  if path!=mount and mount not in path.parents:continue
  if fields[2]!=str(os.major(device))+':'+str(os.minor(device)):continue
  choices.append((len(mount.parts),order,tail[0]))
 if not choices:raise ValueError('Current filesystem type unavailable')
 return max(choices)[2]

def _privileged():
 if sys.platform!='linux' or os.geteuid()!=0:return False
 # A container/user-namespace root must not be mistaken for host root.
 mapping=[tuple(map(int,line.split())) for line in _text('/proc/self/uid_map').splitlines() if line.strip()]
 if mapping!=[(0,0,4294967295)]:return False
 capabilities=re.findall(r'^CapEff:\s*([0-9a-fA-F]+)\s*$',_text('/proc/self/status'),re.M)
 return len(capabilities)==1 and bool(int(capabilities[0],16)&(1<<24))

def report(path):
 if not hasattr(os,'statvfs'):
  data=shutil.disk_usage(path)
  return {'total':data.total,'used':data.used,'free':data.free,'ordinary_available':data.free,
          'all_free':data.free,'reserved_difference':0,'policy':'PLATFORM_DEFAULT','filesystem':'unknown','effective_uid':os.geteuid() if hasattr(os,'geteuid') else None}
 s=os.statvfs(path);unit=s.f_frsize or s.f_bsize
 if unit<=0 or s.f_blocks<0 or s.f_bfree<0:raise ValueError('Invalid filesystem statistics')
 total=s.f_blocks*unit;all_free=min(s.f_bfree,s.f_blocks)*unit;ordinary=max(0,min(s.f_bavail,s.f_bfree,s.f_blocks))*unit
 filesystem='unknown';permitted=False;reason='ordinary-user availability retained'
 try:
  filesystem=_filesystem(path)
  permitted=filesystem=='ext4' and _privileged()
  if permitted:reason='host-root CAP_SYS_RESOURCE verified for ext4'
 except (OSError,ValueError,OverflowError):
  reason='privilege/filesystem evidence unavailable; ordinary-user availability retained'
 return {'total':total,'used':total-all_free,'free':all_free if permitted else ordinary,
         'ordinary_available':ordinary,'all_free':all_free,'reserved_difference':all_free-ordinary,
         'policy':'PRIVILEGED_EXT4' if permitted else 'ORDINARY_AVAILABLE',
         'filesystem':filesystem,'effective_uid':os.geteuid(),'basis':reason}

def disk_usage(path):
 data=report(path);return Usage(data['total'],data['used'],data['free'])

if __name__=='__main__':
 path=sys.argv[1] if len(sys.argv)>1 else '/userdata'
 print(json.dumps(report(path),indent=2))
