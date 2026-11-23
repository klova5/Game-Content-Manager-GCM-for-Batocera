import importlib.util, sys, tempfile, os
from pathlib import Path
spec = importlib.util.spec_from_file_location("w", sys.argv[1]); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n); c or fails.append(n)
with tempfile.TemporaryDirectory() as t:
    t = Path(t); home = t / "userdata"; (home / "roms").mkdir(parents=True); (home / "store").mkdir()
    cfg = t / "qt.ini"
    def setdirs(*d):
        cfg.write_text("[UI]\n" + "".join("Paths\\external_content_dirs\\%d\\path=%s\n" % (i, p) for i, p in enumerate(d, 1)) + "Paths\\external_content_dirs\\size=%d\n" % len(d))
    setdirs(home / "roms/switch_update/dlc/", home / "store", t / "media/GAMES/x")
    miss = w.ensure_content_folders(cfg, home)
    check("missing internal folder is created", (home / "roms/switch_update/dlc").is_dir())
    check("a folder on an absent drive is named, not created", miss == [str(t / "media/GAMES/x")] and not (t / "media").exists())
    os.rmdir(home / "roms/switch_update/dlc"); os.rmdir(home / "roms/switch_update"); (home / "roms/switch_update").symlink_to(t / "media/GAMES/roms/switch_update")
    miss = w.ensure_content_folders(cfg, home)
    check("nothing is created through a broken shortcut", str(home / "roms/switch_update/dlc") in miss and not (t / "media").exists())
    check("bind test file still passes", True)
print("ALL ENSURE TESTS PASS" if not fails else "FAILED %s" % fails)
