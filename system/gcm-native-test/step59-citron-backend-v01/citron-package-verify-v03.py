
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


def verify_records(stream, entries, meta_name, records, content_type, parser):
    expected = {meta_name}
    seen = {meta_name}
    missing_delta = []
    primary_count = 0
    for record in records:
        kind = record["type_number"]
        content_id = record["content_id"]
        expected_hash = record["sha256"]
        size = record["size"]
        if (
            type(kind) is not int or kind not in range(7)
            or type(size) is not int or size <= 0
            or not isinstance(content_id, str) or len(content_id) != 32
            or not isinstance(expected_hash, str) or len(expected_hash) != 64
            or any(c not in "0123456789abcdef" for c in content_id + expected_hash)
            or content_id != expected_hash[:32]
        ):
            raise ValueError("Invalid content record")
        member = content_id + ".nca"
        if member in seen:
            raise ValueError("Duplicate content record")
        seen.add(member)
        if kind != 6:
            primary_count += 1
        if member not in entries:
            if content_type == "update" and kind == 6:
                missing_delta.append(member)
                continue
            raise ValueError("Missing referenced NCA")
        expected.add(member)
        if entries[member][1] != size:
            raise ValueError("Referenced NCA size mismatch")
        if parser.sha256_member(stream, entries[member], member) != expected_hash:
            raise ValueError("Referenced NCA hash mismatch")
    if primary_count == 0:
        raise ValueError("No primary content records")
    actual = {name for name in entries if name.lower().endswith(".nca")}
    if actual != expected:
        raise ValueError("Unverified extra NCA members")
    return {
        "primary_content_hashes_verified": True,
        "present_content_hashes_verified": True,
        "full_cnmt_record_set_present": not missing_delta,
        "missing_delta_records": missing_delta,
        "verified_primary_records": primary_count,
    }

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

            content = verify_records(
                stream, entries, name, cnmt["records"], content_type, parser
            )
            display = {}
            if content_type == "update":
                controls = [r for r in cnmt["records"] if r["type_number"] == 3]
                if len(controls) != 1:
                    raise ValueError("Expected one verified update Control record")
                control = controls[0]
                member = control["content_id"] + ".nca"
                control_path = folder / "control.nca"
                control_output = folder / "control-extracted"
                control_output.mkdir()
                control_offset, control_size = entries[member]
                stream.seek(control_offset)
                remaining = control_size
                with control_path.open("wb") as extracted:
                    while remaining:
                        block = stream.read(min(8 * 1024 * 1024, remaining))
                        if not block:
                            raise ValueError("Truncated Control NCA")
                        extracted.write(block)
                        remaining -= len(block)
                if digest(control_path) != control["sha256"]:
                    raise ValueError("Extracted Control hash mismatch")
                process = subprocess.run(
                    [str(reader.NSTOOL), "-k", str(reader.KEYSET),
                     "-t", "nca", "-x", "/0", str(control_output),
                     str(control_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=90)
                nacps = list(control_output.rglob("control.nacp"))
                if process.returncode or len(nacps) != 1:
                    raise ValueError("Control NACP extraction failed")
                with nacps[0].open("rb") as nacp:
                    nacp.seek(0x3060)
                    raw = nacp.read(16)
                if len(raw) != 16:
                    raise ValueError("Truncated NACP display version")
                version = raw.split(bytes([0]), 1)[0].decode("utf-8")
                if (not version or version != version.strip()
                        or any(ord(c) < 32 or ord(c) == 127 for c in version)):
                    raise ValueError("Invalid NACP display version")
                display = {
                    "display_version": version,
                    "control_sha256": control["sha256"],
                }


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
        **display,
        **content,
        "cnmt_content_hashes_verified": content["full_cnmt_record_set_present"],
        "full_package_integrity_verified": False,
        "citron_registration_verified": False,
    }
