
"""Cache shared candidate metadata with current-file and proof-state validation."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {'/userdata/system/add-ons/game-content-manager/gamecontentctl.py': 'f649b3346f19601e4bc498111dffd249b5e7c37a520b1fdc4af1de584361a03f', '/userdata/system/add-ons/game-content-manager/gcm-native-v32.py': 'f6a573a8af3e412f41c3319c1b83288f62b6ff570cfc6d924ae5b22b35a83a2f', '/userdata/system/add-ons/game-content-manager/gcm-native-v33.py': '1d5926f632acc45ff50d5cb0b3656656b92e65fd48ac43e9198313a6361e9f08', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-cnmt-parser.py': 'cf0b35eb3ffb219b881b6e298fec817aac5d1b3ba06314f12590fe4cadfd7b15', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-config-preview.py': '48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-dlc-inventory-model.py': '914ac9ee5e12b5ea0103af1a160dfee92f6b1727d58f24e92added5720f4e78b', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-dlc-toggle.py': '28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-install-provider-v02.py': '6ff3c49b9ffb3196f0da58697f90e26628d17e48c59a4467d3d4e561871d8ae4', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-install-provider-v03.py': 'dc5735103bca8678ec640958ba29501e4dc78b71f230fa943e50d757256f7360', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-managed-copy-v02.py': 'bd30b8d534b2777e68d7adf65d247e0a828a05c99c649329f0535a124fe7d114', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nand-verify-v02.py': '35ca963cd6326d486de4e610773c080892c8246e3836f84d0c849457b6abab60', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nand-verify.py': 'a7e809d029dccb59a0385a4a9484ffd623800dd5cea3d1ccd11b3dac681bc611', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-native-protocol.py': 'a2d71559c17f516187184d9891c4c63829a7e93a7ab462c214ad8085a39bd054', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-native-query-v02.py': '9f65d40421e7697ddd60ecf9d7c57cfedf867d527ee6ebd94d4a75624bb0a225', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nsp-identity.py': '4405e100d73dc645eaa324cbcc1158d25707944f86646cc070fbcd6eff212af8', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify-v02.py': '24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify-v03.py': '9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify.py': 'b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-publish-managed-update.py': 'e02f596a40437020bdcfee4b4ec3ebfd76421685ca977b95b9ad5463d924dcf1', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-coverage-v02.py': '83d53e303c1c4234c2bac4ea03361e505eae17fcd6a48f8b79feeec72b89cfe3', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-coverage-v03.py': '6fc7337e87364ee5d808d3747b4033509e261dd7c54baed883103c1b04fcbb9e', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-effective-selection-v02.py': 'c69272852f6ecf84a7bf0cdc9f40a60082eb5980d1917544c85a9c5496440b5d', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-inventory-snapshot-v03.py': 'f20025169c3291677c0be5e9c4b7c9c4643f45f41594b39db7834320b61d0428', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-registration-transaction-v03.py': 'a037e70af2a3faf45f12c353c50eaa2805826fc69ab2301349be309581bb8285', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-select-v03.py': '2b5ba9e79d7e212be64606c2fb2a03a8743cba09de5dc82fd3aeca0a0342408e', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-selection-plan.py': '13acbd30301f0c48dc1c8b9de5958d55087e739981017f53a508fe7afa07f86b', '/userdata/system/gcm-native-test/step60-shared-switch/citron-configuration-transaction.py': '8db23bcb1f95095502310fa110793601c742481fa837613524bdba778809e5f6', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-catalog.py': '6dec4983cb4c7f6707d4604db6f11c5cca01d0d1a9e2f20169e5df84d1e79cfa', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-install-job.py': 'e2c518eae8bb75551b34b259577e0f2ba926b58161a88009a086df034bb5c5a7', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-plan.py': 'aae0d5039b57b6a497d4415a0268bcb69195fe218e7adef52ad6e7f194be6b56', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-publication.py': 'b850734f6f1ed6df2fd361779e253ab4374e97da96149ddbd5e6a409af1abe37', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-registration.py': 'b73d2bf2d56ac40cbdb00e8215e12854f7b8d58c7950c7092f8e74051347a27a', '/userdata/system/gcm-native-test/step60-shared-switch/citron-global-inventory.py': '838df22ab83fe52cef2e04909f7e04c2971e9df02ed845ffd5865e7690838e6e', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-provider-v02.py': '2f2232d8032c96e5f02f0c809363789107e9034ef65b7e267f84dedc873f72f9', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-provider-v03.py': 'fa90fe48337f9d2be401bdc03cf05fcf95174cd4bd8ab1deeca773c1e9f94500', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-worker-provider.py': '79d3ac22e3209d8ec3557a5ccbee0202f540e80280789b3fc2109258a0aaf946', '/userdata/system/gcm-native-test/step60-shared-switch/citron-installed-integrity.py': '987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc', '/userdata/system/gcm-native-test/step60-shared-switch/citron-installed-rows.py': '8be6c58ecceaef24c6f96c3ece1e5251112c598a7a1a7d05d3d8aea89fd3b42b', '/userdata/system/gcm-native-test/step60-shared-switch/citron-registration-plan.py': 'c78ccd7a9a76f448fc33085aa44374ddef1b1e7d2bba9532094512b901763024', '/userdata/system/gcm-native-test/step60-shared-switch/citron-update-resolution.py': 'ddc841e0c20c4a8d6e902d715db3c1221fa7cdfb05eb772263a3dd39f7375853', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-base-identity.py': '4a4d0be60991eeb9ae35e2479d563c5aec5c18aae4d6648f7f7ad4aad75bf3f1', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-content-engine.py': '3e14cc15a650e1d1e92f39ca687ccd681242ad7f4eb7f0310acdb9b1b4e35c02', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-index-candidates.py': '5803ddbdf1b605b9943456c3dbe71db6352c839d1682a025a701175d43de669a', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-managed-copy.py': 'e6c9140a8086cb30202d831630d0b2219690ffd724735376c333f6b2ac544432', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-batch.py': 'ccec67b43d67778ed74722954bd2382be9655a0c2d172c9c96796fe00c350215', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-store.py': '07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verified-candidates.py': '12a739348834af57fb5b9494a7ea7f23da478babbb143e5597524d564f9d7fae', '/userdata/system/gcm-native-test/v34-development/gcm-native-v34.py': '81a5d5e1766132a06eda8d5a2b90521d47213db41f0b84408f4ce1fd6cbea1ad', '/userdata/system/gcm-native-test/v34-development/gcm-native-v35.py': 'c7849802eda48ead003c8f2db60d2461a678ce36bf45d51e2e2673c387a7ef47', '/userdata/system/gcm-native-test/v34-development/gcm-v34-full-scan.py': '4bc842b121751e78b169b400a54b148371ba18e1b6c482b75dc69beb77e289db', '/userdata/system/gcm-native-test/v34-development/gcm-v34-update-import.py': 'd7b064e8809ad352cd2785c7134973e9db1c7fbf6ed74167c7a86a163ed56009', '/userdata/system/gcm-native-test/v34-development/gcm-v34-update-migrate.py': 'e653b16a3d8318b8c09a2f93ddf84a8aca882f29f3ed7bd35f7562a6fcc26468', '/userdata/system/gcm-native-test/v34-development/gcm-v35-auto-preview.py': '2f372c00799df5ce3531d237e3e634e86b0590860ff9b8b1c190cd1617e525d5', '/userdata/system/gcm-native-test/v34-development/gcm-v35-background.py': '7a9efbec42b6a9aa341b071f1ca64580c41d714c9b55849a84633a6ebd75e2b1', '/userdata/system/gcm-native-test/v34-development/gcm-v35-guarded-preview.py': '0fac479891f22558364124033250f6ec4dc77262339eb0c94a4ffaf3dad4df52', '/userdata/system/gcm-native-test/v34-development/gcm-v35-internal-integrity.py': 'dd85cbb625fec9f95c0f8ad6d68f8a87359bc5044177a488e77ad5379b612cf5', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-backend.py': '7f1ce0ac49c07a081f03c45b1dd06088a67e540cc7ef92818223907d4e65ec71', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-nsp-identity.py': '6da1ec6323b28980add834e4abc1a7f21fc9baf19770f44c2568674be96bf7cc', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-install.py': '19e01da73ec4cb15e08aa4f38fa855314822f2fb32ed0148cd23c435c46f1c3a', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-inventory.py': '8f3801ae213efbc9e5eda20a7a0ee85613e6b81aced9b6f56b5074d2f59fe1ca'}

def load(path, expected):
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Candidate cache dependency changed: " + path.name)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identity(path):
    path = Path(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return [os.path.realpath(path), st.st_dev, st.st_ino, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns]

def fingerprint(rom):
    for name, expected in PINS.items():
        path = Path(name)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Shared code changed: " + path.name)
    path = Path(
        "/userdata/system/gcm-native-test/v34-development/"
        "v35-native-development/gcm-v35-backend.py")
    backend = load(path,
        "7f1ce0ac49c07a081f03c45b1dd06088a67e540cc7ef92818223907d4e65ec71")
    database = Path(backend.DB_PATH).resolve(strict=True)
    backend.db = lambda: sqlite3.connect(
        database.as_uri() + "?mode=ro", uri=True)
    indexed = backend.list_packages(system="switch")
    indexed.sort(key=lambda item: item["path"])
    files = {
        item["path"]: identity(item["path"])
        for item in indexed if Path(item["path"]).suffix.lower() == ".nsp"
    }
    proof_store = ROOT / "state/verified-content.json"
    if proof_store.is_symlink():
        raise ValueError("Unsafe shared proof store")
    proof_digest = (
        hashlib.sha256(proof_store.read_bytes()).hexdigest()
        if proof_store.exists() else None)
    value = {
        "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "rom": identity(rom),
        "database": str(database),
        "indexed": indexed,
        "files": files,
        "proof_store": proof_digest,
        "cache_code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

def candidates(rom, content_type):
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    rom = str(Path(rom).resolve(strict=True))
    folder = ROOT / "state/candidate-query-cache"
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ValueError("Unsafe candidate cache directory")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = hashlib.sha256((rom + "\0" + content_type).encode()).hexdigest()
    path = folder / (key + ".json")
    if path.is_symlink():
        raise ValueError("Unsafe candidate cache file")
    before = fingerprint(rom)
    try:
        saved = json.loads(path.read_text())
    except (FileNotFoundError, ValueError, UnicodeError):
        saved = None
    if (isinstance(saved, dict)
        and saved.get("schema") == "GCM_SHARED_CANDIDATE_CACHE_V1"
        and saved.get("fingerprint") == before
        and saved.get("rom") == rom
        and saved.get("content_type") == content_type
        and isinstance(saved.get("result"), dict)):
        if fingerprint(rom) != before:
            raise ValueError("Candidate inputs changed during cache validation")
        return saved["result"]

    shared = load(ROOT / "gcm-switch-verified-candidates.py",
        "12a739348834af57fb5b9494a7ea7f23da478babbb143e5597524d564f9d7fae")
    result = shared.candidates(rom, content_type)
    if fingerprint(rom) != before:
        raise ValueError("Candidate inputs changed during discovery")
    saved = {
        "schema": "GCM_SHARED_CANDIDATE_CACHE_V1",
        "fingerprint": before, "rom": rom,
        "content_type": content_type, "result": result,
    }
    fd, temporary = tempfile.mkstemp(prefix=".candidates-", dir=folder)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(saved, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return result
