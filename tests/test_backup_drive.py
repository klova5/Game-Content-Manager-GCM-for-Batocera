import importlib.util, sys, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("w", sys.argv[1]); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n); c or fails.append(n)
with tempfile.TemporaryDirectory() as t:
    t = Path(t); media = t / "media"; target = t / "roms/switch_update"; (target / "dlc").mkdir(parents=True)
    (media / "GAMES/roms/switch_update/dlc").mkdir(parents=True); (media / "nvme1n1p3").mkdir()
    (media / "roms-ssd").symlink_to(media / "nvme1n1p3")
    calls = []
    run = lambda *c: calls.append(c) or True
    m = t / "mounts"
    m.write_text("/dev/sdb1 %s ntfs rw 0 0\n/dev/nvme1n1p3 %s ntfs rw 0 0\n" % (media / "GAMES", media / "nvme1n1p3"))
    note = w.bind_backup_drive(media, target, run, m)
    check("connected GAMES drive is bound onto switch_update", calls == [("mount", "--bind", str(media / "GAMES/roms/switch_update"), str(target))] and "GAMES" in note)
    calls.clear(); m.write_text(m.read_text() + "/dev/sdb1 %s ntfs rw 0 0\n" % target)
    check("already bound and drive present: nothing done", w.bind_backup_drive(media, target, run, m) is None and not calls)
    import shutil; shutil.rmtree(target / "dlc")
    m.write_text("/dev/nvme1n1p3 %s ntfs rw 0 0\n/dev/sdb1 %s ntfs rw 0 0\n" % (media / "nvme1n1p3", target))
    note = w.bind_backup_drive(media, target, run, m)
    check("drive unplugged: the stale bind is removed", calls == [("umount", "-l", str(target))] and "removed" in note)
    calls.clear(); m.write_text("/dev/nvme1n1p3 %s ntfs rw 0 0\n" % (media / "nvme1n1p3"))
    shutil.rmtree(media / "GAMES"); (media / "GAMES").mkdir()
    check("no drive with switch_update: nothing done", w.bind_backup_drive(media, target, run, m) is None and not calls)
    target.rmdir(); target.symlink_to(t / "elsewhere")
    check("a switch_update shortcut is left alone", w.bind_backup_drive(media, target, run, m) is None and not calls)
print("ALL BIND TESTS PASS" if not fails else "FAILED %s" % fails)
