#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import struct
from pathlib import Path

TYPES = {
    0: "Meta",
    1: "Program",
    2: "Data",
    3: "Control",
    4: "HtmlDocument",
    5: "LegalInformation",
    6: "DeltaFragment",
}

def u16(data, pos):
    return struct.unpack_from("<H", data, pos)[0]

def u32(data, pos):
    return struct.unpack_from("<I", data, pos)[0]

def u64(data, pos):
    return struct.unpack_from("<Q", data, pos)[0]

def parse_cnmt(path):
    data = path.read_bytes()
    if not 64 <= len(data) <= 8 * 1024 * 1024:
        raise ValueError("Unexpected CNMT size")

    kind = data[12]
    extended_size = u16(data, 14)
    count = u16(data, 16)
    start = 0x20 + extended_size

    if count > 4096 or start + count * 0x38 > len(data) - 32:
        raise ValueError("Invalid CNMT content table")

    result = {
        "title_id": f"{u64(data, 0):016X}",
        "version": u32(data, 8),
        "type": kind,
        "related_id": (
            f"{u64(data, 0x20):016X}"
            if ((kind in (0x80, 0x81) and extended_size >= 16)
                or (kind == 0x82 and extended_size >= 8))
            else None
        ),
        "records": [],
    }

    for i in range(count):
        pos = start + i * 0x38
        content_type = data[pos + 0x36]
        result["records"].append({
            "type": TYPES.get(content_type, f"Unknown_{content_type}"),
            "type_number": content_type,
            "content_id": data[pos + 0x20:pos + 0x30].hex(),
            "size": int.from_bytes(data[pos + 0x30:pos + 0x35], "little"),
            "attributes": data[pos + 0x35],
            "sha256": data[pos:pos + 0x20].hex(),
        })

    return result

def read_pfs0(fp, total_size):
    header = fp.read(16)
    if len(header) != 16 or header[:4] != b"PFS0":
        raise ValueError("Not a PFS0 NSP")

    count = u32(header, 4)
    strsize = u32(header, 8)

    if count > 4096 or strsize > 1024 * 1024:
        raise ValueError("Excessive PFS0 directory size")

    entries_size = count * 24
    directory = fp.read(entries_size + strsize)

    if len(directory) != entries_size + strsize:
        raise ValueError("Truncated PFS0 directory")

    names = directory[entries_size:]
    data_start = 16 + entries_size + strsize
    files = {}

    for i in range(count):
        pos = i * 24
        offset, size, name_offset = struct.unpack_from(
            "<QQI", directory, pos
        )

        if name_offset >= len(names):
            raise ValueError("Invalid filename offset")

        end = names.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("Unterminated filename")

        name = names[name_offset:end].decode("ascii")

        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", name):
            raise ValueError("Unexpected package filename")

        if name in files:
            raise ValueError("Duplicate NSP filename")

        absolute = data_start + offset

        if absolute > total_size or size > total_size - absolute:
            raise ValueError("Archive extends beyond NSP")

        files[name] = (absolute, size)

    return files

def sha256_member(fp, entry, name):
    offset, size = entry
    fp.seek(offset)

    digest = hashlib.sha256()
    remaining = size
    processed = 0
    last_progress = 0

    while remaining:
        block = fp.read(min(8 * 1024 * 1024, remaining))
        if not block:
            raise IOError("Unexpected end of package")

        digest.update(block)
        remaining -= len(block)
        processed += len(block)

        progress = processed // (256 * 1024 * 1024)
        if progress > last_progress:
            print(
                f"  {name}: {processed:,} / {size:,} bytes",
                flush=True
            )
            last_progress = progress

    return digest.hexdigest()

def stat_identity(st):
    return (
        st.st_dev, st.st_ino, st.st_size,
        st.st_mtime_ns, st.st_ctime_ns
    )

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nsp", type=Path, required=True)
    parser.add_argument("--base-cnmt", type=Path, required=True)
    parser.add_argument("--patch-cnmt", type=Path, required=True)
    parser.add_argument("--meta-nca", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = parse_cnmt(args.base_cnmt)
    patch = parse_cnmt(args.patch_cnmt)

    identity_match = (
        base["type"] == 0x80
        and patch["type"] == 0x81
        and patch["related_id"] == base["title_id"]
        and base["related_id"] == patch["title_id"]
        and patch["version"] > 0
    )

    report = {
        "schema": "GCM_V35_INTERNAL_CNMT_CONTENT_V1",
        "base_title_id": base["title_id"],
        "patch_title_id": patch["title_id"],
        "patch_version": patch["version"],
        "identity_match": identity_match,
        "content_results": [],
        "delta_fragment_records_not_in_package": [],
        "unreferenced_nca_entries": [],
        "main_content_hashes_verified": False,
        "meta_archive_hash_verified": False,
        "full_package_integrity_verified": False,
        "nca_header_signatures_verified_in_this_run": False,
        "import_authorized": False,
        "changes_performed": False,
    }

    print("===== READ NSP DIRECTORY =====", flush=True)

    with args.nsp.open("rb") as fp:
        before = stat_identity(os.fstat(fp.fileno()))
        files = read_pfs0(fp, os.fstat(fp.fileno()).st_size)
        print(f"NSP directory: {len(files)} entries", flush=True)

        print("===== VERIFY META NCA =====", flush=True)

        meta_name = args.meta_nca.name
        if meta_name not in files:
            raise ValueError("Metadata NCA absent from original NSP")

        meta_hash = sha256_member(fp, files[meta_name], meta_name)
        extracted_meta_hash = hashlib.sha256(
            args.meta_nca.read_bytes()
        ).hexdigest()

        report["meta_archive_hash_verified"] = (
            meta_hash == extracted_meta_hash
            and meta_name.lower() == meta_hash[:32] + ".cnmt.nca"
        )

        print(
            "Meta NCA:",
            "MATCH" if report["meta_archive_hash_verified"] else "FAIL",
            flush=True
        )

        expected_names = {meta_name}
        mandatory_ok = True
        mandatory_count = 0

        print("===== VERIFY INTERNAL CNMT CONTENT RECORDS =====", flush=True)

        for record in patch["records"]:
            name = record["content_id"] + ".nca"

            if record["type_number"] == 6 and name not in files:
                report["delta_fragment_records_not_in_package"].append(name)
                continue

            if record["type_number"] != 6:
                mandatory_count += 1

            expected_names.add(name)
            entry = files.get(name)

            result = {
                "type": record["type"],
                "filename": name,
                "expected_size": record["size"],
                "present": entry is not None,
                "size_match": False,
                "sha256_match": False,
            }

            if entry is not None:
                result["size_match"] = entry[1] == record["size"]

                if result["size_match"]:
                    digest = sha256_member(fp, entry, name)
                    result["sha256_match"] = (
                        digest == record["sha256"]
                    )

            report["content_results"].append(result)

            passed = result["size_match"] and result["sha256_match"]

            if record["type_number"] != 6 and not passed:
                mandatory_ok = False

            print(
                f'{record["type"]}: {"MATCH" if passed else "FAIL"}',
                flush=True
            )

        report["unreferenced_nca_entries"] = sorted(
            name for name in files
            if name.endswith(".nca") and name not in expected_names
        )

        after = stat_identity(os.fstat(fp.fileno()))

    report["source_stat_unchanged_during_scan"] = (
        before == after
        and stat_identity(args.nsp.stat()) == before
    )

    report["main_content_hashes_verified"] = (
        identity_match
        and mandatory_count > 0
        and mandatory_ok
        and report["meta_archive_hash_verified"]
        and not report["unreferenced_nca_entries"]
        and report["source_stat_unchanged_during_scan"]
    )

    report["decision"] = (
        "MAIN_CONTENT_HASHES_MATCH_INTERNAL_CNMT_IMPORT_BLOCKED"
        if report["main_content_hashes_verified"]
        else "INTEGRITY_CHECK_FAILED_IMPORT_BLOCKED"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)

    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    os.replace(temporary, args.output)

    print("\n===== GCM V3.5 VERDICT =====")
    print("Identity match:", identity_match)
    print("Primary content records:", mandatory_count)
    print(
        "Primary content hashes verified:",
        report["main_content_hashes_verified"]
    )
    print(
        "Delta records absent from NSP:",
        len(report["delta_fragment_records_not_in_package"])
    )
    print(
        "Unexpected NCA files:",
        len(report["unreferenced_nca_entries"])
    )
    print("Import authorized: False")
    print("Report:", args.output)

if __name__ == "__main__":
    main()
