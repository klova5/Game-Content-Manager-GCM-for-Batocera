
"""Cache shared candidate metadata with current-file and proof-state validation."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {'/userdata/system/add-ons/game-content-manager/gamecontentctl-measured-v01.py': 'c9c2658d6490f78735a08dbeb42577c38fb0c7cdcbc4cc6c52677e73d2c8fca4', '/userdata/system/add-ons/game-content-manager/gcm-native-v32-measured-v01.py': 'c3af8732022a329c6399d9e791459e1382f2abc1f5668c92746669762ed8c2ff', '/userdata/system/add-ons/game-content-manager/gcm-native-v33-measured-v01.py': '42a4814d1997afda2831cd4a197e42f9fa21228e5187a24fe3b2725f56b89642', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-cnmt-parser.py': 'cf0b35eb3ffb219b881b6e298fec817aac5d1b3ba06314f12590fe4cadfd7b15', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-config-preview.py': '48387f6f5157aaf22f9763813006fce44dbad8828da6c7c78c4351f851fc41a3', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-dlc-inventory-model.py': '914ac9ee5e12b5ea0103af1a160dfee92f6b1727d58f24e92added5720f4e78b', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-dlc-toggle.py': '28d91a9d2e24faecd16bdf6a3e72c212b16be3c27cf08196c244e7f751efcadf', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-install-provider-v02.py': '6ff3c49b9ffb3196f0da58697f90e26628d17e48c59a4467d3d4e561871d8ae4', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-install-provider-v03.py': 'dc5735103bca8678ec640958ba29501e4dc78b71f230fa943e50d757256f7360', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-managed-copy-v02.py': 'bd30b8d534b2777e68d7adf65d247e0a828a05c99c649329f0535a124fe7d114', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nand-verify-v02.py': '35ca963cd6326d486de4e610773c080892c8246e3836f84d0c849457b6abab60', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nand-verify.py': 'a7e809d029dccb59a0385a4a9484ffd623800dd5cea3d1ccd11b3dac681bc611', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-native-protocol.py': 'a2d71559c17f516187184d9891c4c63829a7e93a7ab462c214ad8085a39bd054', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-native-query-v02.py': '9f65d40421e7697ddd60ecf9d7c57cfedf867d527ee6ebd94d4a75624bb0a225', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-nsp-identity.py': '4405e100d73dc645eaa324cbcc1158d25707944f86646cc070fbcd6eff212af8', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify-v02.py': '24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify-v03.py': '9244d98550821644d2e54fe933010b478e3646964e12ac10233080578074a388', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-package-verify.py': 'b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-publish-managed-update.py': 'e02f596a40437020bdcfee4b4ec3ebfd76421685ca977b95b9ad5463d924dcf1', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-coverage-v02.py': '83d53e303c1c4234c2bac4ea03361e505eae17fcd6a48f8b79feeec72b89cfe3', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-coverage-v03.py': '6fc7337e87364ee5d808d3747b4033509e261dd7c54baed883103c1b04fcbb9e', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-effective-selection-v02.py': 'c69272852f6ecf84a7bf0cdc9f40a60082eb5980d1917544c85a9c5496440b5d', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-inventory-snapshot-v03.py': 'f20025169c3291677c0be5e9c4b7c9c4643f45f41594b39db7834320b61d0428', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-registration-transaction-v03.py': 'a037e70af2a3faf45f12c353c50eaa2805826fc69ab2301349be309581bb8285', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-select-v03.py': '2b5ba9e79d7e212be64606c2fb2a03a8743cba09de5dc82fd3aeca0a0342408e', '/userdata/system/gcm-native-test/step59-citron-backend-v01/citron-update-selection-plan.py': '13acbd30301f0c48dc1c8b9de5958d55087e739981017f53a508fe7afa07f86b', '/userdata/system/gcm-native-test/step60-shared-switch/citron-configuration-transaction-measured-v01.py': '57fdca525e8ef8b5bb8a051050d3ac48cd8189dfb3f4114c43ceb6ba9ef05bdd', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-catalog.py': '6dec4983cb4c7f6707d4604db6f11c5cca01d0d1a9e2f20169e5df84d1e79cfa', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-install-job-measured-v01-space-v01.py': 'bd8c97b7de85be2d7c847289b6fdeb2416bdd391744ba4153d7bdb53f9bff0ac', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-plan.py': 'aae0d5039b57b6a497d4415a0268bcb69195fe218e7adef52ad6e7f194be6b56', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-publication.py': 'b850734f6f1ed6df2fd361779e253ab4374e97da96149ddbd5e6a409af1abe37', '/userdata/system/gcm-native-test/step60-shared-switch/citron-content-registration.py': 'b73d2bf2d56ac40cbdb00e8215e12854f7b8d58c7950c7092f8e74051347a27a', '/userdata/system/gcm-native-test/step60-shared-switch/citron-global-inventory-measured-v01.py': '803e7f6afc1eec4d969a1e8f9454f8883c9b84866d51a2d555ce3fb99882bede', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-provider-v02-measured-v01-space-v01.py': '2596aa79c463d604b685073a99204e629900b4e3c5d613d649415cab925837f0', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-provider-v03-measured-v01-space-v01.py': '33fd95fe187765e793ae2e69997bbceaa6e6752dd77c63dadb31f2f796c83d9d', '/userdata/system/gcm-native-test/step60-shared-switch/citron-install-worker-provider-measured-v01-space-v01.py': '2e9693ce708df11298b3a33fbf67d7ecabba197a06ef5593acdf162f3f04f1e0', '/userdata/system/gcm-native-test/step60-shared-switch/citron-installed-integrity.py': '987136c7420b9b27e9e15c9f6c00e45d4e30a17f9a4cab7113de51951a8cfebc', '/userdata/system/gcm-native-test/step60-shared-switch/citron-installed-rows-measured-v01.py': 'c6e0536e81ae6841afbe205301f3a212963af76cd4da12fe3a8c29f3190d846e', '/userdata/system/gcm-native-test/step60-shared-switch/citron-registration-plan.py': 'c78ccd7a9a76f448fc33085aa44374ddef1b1e7d2bba9532094512b901763024', '/userdata/system/gcm-native-test/step60-shared-switch/citron-update-resolution.py': 'ddc841e0c20c4a8d6e902d715db3c1221fa7cdfb05eb772263a3dd39f7375853', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-base-identity-measured-v01.py': '99f68705f60da1217d88bb5253cd2e25f31be49fff59f30d6b3e6bd809a5579a', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-content-engine-measured-v01.py': '7120a24469daa2e31316235a79989060777f92701628ce3711ae2b67d9b262c4', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-index-candidates-measured-v01.py': '99a1a5222c1dac8e21982d0fee7491ef21696da16fc89127429494e3c3bb1a47', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-managed-copy-measured-v01-space-v01.py': '19fa1f5ab2fa2dd06dce95250095994c7d9819e23802d97f5df6c15a0b0e001e', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-batch-measured-v01.py': 'da99ce55cd55be06b209b9a7f6faf3e53c8ae829547b85147c2e3dd88e4c76b4', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verification-store.py': '07b5e7d7481627f4b4b93ac92a6944cb9bba826cc315f6849dea47b49dcf8195', '/userdata/system/gcm-native-test/step60-shared-switch/gcm-switch-verified-candidates-measured-v01.py': 'c086ea55dbe2bbd16310d2e81a7b64ca69396b4160ebc8252059935cab899b26', '/userdata/system/gcm-native-test/v34-development/gcm-native-v34-measured-v01.py': 'a271669e29799e8b8816983dfef1d41b4e705d7bbd8ed19ac2301eec34589fca', '/userdata/system/gcm-native-test/v34-development/gcm-native-v35-measured-v01.py': '7d63b62932c6449e316d1c6caf9538c9914169b0f783ea36465e27c7b95f7c65', '/userdata/system/gcm-native-test/v34-development/gcm-v34-full-scan-measured-v01.py': '61bc39e8708e6a85c328ce8f2c0049fe647f1c751cc07f49976175afc3de44a8', '/userdata/system/gcm-native-test/v34-development/gcm-v34-update-import-measured-v01-space-v01.py': 'ca60a3dc69fb30b5ab7002fd0e7bc04293234771c71bbee68f0128d7d35b298c', '/userdata/system/gcm-native-test/v34-development/gcm-v34-update-migrate.py': 'e653b16a3d8318b8c09a2f93ddf84a8aca882f29f3ed7bd35f7562a6fcc26468', '/userdata/system/gcm-native-test/v34-development/gcm-v35-auto-preview-measured-v01.py': '39f1a368e0ecbb5696f5d5eaad6859f29ce474406855d4e5a4bd94e7a99f9795', '/userdata/system/gcm-native-test/v34-development/gcm-v35-background-measured-v01.py': '9b5fa595036f6dc2eef3065a738d645dd1d4f4336b0273800c1231e2c31cb1b9', '/userdata/system/gcm-native-test/v34-development/gcm-v35-guarded-preview-measured-v01.py': '27cc7e5f5e248ddba89a8f96b8a8aafef416c22f4ef665a372200b47e9c3f979', '/userdata/system/gcm-native-test/v34-development/gcm-v35-internal-integrity-measured-v01.py': '5815ff8c65271ada574829146f39ec64593c29d8a7047e5285afa3e567ee4f05', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-backend-measured-v01.py': '8ce2bbce6d585cce8d29cc7a4907a070ac93e69b49110c826bcb0b3b7afed268', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-nsp-identity-measured-v01.py': 'd480e9ac718ab634ef1335f208f0cc885038229d7f702540ec62eedb8cf8f666', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-install-measured-v01-space-v01.py': '5c4dafd5826105920f17f0b5a90b0aefdbee66157ea934f18c25f26b47e0ae0a', '/userdata/system/gcm-native-test/v34-development/v35-native-development/gcm-v35-verified-inventory-measured-v01.py': '0b6cb244e3fb6841eb5a04e6facd1ccbcf8600a64448f3305a81e0d36bb450c0'}

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
        "v35-native-development/gcm-v35-backend-measured-v01.py")
    backend = load(path,
        "8ce2bbce6d585cce8d29cc7a4907a070ac93e69b49110c826bcb0b3b7afed268")
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

    shared = load(ROOT / "gcm-switch-verified-candidates-measured-v01.py",
        "c086ea55dbe2bbd16310d2e81a7b64ca69396b4160ebc8252059935cab899b26")
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
