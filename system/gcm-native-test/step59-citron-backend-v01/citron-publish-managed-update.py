
"""Publish a previously verified managed update without copying or overwriting."""
from pathlib import Path
import hashlib
import importlib.util
import os

ROOT = Path(__file__).resolve().parent
PUBLICATION_ROOT = Path("/userdata/system/game-content/citron-registered")

def dependencies():
    path = ROOT / "citron-managed-copy-v02.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != "bd30b8d534b2777e68d7adf65d247e0a828a05c99c649329f0535a124fe7d114":
        raise ValueError("Managed-directory helper changed")
    spec = importlib.util.spec_from_file_location("managed_directories", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

def publish(managed, destination, expected_sha256, *, publication_root=PUBLICATION_ROOT):
    managed = Path(managed)
    destination = Path(destination)
    publication_root = Path(publication_root)
    if (
        not managed.is_absolute() or managed.is_symlink() or not managed.is_file()
        or not destination.is_absolute() or not publication_root.is_absolute()
        or len(expected_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_sha256)
    ):
        raise ValueError("Invalid managed publication input")
    relative = destination.relative_to(publication_root)
    if len(relative.parts) != 3 or relative.parts[1] != "updates":
        raise ValueError("Unexpected publication layout")
    game = relative.parts[0]
    if len(game) != 16 or any(c not in "0123456789ABCDEF" for c in game):
        raise ValueError("Invalid publication application ID")
    if destination.suffix.lower() != ".nsp":
        raise ValueError("NSP publication required")
    directories = dependencies()
    directories.safe_directory(destination.parent)
    if destination.is_symlink():
        raise ValueError("Symlink publication rejected")

    before = managed.stat()
    if digest(managed) != expected_sha256:
        raise ValueError("Managed hash differs from verified copy")
    checked = managed.stat()
    fields = lambda st: (
        st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if fields(before) != fields(checked):
        raise ValueError("Managed file changed during hash check")

    if destination.exists():
        if not destination.is_file() or not destination.samefile(managed):
            raise FileExistsError("Existing publication differs; no overwrite")
        return False

    created = False
    try:
        os.link(managed, destination)
        created = True
        directories.sync_directory(destination.parent)
        after = managed.stat()
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or not destination.samefile(managed)
            or digest(destination) != expected_sha256
        ):
            raise ValueError("Published content failed verification")
        return True
    except BaseException:
        if created:
            st = destination.lstat()
            if (st.st_dev, st.st_ino) == (before.st_dev, before.st_ino):
                destination.unlink()
                directories.sync_directory(destination.parent)
        raise
