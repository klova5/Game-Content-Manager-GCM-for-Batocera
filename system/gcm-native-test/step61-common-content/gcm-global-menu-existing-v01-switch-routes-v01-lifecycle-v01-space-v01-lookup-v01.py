"""Extend the established shared inventory with existing emulator installations."""
import hashlib
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PINS={'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py': '77b5e60766957cb015ea2b09e619a8b9f443fdcb54c62a3ca6806b0309c15ce5', 'gcm-global-install-locally-v01.py': '58043a4915c5c0890bdd061935bad7d6e2c4070f27e65a86e9b6b93f029eb2b2'}

_GCM_DECLARED_CODE_NAMES = {'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01.py': 'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py', 'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01.py': 'gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py'}

def load(name):
    name = _GCM_DECLARED_CODE_NAMES.get(name, name)
    path=ROOT/name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=PINS[name]:raise ValueError('Existing-content menu dependency changed: '+name)
    spec=importlib.util.spec_from_file_location(name.replace('.','_'),path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def extra_rows(rom,kind):
    provider=load('gcm-global-install-locally-v01.py');doc=provider.inventory(rom,'shadps4');output=[]
    for row in doc['rows']:
        if row['content_type']!=kind or row.get('managed_status')=='MANAGED_FILES_VERIFIED':continue
        token='existing:'+row['source_root'].encode().hex()
        ready=provider.local_copy(row,doc) is not None
        state='EXISTING_LOCAL' if ready else 'EXISTING_EXTERNAL' if row['install_locally_available'] else 'EXISTING_UNASSESSED'
        label=row['content_id'] if kind=='dlc' else 'Update '+str(row['version'])
        label+=' (EXISTING)'
        output.append('\t'.join(('ROW','AVAILABLE',state,label,token)))
    return output

def install_extension(module):
    original=module.inventory
    def inventory(rom,kind,assess=False):
        rows=original(rom,kind,assess)
        if assess:return rows
        try:extra=extra_rows(rom,kind)
        except (OSError,ValueError,KeyError) as exc:
            # A new adapter must not take the established menu offline.
            return rows+['STATUS\tEXISTING_CONTENT_UNAVAILABLE\t'+str(exc).replace('\n',' ').replace('\t',' ')[:300]]
        seen={r.split('\t')[4] for r in rows if r.startswith('ROW\t')}
        return rows+[r for r in extra if r.split('\t')[4] not in seen]
    module.inventory=inventory
    return module

def main():
    module=load('gcm-global-menu-unified-v02-prepared-action-v01-file-reuse-v01-item-progress-v01-switch-routes-v01-lifecycle-v01-space-v01-lookup-v01.py')
    install_extension(module).main()

if __name__=='__main__':
    try:main()
    except Exception as exc:print('ERROR\t'+str(exc).replace('\n',' '));raise SystemExit(1)
