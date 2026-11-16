"""One global update-uninstall API; stable PS4/Switch and native routes retained."""
import hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def workflow():
 path=ROOT/'gcm-global-update-workflow-v01.py';manifest=ROOT/'manifest.json'
 if any(p.is_symlink() for p in (path,*path.parents,manifest)):raise ValueError('Unsafe shared update provider path')
 expected=json.loads(manifest.read_text())['sha256'][path.name]
 if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Shared update workflow changed')
 spec=importlib.util.spec_from_file_location('gcm_workflow',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
if __name__=='__main__':
 try:workflow().provider().main()
 except (OSError,ValueError,KeyError) as exc:print('ERROR\t'+str(exc));raise SystemExit(1)
