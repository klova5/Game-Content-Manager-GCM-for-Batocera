"""Shared Install Locally dispatch; adapters own existing-content bindings."""
import hashlib
import importlib.util
from pathlib import Path
ROOT=Path('/userdata/system/gcm-native-test/step61-common-content')
PINS={'gcm-global-install-locally-v01.py': '58043a4915c5c0890bdd061935bad7d6e2c4070f27e65a86e9b6b93f029eb2b2', 'gcm-switch-install-locally-v01.py': '7ba756e389403b0bc5d12285afb0dedd311cc652edbccf8f740d53c483345471'}

def load(name):
 p=ROOT/name
 if any(x.is_symlink() for x in (p,*p.parents)) or hashlib.sha256(p.read_bytes()).hexdigest()!=PINS[name]:
  raise ValueError('Install Locally provider changed: '+name)
 spec=importlib.util.spec_from_file_location('local_provider_'+p.stem,p)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def provider(emulator):
 if emulator=='shadps4':return load('gcm-global-install-locally-v01.py').provider(emulator)
 if emulator in ('ryujinx','ryujinx-emu','citron','citron-emu'):
  return load('gcm-switch-install-locally-v01.py').provider(emulator)
 raise ValueError('Install Locally adapter not connected: '+emulator)
