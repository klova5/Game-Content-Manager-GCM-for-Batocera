import json, os, subprocess, sys, tempfile
from pathlib import Path
R = Path(sys.argv[1]).resolve(); fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else " :: " + str(x)[:500])); c or fails.append(n)
with tempfile.TemporaryDirectory() as t:
    t = Path(t); g = t / "userdata/system/add-ons/game-content-manager"; g.mkdir(parents=True)
    env = dict(os.environ, GCM_SCAN_ROOT=str(t))
    run = lambda *a: subprocess.run([sys.executable, "-B", str(R), *a], capture_output=True, text=True, env=env).stdout
    out = run(); check("no scan yet: says so and offers RESCAN NOW", "NO SCAN YET" in out and "ACTION\tRESCAN NOW" in out, out)
    (g / "emulator-registry.json").write_text(json.dumps({"scanned": "2026-11-23 10:00:00", "emulators": [
        {"system": "switch", "emulator": "citron-emu", "status": "built-in", "backend": "citron", "family": "yuzu", "evidence": ["supported by name"]},
        {"system": "switch", "emulator": "eden-emu", "status": "compatible", "backend": "citron", "family": "yuzu", "evidence": ["same storage"]},
        {"system": "n64", "emulator": "newemu", "status": "new-console", "backend": None, "family": None, "evidence": ["GCM does not manage the n64 console yet"]},
        {"system": "xbox", "emulator": "xemu", "status": "excluded", "evidence": []}]}))
    src = t / "userdata/roms/switch"; src.mkdir(parents=True); f = src / "Mystery [UPD].bin"; f.write_bytes(os.urandom(100000))
    w = t / "userdata/roms/wii"; w.mkdir(parents=True); f2 = w / "odd (DLC).wad"; f2.write_bytes(os.urandom(90000))
    (g / "content-inspection.json").write_text(json.dumps({"inspected": "2026-11-23 10:05:00", "content": [
        {"file": "/userdata/roms/switch/Mystery [UPD].bin", "size": 100000, "console": "switch", "kind": "possible-update", "evidence": ["its name looks like an update"]},
        {"file": "/userdata/roms/wii/odd (DLC).wad", "size": 90000, "console": "wii", "kind": "possible-dlc", "evidence": ["its name looks like DLC"]},
        {"file": "/userdata/roms/switch/x.nsp", "size": 1, "console": "switch", "kind": "update", "evidence": [], "drive": "GAMES"},
        {"file": "/userdata/roms/switch/y.nsp", "size": 1, "console": "switch", "kind": "dlc", "evidence": []}]}))
    out = run()
    check("a new console is listed first under NEEDS WORK", out.index("NEEDS WORK BEFORE") < out.index("N64 · NEWEMU") < out.index("EMULATORS (SCANNED"), out)
    check("Eden shows as handled by Citron", "SWITCH · EDEN-EMU\tSUPPORTED VIA CITRON" in out, out)
    check("excluded emulators are not listed", "XEMU" not in out)
    check("content counts per console, with the backup drive", "ROW\tSWITCH\t1 UPDATES · 1 DLC · 1 ON A BACKUP DRIVE" in out, out)
    check("2 files GCM cannot identify", "PAGE\tNOT IDENTIFIED\t2 FILES\tFILES GCM CANNOT IDENTIFY\tmanual" in out, out)
    d = run("details", "--system", "n64", "--emulator", "newemu")
    check("details say what a new console needs", "A GCM BACKEND FOR THIS CONSOLE" in d and "GCM DOES NOT MANAGE THE N64 CONSOLE YET" in d, d)
    m = run("manual")
    acts = [l.split("\t") for l in m.splitlines() if l.startswith("ACTION")]
    sw = [a for a in acts if a[1] == "Mystery [UPD].bin"][0]
    check("the warning names the target folder and that GCM cannot check it", "GCM CANNOT CHECK THIS FILE" in sw[3] and "/userdata/system/game-content/switch/updates" in sw[3] and "CITRON READS" in sw[3], sw)
    wi = [a for a in acts if a[1].startswith("odd")][0]
    check("other consoles go to GCM's manual folder, with a note", "/userdata/system/game-content/wii/manual/dlc" in wi[3] and "MAY NOT READ" in wi[3], wi)
    r = run(*sw[4].split()); check("install as-is copies the file", r.startswith("OK") and (t / "userdata/system/game-content/switch/updates/Mystery [UPD].bin").read_bytes() == f.read_bytes(), r)
    check("the original is left in place", f.exists())
    r2 = run(*sw[4].split()); check("installing the same file twice is refused", r2.startswith("ERROR"), r2)
    m = run("manual"); check("copied file listed under COPIED AS-IS, no longer under NOT IDENTIFIED", "COPIED AS-IS BY YOU" in m and sum(l.split("\t")[1] == "Mystery [UPD].bin" for l in m.splitlines() if l.startswith("ACTION")) == 1 and "NOT IDENTIFIED (1)" in m, m)
    check("summary counts the copy", "PAGE\tCOPIED AS-IS BY YOU\t1 FILES" in run())
    rem = [l.split("\t") for l in m.splitlines() if l.startswith("ACTION") and "manual-remove" in l][0]
    (t / "userdata/system/game-content/switch/updates/Mystery [UPD].bin").write_bytes(b"changed")
    r = run(*rem[4].split()); check("a copy changed by someone else is not deleted", r.startswith("ERROR") and (t / "userdata/system/game-content/switch/updates/Mystery [UPD].bin").exists(), r)
    (t / "userdata/system/game-content/switch/updates/Mystery [UPD].bin").write_bytes(f.read_bytes())
    r = run(*rem[4].split()); check("REMOVE deletes the copy and keeps the original", r.startswith("OK") and not (t / "userdata/system/game-content/switch/updates/Mystery [UPD].bin").exists() and f.exists(), r)
    f2.write_bytes(b"short"); wi2 = [l.split("\t") for l in run("manual").splitlines() if l.startswith("ACTION") and l.split("\t")[1].startswith("odd")][0]
    r = run(*wi2[4].split()); check("a file that changed since the check is refused", r.startswith("ERROR") and "changed" in r, r)
    r = run("rescan"); check("rescan reports both tools (missing here)", r.startswith("OK") and "tool missing" in r, r)
print("ALL REPORT TESTS PASS" if not fails else "FAILED %s" % fails); sys.exit(1 if fails else 0)
