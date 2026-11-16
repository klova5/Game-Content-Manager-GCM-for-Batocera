"""Shared existing-content GUI extension; original inventory routes retained."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
PRIOR='gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py'
PINS={'gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py': '16cd5f04492150c36bd08ac33c572783df747b661424a78465153c94f9245981', 'gcm-switch-install-locally-v01.py': '7ba756e389403b0bc5d12285afb0dedd311cc652edbccf8f740d53c483345471'}

_GCM_DECLARED_CODE_NAMES = {'gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01-space-v01.py': 'gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py', 'gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01.py': 'gcm-global-menu-existing-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py'}

def pinned(name):
 name = _GCM_DECLARED_CODE_NAMES.get(name, name)
 p=ROOT/name
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Shared menu extension dependency changed: '+name)
 return p

def load(name):
 p=pinned(name);spec=importlib.util.spec_from_file_location('menu_extension_'+p.stem,p)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def extend(output,doc,provider,kind):
 result=output.splitlines();seen={s.split('\t')[4] for s in result if s.startswith('ROW\t') and len(s.split('\t'))==5}
 for row in doc['rows']:
  if row['content_type']!=kind or row['token'] in seen:continue
  ready=provider.local_copy(row,doc) is not None
  state='EXISTING_LOCAL' if ready else 'EXISTING_EXTERNAL'
  label=('Update '+str(row['version'])) if kind=='update' else ('DLC '+str(row['content_id']))
  label+=' (EXISTING)';label=''.join(c if ord(c)>=32 and ord(c)!=127 else ' ' for c in label)[:150]
  result.append('\t'.join(('ROW','AVAILABLE',state,label,row['token'])));seen.add(row['token'])
 return '\n'.join(result)+'\n'

def main():
 p=argparse.ArgumentParser(add_help=False);p.add_argument('--mode');p.add_argument('--emulator');p.add_argument('--rom');p.add_argument('--type',default='update')
 a,_=p.parse_known_args()
 result=subprocess.run([sys.executable,'-B',str(pinned(PRIOR)),*sys.argv[1:]],capture_output=True,text=True)
 output=result.stdout
 if result.returncode==0 and a.mode=='list' and a.emulator in ('ryujinx','ryujinx-emu','citron','citron-emu'):
  try:
   provider=load('gcm-switch-install-locally-v01.py');doc=provider.inventory(a.rom,a.emulator)
   output=extend(output,doc,provider,a.type)
  except (OSError,ValueError,KeyError) as exc:
   output=output.rstrip('\n')+'\nSTATUS\tEXISTING_CONTENT_UNAVAILABLE\t'+str(exc).replace('\t',' ').replace('\n',' ')[:300]+'\n'
 sys.stdout.write(output);sys.stderr.write(result.stderr);return result.returncode

if __name__=='__main__':
 try:raise SystemExit(main())
 except Exception as exc:print('ERROR\t'+str(exc).replace('\n',' '));raise SystemExit(1)
