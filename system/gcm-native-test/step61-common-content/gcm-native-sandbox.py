"""Run a native tool with read-only mounts and one writable work directory."""
import ctypes
import errno
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class MountAttr(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "attr_set", "attr_clr", "propagation", "userns_fd")]

def isolate(work):
    work = Path(work).absolute()
    for path in (work, *work.parents):
        if path.is_symlink():
            raise ValueError("Symlink sandbox work path rejected")
    work = work.resolve(strict=True)
    work.relative_to(ROOT.resolve())
    if work == ROOT.resolve() or not work.is_dir():
        raise ValueError("Dedicated sandbox directory required")
    if platform.machine() not in ("x86_64", "aarch64"):
        raise ValueError("Sandbox syscall mapping unsupported on architecture")
    libc = ctypes.CDLL(None, use_errno=True)

    def checked(value, operation):
        if value != 0:
            error = ctypes.get_errno()
            raise OSError(error, operation + ": " + os.strerror(error))

    # Private mount and network namespaces; failure stops before mounting.
    checked(libc.unshare(ctypes.c_int(0x00020000 | 0x40000000)),
            "Create private mount/network namespaces")
    checked(libc.mount(None, b"/", None,
                       ctypes.c_ulong((1 << 14) | (1 << 18)), None),
            "Make mount propagation private")
    checked(libc.mount(b"/", b"/", None,
                       ctypes.c_ulong(4096 | 16384), None),
            "Clone root mount tree")
    checked(libc.mount(os.fsencode(work), os.fsencode(work), None,
                       ctypes.c_ulong(4096), None),
            "Create work-directory mount")
    # mount_setattr(AT_FDCWD, path, AT_RECURSIVE, attributes, size).
    readonly = MountAttr(1, 0, 0, 0)
    checked(libc.syscall(ctypes.c_long(442), ctypes.c_int(-100), b"/",
                         ctypes.c_uint(0x8000), ctypes.byref(readonly),
                         ctypes.c_size_t(ctypes.sizeof(readonly))),
            "Make mount tree read-only")
    writable = MountAttr(0, 1, 0, 0)
    checked(libc.syscall(ctypes.c_long(442), ctypes.c_int(-100),
                         os.fsencode(work), ctypes.c_uint(0),
                         ctypes.byref(writable),
                         ctypes.c_size_t(ctypes.sizeof(writable))),
            "Allow writes inside work directory")
    return work

def main():
    if len(sys.argv) < 3:
        raise ValueError("Usage: sandbox WORK probe | sandbox WORK run COMMAND...")
    work = isolate(sys.argv[1])
    if sys.argv[2] == "probe":
        inside = work / "writable-probe"
        inside.write_bytes(b"isolated")
        inside.unlink()
        outside = ROOT / (".readonly-probe-" + str(os.getpid()))
        try:
            with outside.open("xb") as stream:
                stream.write(b"unexpected")
        except OSError as exc:
            if exc.errno != errno.EROFS:
                raise
        else:
            outside.unlink()
            raise RuntimeError("Outside work directory remained writable")
        print("PASS: Work directory writable")
        print("PASS: Outside work directory read-only")
        print("PASS: Private mount and network namespaces active")
    elif sys.argv[2] == "run" and len(sys.argv) > 3:
        os.chdir(work)
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    else:
        raise ValueError("Invalid sandbox action")

if __name__ == "__main__":
    main()
