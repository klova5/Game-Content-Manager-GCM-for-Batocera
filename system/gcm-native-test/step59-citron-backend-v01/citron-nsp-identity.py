#!/usr/bin/env python3
"""Read NSP internal CNMT identity; never modify source packages."""

import argparse
import importlib.util
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

DEV = Path("/userdata/system/gcm-native-test/v34-development")
WORK = Path(__file__).resolve().parent
NSTOOL = DEV / "v35-tools/build/nstool/bin/nstool"
PARSER = WORK / "citron-cnmt-parser.py"
KEYSET = Path("/userdata/bios/switch/keys/prod.keys")

MAX_ENTRIES = 4096
MAX_DIRECTORY = 4 * 1024 * 1024
MAX_META_NCA = 32 * 1024 * 1024


def cnmt_parser():
    spec = importlib.util.spec_from_file_location(
        "gcm_v35_cnmt_parser", PARSER
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_cnmt


def metadata_entries(fp, total):
    header = fp.read(16)

    if len(header) != 16:
        raise ValueError("Truncated NSP header")

    magic, count, string_size, _reserved = struct.unpack(
        "<4sIII", header
    )

    if magic != b"PFS0":
        raise ValueError("Not a standard PFS0 NSP")

    if count < 1 or count > MAX_ENTRIES:
        raise ValueError("Invalid NSP entry count")

    directory_size = count * 24 + string_size

    if directory_size > MAX_DIRECTORY:
        raise ValueError("NSP directory exceeds safety limit")

    directory = fp.read(directory_size)

    if len(directory) != directory_size:
        raise ValueError("Truncated NSP directory")

    table_end = count * 24
    strings = directory[table_end:]
    data_start = 16 + directory_size
    results = []

    for i in range(count):
        offset, size, name_pos, _ = struct.unpack_from(
            "<QQII", directory, i * 24
        )

        if name_pos >= len(strings):
            raise ValueError("Invalid NSP filename offset")

        end = strings.find(b"\0", name_pos)

        if end == -1:
            raise ValueError("Unterminated NSP filename")

        name = os.fsdecode(strings[name_pos:end])

        absolute = data_start + offset

        if absolute > total or size > total - absolute:
            raise ValueError("NSP entry exceeds file boundaries")

        if name.lower().endswith(".cnmt.nca"):
            if size < 1 or size > MAX_META_NCA:
                raise ValueError("Invalid metadata NCA size")

            results.append((name, absolute, size))

    return results


def identify(path):
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(path)

    if path.suffix.lower() != ".nsp":
        raise ValueError("Internal CNMT extraction currently supports NSP")

    if not NSTOOL.is_file():
        raise FileNotFoundError("NSTool is unavailable")

    before = path.stat()
    entries = []

    with path.open("rb") as fp:
        entries = metadata_entries(fp, before.st_size)

        if len(entries) != 1:
            raise ValueError(
                "Expected exactly one metadata NCA; found "
                + str(len(entries))
            )

        name, offset, size = entries[0]

        with tempfile.TemporaryDirectory(
            prefix="gcm-v35-identity-", dir=WORK
        ) as temporary:
            directory = Path(temporary)
            meta = directory / "package.cnmt.nca"
            output = directory / "extracted"
            output.mkdir()

            # Copy only the metadata NCA, never the complete NSP.
            fp.seek(offset)
            remaining = size

            with meta.open("wb") as dst:
                while remaining:
                    chunk = fp.read(min(1024 * 1024, remaining))

                    if not chunk:
                        raise ValueError("Truncated metadata NCA")

                    dst.write(chunk)
                    remaining -= len(chunk)

            process = subprocess.run(
                [
                    str(NSTOOL),
                    "-k", str(KEYSET),
                    "-t", "nca",
                    "-x", "/0", str(output),
                    str(meta),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
            )

            cnmt_files = [
                p for p in output.rglob("*.cnmt")
                if p.is_file()
            ]

            if process.returncode != 0 or len(cnmt_files) != 1:
                raise RuntimeError(
                    "NSTool could not extract one CNMT "
                    "(exit code "
                    + str(process.returncode) + "). "
                    "Check whether the existing decryption "
                    "configuration supports this package."
                )

            cnmt = cnmt_parser()(cnmt_files[0])

    after = path.stat()

    identity_before = (
        before.st_dev, before.st_ino,
        before.st_size, before.st_mtime_ns,
        before.st_ctime_ns,
    )
    identity_after = (
        after.st_dev, after.st_ino,
        after.st_size, after.st_mtime_ns,
        after.st_ctime_ns,
    )

    if identity_before != identity_after:
        raise RuntimeError("NSP changed during metadata identification")

    title_id = str(cnmt["title_id"]).upper()
    related = str(cnmt.get("related_id") or "").upper()
    ctype = int(cnmt["type"])
    version = int(cnmt["version"])

    if ctype == 0x81:
        content_type = "update"
        application_id = related

        if (
            not application_id
            or int(title_id, 16) - int(application_id, 16) != 0x800
        ):
            raise ValueError("Update/application relationship mismatch")

    elif ctype == 0x82:
        content_type = "dlc"
        application_id = related

        if (
            not application_id
            or (int(title_id, 16) & ~0x1FFF)
               != int(application_id, 16)
        ):
            raise ValueError("DLC/application relationship mismatch")

    elif ctype == 0x80:
        content_type = "application"
        application_id = title_id

    else:
        raise ValueError(
            "Unsupported CNMT content type: " + str(ctype)
        )

    if (
        len(title_id) != 16
        or len(application_id) != 16
        or not all(
            c in "0123456789ABCDEF"
            for c in title_id + application_id
        )
    ):
        raise ValueError("Invalid internal Title ID")

    return {
        "identified": True,
        "path": str(path),
        "title_id": title_id,
        "application_id": application_id,
        "content_type": content_type,
        "cnmt_type": ctype,
        "internal_version": version,
        "metadata_source": "internal-cnmt",
        "meta_nca_name": name,
        "meta_nca_bytes_read": size,
        "source_size": before.st_size,
        "full_package_integrity_verified": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nsp", type=Path, required=True)
    args = parser.parse_args()

    result = identify(args.nsp)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
