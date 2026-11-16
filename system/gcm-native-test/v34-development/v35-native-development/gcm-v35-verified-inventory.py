#!/usr/bin/env python3
"""GCM v3.5 verified external inventory overlay.

Read-only with respect to game files and the existing GCM
database. A verified record requires a guarded PASS job.
"""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEV = Path(
    "/userdata/system/gcm-native-test/v34-development"
)
WORK = DEV / "v35-native-development"
JOBS = DEV / "v35-tools/verification-jobs"

BACKEND = Path(
    "/userdata/system/add-ons/"
    "game-content-manager/gcm-native-v33.py"
)

STORE = WORK / "v35-verified-inventory.json"
LOCK = WORK / "v35-verified-inventory.lock"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

SCHEMA = "GCM_V35_VERIFIED_EXTERNAL_V1"

REQUIRED_CHECKS = (
    "preview_passed",
    "identity_match",
    "main_content_hashes_verified",
    "meta_archive_hash_verified",
    "base_meta_nca_sha256_verified",
    "base_source_stat_unchanged",
    "update_source_stat_unchanged",
    "source_stat_unchanged_during_scan",
)


def canonical(path):
    return os.path.abspath(os.fspath(path))


def key_for(base, source):
    data = (base + "\0" + source).encode()
    return hashlib.sha256(data).hexdigest()


def full_stat(path):
    s = Path(path).stat()
    return [
        s.st_dev, s.st_ino, s.st_size,
        s.st_mtime_ns, s.st_ctime_ns
    ]


def persistent_stat(path):
    s = Path(path).stat()
    return [s.st_size, s.st_mtime_ns]


def load_store():
    if not STORE.exists():
        return {"schema": SCHEMA, "records": {}}

    state = json.loads(STORE.read_text())

    if (
        state.get("schema") != SCHEMA
        or not isinstance(state.get("records"), dict)
    ):
        raise ValueError("Unexpected verified inventory schema")

    return state


def save_store(state):
    fd, temp = tempfile.mkstemp(
        prefix=".v35-verified-",
        suffix=".tmp",
        dir=WORK
    )

    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

        os.chmod(temp, 0o600)
        os.replace(temp, STORE)

    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def verified_now(entry, rom, source):
    if (
        entry.get("rom") != rom
        or entry.get("source") != source
    ):
        return False

    try:
        return (
            entry.get("base_stat") == persistent_stat(rom)
            and entry.get("update_stat") == persistent_stat(source)
        )
    except OSError:
        return False


def record(args):
    base = canonical(args.rom)
    token = args.token

    if (
        len(token) < 2
        or len(token) > 8192
        or len(token) % 2
        or any(
            c not in "0123456789abcdefABCDEF"
            for c in token
        )
    ):
        raise ValueError("Invalid selected package token")

    decoded = bytes.fromhex(token).decode("utf-8")

    if not os.path.isabs(decoded):
        raise ValueError("Package path is not absolute")

    source = canonical(decoded)

    if not source.lower().endswith(".nsp"):
        raise ValueError("Only NSP updates are supported")

    job_file = JOBS / (key_for(base, source) + ".json")
    job = json.loads(job_file.read_text())

    if (
        job.get("status") != "PASS"
        or job.get("boot_id") != BOOT_ID.read_text().strip()
        or job.get("preview_passed") is not True
        or job.get("import_authorized") is not False
        or job.get("changes_performed") is not False
        or not job.get("scan_id")
    ):
        raise ValueError(
            "No current guarded PASS job for this package"
        )

    if job.get("source_stats") != [
        full_stat(base),
        full_stat(source)
    ]:
        raise ValueError(
            "Base game or update changed since verification"
        )

    report_path = Path(job.get("verifier_report", ""))

    if not report_path.is_file():
        raise ValueError("Guarded verifier report unavailable")

    report = json.loads(report_path.read_text())

    if (
        report.get("status") != "PASS"
        or report.get("import_authorized") is not False
        or report.get("changes_performed") is not False
        or not all(
            report.get(field) is True
            for field in REQUIRED_CHECKS
        )
    ):
        raise ValueError(
            "Guarded integrity checks did not all pass"
        )

    base_id = job.get("base_title_id", "")
    patch_id = job.get("patch_title_id", "")

    if (
        report.get("base_title_id") != base_id
        or report.get("patch_title_id") != patch_id
        or str(report.get("patch_version"))
           != str(job.get("version"))
    ):
        raise ValueError(
            "Verification identity or version mismatch"
        )

    entry = {
        "rom": base,
        "source": source,
        "base_stat": persistent_stat(base),
        "update_stat": persistent_stat(source),
        "base_title_id": base_id,
        "patch_title_id": patch_id,
        "version": str(job.get("version")),
        "verification_scan_id": job["scan_id"],
        "verified_at_utc": job.get(
            "completed_at_utc", ""
        ),
        "verifier_report": str(report_path),
        "classification": "VERIFIED_EXTERNAL",
    }

    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        state = load_store()
        state["records"][key_for(base, source)] = entry
        save_store(state)

    print("VERIFY\tPROMOTION\tRECORDED")
    print("VERIFY\tSOURCE\t" + source)
    print("VERIFY\tSTATE\tVERIFIED_EXTERNAL")
    print("STATUS\tNO FILE CHANGES")


def rewrite_inventory(output, rom, state):
    """Promote only exact, still-valid DETECTED entries."""

    result = []
    changed = 0

    for line in output.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        fields = raw.split("\t", 4)

        if (
            len(fields) == 5
            and fields[:2] == ["ROW", "DETECTED"]
        ):
            try:
                source = bytes.fromhex(
                    fields[4]
                ).decode("utf-8")

                if os.path.isabs(source):
                    source = canonical(source)

                    entry = state["records"].get(
                        key_for(rom, source), {}
                    )

                    if verified_now(entry, rom, source):
                        fields[1] = "AVAILABLE"
                        fields[2] = "VERIFIED_EXTERNAL"

                        line = (
                            "\t".join(fields)
                            + line[len(raw):]
                        )

                        changed += 1

            except (ValueError, UnicodeError, TypeError):
                pass

        result.append(line)

    return "".join(result), changed


def find_arg(args, name):
    try:
        at = args.index(name)
        return args[at + 1]

    except (ValueError, IndexError):
        return None


def inventory(args):
    command = [
        sys.executable,
        str(BACKEND),
        *args
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True
    )

    if result.stderr:
        sys.stderr.write(result.stderr)

    output = result.stdout

    if (
        result.returncode == 0
        and find_arg(args, "--mode") == "list"
        and find_arg(args, "--type") == "update"
        and find_arg(args, "--rom")
    ):
        base = canonical(find_arg(args, "--rom"))

        try:
            output, _ = rewrite_inventory(
                output, base, load_store()
            )

        except (
            OSError, ValueError, KeyError, TypeError
        ) as exc:
            # Fail closed: leave packages in DETECTED.
            sys.stderr.write(
                "GCM verified overlay unavailable: "
                + str(exc) + "\n"
            )

    sys.stdout.write(output)
    return result.returncode


def selftest():
    with tempfile.TemporaryDirectory(
        prefix="gcm-v35-overlay-test-"
    ) as tmp:
        base = Path(tmp) / "Stray.nsp"
        source = Path(tmp) / "Stray v1.6.0.nsp"

        base.write_bytes(b"test-base")
        source.write_bytes(b"test-update")

        rom = str(base)
        update = str(source)
        token = update.encode().hex()

        row = (
            "ROW\tDETECTED\tEXTERNAL\t"
            f"Stray v1.6.0\t{token}\n"
        )

        # In-memory test fixture only.
        # Never creates a real verification PASS.
        fixture = {
            "schema": SCHEMA,
            "records": {
                key_for(rom, update): {
                    "rom": rom,
                    "source": update,
                    "base_stat": persistent_stat(rom),
                    "update_stat": persistent_stat(update),
                }
            }
        }

        rewritten, count = rewrite_inventory(
            row, rom, fixture
        )

        assert count == 1
        assert (
            "\tAVAILABLE\tVERIFIED_EXTERNAL\t"
            in rewritten
        )

        untouched, count = rewrite_inventory(
            row, "/wrong/game", fixture
        )

        assert count == 0
        assert untouched == row

        source.write_bytes(b"changed-update")

        untouched, count = rewrite_inventory(
            row, rom, fixture
        )

        assert count == 0
        assert untouched == row

    print("PASS: Verified update promotes into AVAILABLE")
    print("PASS: Unrelated games remain untouched")
    print("PASS: Changed source cannot be promoted")
    print("PASS: No live verification record created")
    print("PASS: No NSP or SQLite modifications")


def main():
    os.umask(0o077)

    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
        return 0

    if len(sys.argv) > 1 and sys.argv[1] == "record":
        parser = argparse.ArgumentParser()
        parser.add_argument("action")
        parser.add_argument("--rom", required=True)
        parser.add_argument("--token", required=True)

        record(parser.parse_args())
        return 0

    return inventory(sys.argv[1:])


if __name__ == "__main__":
    try:
        sys.exit(main())

    except Exception as exc:
        print(
            "ERROR\t"
            + str(exc).replace("\n", " ")[:400]
        )
        sys.exit(1)
