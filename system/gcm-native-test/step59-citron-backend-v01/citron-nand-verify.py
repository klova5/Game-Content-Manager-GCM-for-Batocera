
"""Verify one registered NAND metadata NCA and its referenced content."""
from pathlib import Path
import hashlib
import importlib.util
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent

def verify_record(registered, metadata, application_id, content_type):
    dependency = ROOT / "citron-package-verify.py"
    expected = "b3e4337e547e9a47a73ce1b36a7569b3f9f9f8bf21495329bc1ed0c4b5130042"
    if dependency.is_symlink() or hashlib.sha256(dependency.read_bytes()).hexdigest() != expected:
        raise ValueError("Package verifier changed")
    spec = importlib.util.spec_from_file_location("citron_verify", dependency)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser, reader = module.dependencies()

    if content_type not in ("update", "dlc"):
        raise ValueError("Unsupported content type")
    registered = Path(registered).resolve(strict=True)
    metadata = Path(metadata)
    if metadata.is_symlink() or not metadata.is_file():
        raise ValueError("Regular metadata file required")
    metadata = metadata.resolve(strict=True)
    metadata.relative_to(registered)
    before = metadata.stat()
    if not 0 < before.st_size <= reader.MAX_META_NCA:
        raise ValueError("Metadata size outside limits")

    content_id = metadata.name.split(".")[0].lower()
    if not re.fullmatch(r"[0-9a-f]{32}", content_id):
        raise ValueError("Invalid metadata content ID")
    if module.digest(metadata)[:32] != content_id:
        raise ValueError("Metadata content ID mismatch")

    index = {}
    for directory, dirs, files in os.walk(registered, followlinks=False):
        depth = len(Path(directory).relative_to(registered).parts)
        dirs[:] = [
            name for name in dirs
            if depth < 4 and not (Path(directory) / name).is_symlink()
        ]
        for name in files:
            candidate = Path(directory) / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            match = re.fullmatch(r"([0-9a-fA-F]{32})(?:\.cnmt)?\.nca", name)
            if match:
                key = match.group(1).lower()
                if key in index:
                    raise ValueError("Ambiguous NAND content ID")
                index[key] = candidate

    with tempfile.TemporaryDirectory(prefix="nand-verify-", dir=ROOT) as temporary:
        output = Path(temporary) / "extracted"
        output.mkdir()
        result = subprocess.run(
            [
                str(reader.NSTOOL), "-k", str(reader.KEYSET),
                "-t", "nca", "-x", "/0", str(output), str(metadata),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
        )
        found = list(output.rglob("*.cnmt"))
        if result.returncode or len(found) != 1:
            raise ValueError("NAND CNMT extraction failed")
        cnmt = parser.parse_cnmt(found[0])

    expected_type = 0x82 if content_type == "dlc" else 0x81
    if cnmt["type"] != expected_type or cnmt["related_id"] != application_id:
        raise ValueError("NAND record does not match selected game/type")
    if not cnmt["records"]:
        raise ValueError("Empty NAND content record list")

    seen = set()
    for record in cnmt["records"]:
        key = record["content_id"]
        if key in seen:
            raise ValueError("Duplicate NAND content record")
        seen.add(key)
        candidate = index.get(key)
        if candidate is None:
            raise ValueError("Referenced NAND content missing")
        initial = candidate.stat()
        if (
            initial.st_size != record["size"]
            or key != record["sha256"][:32]
            or module.digest(candidate) != record["sha256"]
        ):
            raise ValueError("NAND content integrity failure")
        if parser.stat_identity(initial) != parser.stat_identity(candidate.stat()):
            raise ValueError("NAND content changed during verification")

    if parser.stat_identity(before) != parser.stat_identity(metadata.stat()):
        raise ValueError("Metadata changed during verification")

    return {
        "title_id": cnmt["title_id"],
        "application_id": application_id,
        "content_type": content_type,
        "internal_version": cnmt["version"],
        "origin": "UserNAND",
        "path": str(metadata),
        "content_hashes_verified": True,
    }
