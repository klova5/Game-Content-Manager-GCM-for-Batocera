
"""Copy verified NSP content to persistent GCM storage without overwriting."""
from pathlib import Path
import fcntl
import hashlib
import importlib.util
import os
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
DATA = Path("/userdata/system/game-content")
VERIFIER_HASH = "24882cddab3fe9bbb4ad35f39fb0a99fc70078a071987c1f7698aba75365dd6f"

def verifier():
    path = ROOT / "citron-package-verify-v02.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != VERIFIER_HASH:
        raise ValueError("Verifier checksum mismatch")
    spec = importlib.util.spec_from_file_location("citron_verify", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def safe_directory(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Absolute managed path required")
    current = Path("/")
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symlink in managed destination")
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise ValueError("Managed directory is not a directory")

def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def copy_verified(source, application_id, content_type):
    source = Path(source)
    check = verifier()
    with (ROOT / "managed-copy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = check.verify(source, application_id, content_type)

        parent = DATA / "switch"
        safe_directory(parent)
        matches = [
            item for item in parent.iterdir()
            if item.name.casefold() == application_id.casefold()
        ]
        if len(matches) > 1:
            raise ValueError("Ambiguous managed game directories")
        game = matches[0] if matches else parent / application_id.lower()
        folder = game / ("dlc" if content_type == "dlc" else "updates")
        safe_directory(folder)
        destination = folder / (source.stem + ".nsp")
        if destination.is_symlink():
            raise ValueError("Symlink destination rejected")

        copied = False
        if destination.exists():
            local = check.verify(destination, application_id, content_type)
            if local != report:
                raise ValueError("Existing managed file differs; retained")
        else:
            required = report["source_size"] + 128 * 1024 * 1024
            if shutil.disk_usage(folder).free < required:
                raise OSError("Insufficient space for verified local copy")
            fd, name = tempfile.mkstemp(prefix=".citron-", suffix=".part", dir=folder)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output, 8 * 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())

                # The verifier requires an NSP extension.
                staged = temporary.with_suffix(".nsp")
                os.link(temporary, staged)
                try:
                    local = check.verify(staged, application_id, content_type)
                    if local != report or check.digest(source) != report["sha256"]:
                        raise ValueError("Copy/source verification failed")
                    os.link(staged, destination)
                    copied = True
                    sync_directory(folder)
                finally:
                    staged.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)

        final = check.verify(destination, application_id, content_type)
        if final != report or check.digest(source) != report["sha256"]:
            raise ValueError("Final copy/source verification failed")

        return {
            **report,
            "state": "READY",
            "destination": str(destination),
            "copied": copied,
            "source_preserved": True,
            "citron_registration_verified": False,
        }
