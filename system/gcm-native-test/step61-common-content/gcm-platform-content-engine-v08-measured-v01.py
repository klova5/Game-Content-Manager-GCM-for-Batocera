"""Common platform content metadata API; verification and installation are separate."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PINS = {
    "gcm-batch-content-discovery-v02-measured-v01.py":
        "501003eb7893e1ba11c12d96b5ac54c19043b62decf9cc1cf5ff850e64ab014e",
    "gcm-content-container-probe.py":
        "6982245457cf40c45ba9199aaa3e7001dfa39a704b3371dd9e930ce044de5f21",
    "gcm-sony-package-metadata-v02.py":
        "0238e5f5072ce329d049dda515be94420a3c1d23b8790d5644801bcbde43ef1c",
    "gcm-wad-metadata-v02.py":
        "4f6fb8cb2d3ef6a9fa9cd7821732118a8c65196199b4b88d4cd75429fa413d1e",
    "gcm-xml-archive-metadata.py":
        "9bf2c8eaf948d0b646ead790f429efa5faf279577f737e868415c417543790e2",
}
PINS.update({
    "gcm-vita-archive-metadata.py":
        "94d768d67490a36b757dbf2abbbd3f95caa7ea29b66c40a90e2d4acb73f38011",
    "gcm-wiiu-title-metadata-v02.py": "c25b772bd748f31b4cfd31dcd5f907b0a97ce0f403cb2e051bd02f7c26b94a2f",
    "gcm-cia-payload-verify-v02-measured-v01.py": "8d29d3b841a70e28cdbadbc3b7c883964e9afa9d0f922f8b7c6666a83e0fbb45",
    "gcm-cia-metadata-v02.py":
        "0a6f9554f3a78730b314b5dd5fc87087b74f07c02a43c71d8a1e1575ae4068ab",
    "gcm-xbox-content-metadata.py":
        "c0fcfe71406a65d0037b74b633cd6f879c9ee432b272d27885d617749459ecde",
    "gcm-ps3-container-check.py":
        "5006cf34b54d96b9171e2c679d3e04f79da1871f08708236704b58e944c9c38b",
    "gcm-ps4-payload-verify-v02-measured-v01.py":
        "201fee84c26bcd327bcdea6af2e38899e6733dda840918525dd593e578a3a680",
    "gcm-wii-payload-verify-v02-measured-v01.py":
        "c56d4ecbd5322c3d9737e3042dac9405dbaff8110a9ae94d3f61f957013e6035",
})
SYSTEMS = ("ps3", "ps4", "xbox360", "wiiu", "3ds", "psvita", "wii")

def load(name):
    path = ROOT / name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != PINS[name]:
        raise ValueError("Content dependency changed: " + name)
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def check_dependencies():
    for name in PINS:
        load(name)
    return {"status": "PASS", "systems": list(SYSTEMS), "game_allowlist": False}

def discover(systems=None):
    return load("gcm-batch-content-discovery-v02-measured-v01.py").discover(systems)

def inspect(path, system):
    if system not in SYSTEMS:
        raise ValueError("Unknown platform scope")
    path = Path(path)
    probe = load("gcm-content-container-probe.py").probe(path)
    hint = probe["container_hint"]
    if system == "ps4" and hint == "PS4_PKG_HEADER":
        report = load("gcm-sony-package-metadata-v02.py").read(path)
        title = report["title_id"]
        kind = report["content_type"]
        version = report["fields"].get("APP_VER", "")
        platform_identified = True
    elif system in ("ps3", "psvita") and hint == "SONY_PKG_HEADER":
        report = load("gcm-sony-package-metadata-v02.py").read(path)
        fields = report["fields"]
        if system == "ps3":
            platform_identified = fields.get("PKG_PLATFORM") == 1
        else:
            platform_identified = (
                fields.get("PKG_PLATFORM") == 2
                and fields.get("PKG_CONTENT_TYPE") in (0x15, 0x16)
            )
        if not platform_identified:
            raise ValueError("Internal Sony platform/type does not match scope")
        title, kind, version = report["title_id"], report["content_type"], ""
    elif system == "wii" and hint == "WII_WAD_HEADER":
        report = load("gcm-wad-metadata-v02.py").read(
            path, probe["stat_identity"])
        title, kind = report["title_id"], report["content_type"]
        version = report["internal_version"]
        platform_identified = True
    elif system == "3ds":
        report = load("gcm-cia-metadata-v02.py").read(path)
        title, kind = report["title_id"], report["content_type"]
        version = report["internal_version"]
        platform_identified = True
    elif system == "xbox360":
        report = load("gcm-xbox-content-metadata.py").read(path)
        title, kind = report["title_id"], report["content_type"]
        version = report["internal_version"]
        platform_identified = True
    elif system == "wiiu" and path.name.lower() == "meta.xml":
        report = load("gcm-wiiu-title-metadata-v02.py").read(path)
        title, kind = report["title_id"], report["content_type"]
        version = report["internal_version"]
        platform_identified = True
    elif system == "psvita" and hint == "ZIP_CONTAINER_HEADER":
        report = load("gcm-vita-archive-metadata.py").read(path)
        title, kind = report["title_id"], report["content_type"]
        version = report["version"]
        platform_identified = False
    else:
        raise ValueError("Internal metadata reader not yet available for this platform/container")
    if report["stat_identity"] != probe["stat_identity"]:
        raise ValueError("Candidate changed between probe and metadata reading")
    return {
        "schema": "GCM_PLATFORM_CONTENT_METADATA_V1",
        "system_scope": system, "platform_identified": platform_identified,
        "title_id": title, "content_type": kind, "version": version,
        "path": report["path"], "stat_identity": report["stat_identity"],
        "evidence": report,
        "state": "DETECTED",
        "game_relationship_verified": False,
        "package_integrity_verified": False,
        "installation_authorized": False,
    }


def verify_content(path, system):
    adapters = {
        "3ds": "gcm-cia-payload-verify-v02-measured-v01.py",
        "ps4": "gcm-ps4-payload-verify-v02-measured-v01.py",
        "wii": "gcm-wii-payload-verify-v02-measured-v01.py",
    }
    if system not in adapters:
        raise ValueError("Payload verification adapter not yet connected for this platform")
    metadata = inspect(path, system)
    if metadata["content_type"] not in ("update", "dlc"):
        raise ValueError("Update or DLC content required")
    if metadata["platform_identified"] is not True:
        raise ValueError("Internal platform identity required")
    proof = load(adapters[system]).verify(path)
    version = proof.get("version", proof.get("internal_version"))
    if (proof.get("status") != "PAYLOAD_HASHES_VERIFIED"
        or proof.get("title_id") != metadata["title_id"]
        or proof.get("content_type") != metadata["content_type"]
        or version != metadata["version"]
        or proof.get("stat_identity") != metadata["stat_identity"]):
        raise ValueError("Payload proof and internal metadata disagree")
    current = inspect(path, system)
    if current != metadata:
        raise ValueError("Content changed during verification")
    return {
        "schema": "GCM_PLATFORM_PAYLOAD_PROOF_V1",
        "status": "PAYLOAD_HASHES_VERIFIED",
        "system": system, "path": metadata["path"],
        "title_id": metadata["title_id"],
        "content_type": metadata["content_type"],
        "version": metadata["version"],
        "sha256": proof["sha256"],
        "stat_identity": metadata["stat_identity"],
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "payload_evidence": proof,
        "game_relationship_verified": False,
        "installation_authorized": False,
    }


def check_container(path, system):
    if system != "ps3":
        raise ValueError("Separate container check currently supports PS3 only")
    metadata = inspect(path, system)
    proof = load("gcm-ps3-container-check.py").check(path)
    if (
        proof.get("status") != "CONTAINER_HASHES_VERIFIED"
        or proof.get("title_id") != metadata["title_id"]
        or proof.get("content_type") != metadata["content_type"]
        or proof.get("stat_identity") != metadata["stat_identity"]
        or proof.get("decrypted_payload_hashes_verified") is not False
        or proof.get("installation_authorized") is not False
        or inspect(path, system) != metadata
    ):
        raise ValueError("Container proof and internal metadata disagree")
    return {
        "schema": "GCM_PLATFORM_CONTAINER_PROOF_V1",
        "system": system,
        **proof,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("check-dependencies", "inspect", "verify-content", "check-container"))
    parser.add_argument("--system", choices=SYSTEMS)
    parser.add_argument("--path")
    args = parser.parse_args()
    if args.action == "check-dependencies":
        result = check_dependencies()
    else:
        if not args.system or not args.path:
            parser.error("inspect requires --system and --path")
        actions = {
            "inspect": inspect,
            "verify-content": verify_content,
            "check-container": check_container,
        }
        result = actions[args.action](args.path, args.system)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
