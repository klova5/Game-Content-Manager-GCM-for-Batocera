"""Shared DLC uninstall dispatcher for connected emulator adapters."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
PINS={'gcm-ps4-dlc-uninstall-v01.py': 'e8419c5ef1276d9bc419e7b47a88f407d0b50e4b7904430029669b2a2818e2f3', 'gcm-switch-dlc-uninstall-v01.py': '2e2f23c09029e88ee9d35f8823cfc5cfcb7bf880b3ca1ad5987b24eceaa63066'}

def load(name):
 p=ROOT/name
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Uninstall provider changed: '+name)
 spec=importlib.util.spec_from_file_location('uninstall_'+p.stem,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def provider(emulator):
 if emulator=='shadps4':return load('gcm-ps4-dlc-uninstall-v01.py')
 if emulator in ('ryujinx','ryujinx-emu','citron','citron-emu'):return load('gcm-switch-dlc-uninstall-v01.py').provider(emulator)
 raise ValueError('Uninstall adapter not connected: '+emulator)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=('capabilities','preview','uninstall'))
 p.add_argument('--rom');p.add_argument('--emulator',required=True);p.add_argument('--token',action='append',default=[])
 p.add_argument('--all',action='store_true');p.add_argument('--confirm',action='store_true');a=p.parse_args()
 if a.action=='capabilities':
  print('CAPABILITY\tUNINSTALL_DLC\t'+str(int(a.emulator in ('shadps4','ryujinx','ryujinx-emu','citron','citron-emu'))));return
 if not a.rom or (a.all and a.token):raise ValueError('Explicit game and one selection method required')
 m=provider(a.emulator);tokens=a.token
 if a.all:
  if a.emulator=='shadps4':tokens=[r['token'] for r in m.load('gcm-dlc-uninstall-plan-v01.py').preview(a.rom,a.emulator,[])['rows']]
  else:tokens=[r['token'] for r in m.plan(a.rom,None)['rows']]
 if a.action=='uninstall':m.execute(a.rom,tokens,a.confirm);return
 result=m.plan(a.rom,tokens);print('GAME:',result['application_id'])
 for row in result['rows']:print('UNINSTALL:',row['label'],'PRESERVE:',row['preserved_package'])
 print('ITEMS:',len(result['rows']));print('Preview only; no content changed')

if __name__=='__main__':
 try:main()
 except Exception as exc:print('ERROR\t'+str(exc).replace('\n',' '));raise SystemExit(1)
