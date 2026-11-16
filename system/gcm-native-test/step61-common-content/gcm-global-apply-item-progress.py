"""Per-item Apply preparation telemetry; never grants transaction success."""
import hashlib,importlib.util,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def progress():
    path=ROOT/'gcm-global-action-progress-measured-v01.py'
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='d51d7b1e5dbe6a07e259c356da4f61a60b0fb25ae9c7d608735d121712882c2e':
        raise ValueError('Shared measured progress dependency changed')
    spec=importlib.util.spec_from_file_location('item_progress_owner',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def mark_ready(token):
    module=progress();path=module.owner_location()
    if path is None:return
    state=json.loads(module.safe(path.parent/'state.json').read_text())
    allowed={item['token'] for item in state.get('items',[])}
    if state.get('operation')!='apply' or token not in allowed:raise ValueError('Apply item does not belong to this job')
    def change(owner):
        ready=set(owner.get('ready_item_tokens',[]))
        if not ready.issubset(allowed):raise ValueError('Foreign completed Apply item')
        ready.add(token);owner['ready_item_tokens']=sorted(ready)
    # Uses the original live owner, process-group delegation, boot and item checks.
    module.update_owner(change)

def ready(job):
    if job.get('operation')!='apply' or job.get('status')!='RUNNING':return set()
    module=progress();path=module.safe(module.JOBS/job['job_id']/'progress-owner.json')
    if not path.exists():return set()
    owner=json.loads(path.read_text());allowed={item['token'] for item in job.get('items',[])}
    values=owner.get('ready_item_tokens',[])
    if (owner.get('job_id')!=job['job_id'] or owner.get('boot_id')!=job['boot_id']
        or owner.get('root_identity')!=module.process_identity(job['pid'])
        or not isinstance(values,list) or len(values)!=len(set(values))
        or not set(values).issubset(allowed)):
        raise ValueError('Apply item telemetry differs from the running job')
    return set(values)
