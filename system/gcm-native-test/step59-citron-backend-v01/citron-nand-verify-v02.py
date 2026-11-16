
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
    dependency = ROOT / "citron-package-verify-v02.py"
    expected = "24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f"
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
    missing_delta = []
    verified = []
    primary_count = 0
    for record in cnmt["records"]:
        key = record["content_id"]
        kind = record["type_number"]
        if (
            type(kind) is not int or kind not in range(7)
            or not re.fullmatch(r"[0-9a-f]{32}", key)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or key != record["sha256"][:32]
            or type(record["size"]) is not int or record["size"] <= 0
        ):
            raise ValueError("Invalid NAND content record")
        if kind != 6:
            primary_count += 1
        if key in seen:
            raise ValueError("Duplicate NAND content record")
        seen.add(key)
        candidate = index.get(key)
        if candidate is None:
            if content_type == "update" and kind == 6:
                missing_delta.append(key + ".nca")
                continue
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
        verified.append({
            "type": record["type"],
            "path": str(candidate),
            "sha256": record["sha256"],
            "stat_identity": list(parser.stat_identity(initial)),
        })

    if primary_count == 0:
        raise ValueError("No primary NAND content records")
    for record in verified:
        if list(parser.stat_identity(Path(record["path"]).stat())) != record["stat_identity"]:
            raise ValueError("NAND content changed before completion")
    if parser.stat_identity(before) != parser.stat_identity(metadata.stat()):
        raise ValueError("Metadata changed during verification")

    return {
        "title_id": cnmt["title_id"],
        "application_id": application_id,
        "content_type": content_type,
        "internal_version": cnmt["version"],
        "origin": "UserNAND",
        "path": str(metadata),
        "content_hashes_verified": not missing_delta,
        "primary_content_hashes_verified": True,
        "present_content_hashes_verified": True,
        "full_cnmt_record_set_present": not missing_delta,
        "missing_delta_records": missing_delta,
        "verified_primary_records": primary_count,
        "verified_records": verified,
        "metadata_stat_identity": list(parser.stat_identity(before)),
        "in_game_availability": "UNVERIFIED",
    }
