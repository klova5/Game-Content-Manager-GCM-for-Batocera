
"""Verify standard single-metadata NSP identity and referenced content hashes."""
from pathlib import Path
import hashlib
import importlib.util
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = {
    "citron-cnmt-parser.py":
        "cf0b35eb3ffb219b881b6e298fec817aac5d1b3ba06314f12590fe4cadfd7b15",
    "citron-nsp-identity.py":
        "4405e100d73dc645eaa324cbcc1158d25707944f86646cc070fbcd6eff212af8",
}

def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

def dependencies():
    loaded = {}
    for name, expected in DEPENDENCIES.items():
        path = ROOT / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError("Verifier dependency mismatch: " + name)
        spec = importlib.util.spec_from_file_location(name[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded[name] = module
    return loaded["citron-cnmt-parser.py"], loaded["citron-nsp-identity.py"]

def verify(package, application_id, content_type):
    package = Path(package)
    if package.is_symlink() or not package.is_file():
        raise ValueError("Regular package file required")
    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    parser, reader = dependencies()
    before = package.stat()
    identity = reader.identify(package)
    if (
        identity["application_id"] != application_id
        or identity["content_type"] != content_type
    ):
        raise ValueError("Package does not match selected game/type")

    with package.open("rb") as stream:
        entries = parser.read_pfs0(stream, before.st_size)
        name = identity["meta_nca_name"]
        offset, size = entries[name]
        if not 0 < size <= reader.MAX_META_NCA:
            raise ValueError("Invalid metadata size")

        meta_hash = parser.sha256_member(stream, entries[name], name)
        if name.lower() != meta_hash[:32] + ".cnmt.nca":
            raise ValueError("Metadata content ID mismatch")

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            folder = Path(temporary)
            meta = folder / "metadata.cnmt.nca"
            output = folder / "extracted"
            output.mkdir()
            stream.seek(offset)
            data = stream.read(size)
            if len(data) != size:
                raise ValueError("Truncated metadata")
            meta.write_bytes(data)

            process = subprocess.run(
                [
                    str(reader.NSTOOL), "-k", str(reader.KEYSET),
                    "-t", "nca", "-x", "/0", str(output), str(meta),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=90,
            )
            found = list(output.rglob("*.cnmt"))
            if process.returncode or len(found) != 1:
                raise ValueError("Internal CNMT extraction failed")
            cnmt = parser.parse_cnmt(found[0])
            if (
                cnmt["title_id"] != identity["title_id"]
                or cnmt["related_id"] != application_id
                or cnmt["version"] != identity["internal_version"]
                or cnmt["type"] != identity["cnmt_type"]
                or not cnmt["records"]
            ):
                raise ValueError("Internal metadata mismatch")

            expected_ncas = {name}
            for record in cnmt["records"]:
                member = record["content_id"] + ".nca"
                if member in expected_ncas:
                    raise ValueError("Duplicate content record")
                expected_ncas.add(member)
                if member not in entries:
                    raise ValueError("Missing referenced NCA")
                if entries[member][1] != record["size"]:
                    raise ValueError("Referenced NCA size mismatch")
                if record["content_id"] != record["sha256"][:32]:
                    raise ValueError("Content ID/hash mismatch")
                if parser.sha256_member(stream, entries[member], member) != record["sha256"]:
                    raise ValueError("Referenced NCA hash mismatch")

            actual_ncas = {
                item for item in entries if item.lower().endswith(".nca")
            }
            if actual_ncas != expected_ncas:
                raise ValueError("Unverified extra NCA members")

    package_hash = digest(package)
    if parser.stat_identity(before) != parser.stat_identity(package.stat()):
        raise ValueError("Package changed during verification")

    return {
        "application_id": application_id,
        "title_id": identity["title_id"],
        "content_type": content_type,
        "internal_version": identity["internal_version"],
        "sha256": package_hash,
        "source_size": before.st_size,
        "cnmt_content_hashes_verified": True,
        "citron_registration_verified": False,
    }
