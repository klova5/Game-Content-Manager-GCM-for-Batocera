import hashlib, importlib.util, json, os, struct, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(sys.argv[1]).resolve(); B = HERE / "gcm-switch2-backend.py"
spec = importlib.util.spec_from_file_location("n", HERE / "gcm-nup.py"); N = importlib.util.module_from_spec(spec); spec.loader.exec_module(N)
fails = []
def check(n, c, extra=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else "  :: " + str(extra)[:600]), flush=True)
    if not c: fails.append(n)
def npdm(pid):
    d = bytearray(0x200); d[:4] = b"META"; struct.pack_into("<I", d, 0x70, 0x100); d[0x100:0x104] = b"ACI0"; struct.pack_into("<Q", d, 0x110, pid); return bytes(d)
def dump(root, pid=None, files=None):
    if pid is not None:
        (root / "exefs").mkdir(parents=True); (root / "exefs/main.npdm").write_bytes(npdm(pid)); (root / "exefs/main").write_bytes(os.urandom(3000))
    for rel, data in (files or {}).items():
        p = root / "romfs" / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
GID = 0x0400AAAA00010000
with tempfile.TemporaryDirectory() as t:
    t = Path(t); env = dict(os.environ, GCM_S2_ROOT=str(t))
    def run(*a):
        r = subprocess.run([sys.executable, "-B", str(B), *map(str, a)], capture_output=True, text=True, env=env)
        return r.stdout + r.stderr
    def field(out, k):
        for l in out.splitlines():
            p = l.split("\t")
            if len(p) >= 3 and p[1] == k: return p[2]
        return ""
    def job(*a):
        out = run("start", *a, "--confirm")
        jid = field(out, "ID")
        if not jid: return out
        for _ in range(400):
            st = run("status", "--job-id", jid, *a[:6])
            if field(st, "STATUS") != "RUNNING": return st
            time.sleep(0.1)
        return st
    work = t / "work"
    dump(work / "base", GID, {"a.bin": os.urandom(50000), "b.bin": b"base"})
    dump(work / "u1", GID, {"a.bin": os.urandom(60000)}); dump(work / "u2", GID, {"a.bin": os.urandom(70000), "c.bin": b"new"})
    roms = t / "userdata/roms/switch2"; roms.mkdir(parents=True)
    for name, kind, ver, extra in (("base", "base", 0, ["--title", "Test Game", "--display-version", "1.0.0"]), ("u1", "update", 65536, ["--display-version", "1.0.1"]), ("u2", "update", 131072, ["--display-version", "2.0.0"])):
        assert subprocess.run([sys.executable, "-B", str(HERE / "gcm-nup.py"), "pack", work / name, work / (name + ".nup"), "--kind", kind, "--version", str(ver), *extra], capture_output=True).returncode == 0
    game = roms / "Test Game [0400AAAA00010000].nup"
    assert subprocess.run([sys.executable, "-B", str(HERE / "gcm-nup.py"), "add", work / "base.nup", work / "u1.nup", "--out", game], capture_output=True).returncode == 0
    stub = roms / "Test Game.switch2"; stub.write_text("# entry")
    lp = t / "userdata/system/add-ons/switch2/eglantine"; lp.mkdir(parents=True)
    (lp / "launch-eglantine.sh").write_text('case "$(basename "$ROM")" in\n    "Test Game.switch2")\n        GAME="$ROMS/Test Game [0400AAAA00010000].nup" ;;\nesac\n')
    hdd = t / "media/GAMES/roms/switch2_update"; hdd.mkdir(parents=True)
    os.replace(work / "u2.nup", hdd / "Test Game update 2.0.0.nup")
    dump(hdd / "TestGame-dlc/dlc/0400AAAA00011001/0", None, {"Dlc/x.bin": os.urandom(5000)})
    other = roms / "Other [0400BBBB00010000].nup"
    dump(work / "ob", 0x0400BBBB00010000, {"z": b"z"}); subprocess.run([sys.executable, "-B", str(HERE / "gcm-nup.py"), "pack", work / "ob", other, "--kind", "base"], capture_output=True)
    R = ["--rom", stub, "--emulator", "lichii"]
    out = run("--mode", "summary", *R); check("summary for the .switch2 entry: update 1.0.1 active, no DLC", out.strip() == "SUMMARY\tv1.0.1 ACTIVE\t0 ENABLED", out)
    out = run("--mode", "list", "--type", "update", *R); check("update list: the update in the game is ACTIVE", "ROW\tAVAILABLE\tACTIVE\tUpdate 1.0.1\tfolder:" in out, out)
    out = job(*R, "--type", "update", "--operation", "scan-fast", "--tokens", ""); check("fast scan finishes", field(out, "STATUS") == "PASS", out)
    up = run("--mode", "list", "--type", "update", *R); dl = run("--mode", "list", "--type", "dlc", *R)
    check("the backup drive's update 2.0.0 is DETECTED", "DETECTED\tUNVERIFIED\tTest Game update 2.0.0.nup" in up, up)
    check("the loose DLC dump is DETECTED on the DLC page", "DETECTED\tUNVERIFIED\t0" in dl and "0400AAAA00011001" in dl, dl)
    check("another game's file is not offered", "BBBB" not in up + dl)
    tok_u = [l.split("\t")[-1] for l in up.splitlines() if "DETECTED" in l][0]; tok_d = [l.split("\t")[-1] for l in dl.splitlines() if "DETECTED" in l][0]
    out = job(*R, "--type", "update", "--operation", "prepare", "--tokens", tok_u); check("VERIFY copies the update into GCM's store", field(out, "STATUS") == "PASS", out)
    out = job(*R, "--type", "dlc", "--operation", "prepare", "--tokens", tok_d); check("VERIFY packs the loose DLC into GCM's store", field(out, "STATUS") == "PASS", out)
    store = t / "userdata/system/game-content/switch2/0400AAAA00010000"
    check("store holds one file per package", sorted(p.name for p in store.glob("*.nup")) == ["dlc-0400AAAA00011001-v0.nup", "update-0400AAAA00010800-v131072.nup"], list(store.iterdir()))
    up = run("--mode", "list", "--type", "update", *R)
    check("update 2.0.0 now AVAILABLE to install, no longer DETECTED", "AVAILABLE\tFOLDER_PREPARED\tUpdate 2.0.0" in up and "DETECTED" not in up, up)
    tok = [l.split("\t")[-1] for l in up.splitlines() if "FOLDER_PREPARED" in l][0]
    before = game.stat().st_mtime_ns
    out = run("--mode", "install-job", "--action", "start", "--token", tok, *R)
    jid = field(out, "ID")
    for _ in range(400):
        st = run("--mode", "install-job", "--action", "status", "--token", tok, *R)
        if field(st, "STATUS") != "RUNNING": break
        time.sleep(0.1)
    check("INSTALL from the menu puts update 2.0.0 into the game", field(st, "STATUS") == "INSTALLED", st)
    out = run("--mode", "summary", *R); check("summary now shows 2.0.0", out.strip() == "SUMMARY\tv2.0.0 ACTIVE\t0 ENABLED", out)
    up = run("--mode", "list", "--type", "update", *R); kept = [l for l in up.splitlines() if "ACTIVE" in l]
    check("the installed update says its GCM copy is kept", len(kept) == 1 and "(GCM copy kept)" in kept[0], up)
    ktok = kept[0].split("\t")[-1]; gm = game.stat().st_mtime_ns
    out = job(*R, "--type", "update", "--operation", "forget", "--tokens", ktok)
    check("REMOVE GCM COPY deletes the store copy and leaves the game alone", field(out, "STATUS") == "PASS" and not (store / "update-0400AAAA00010800-v131072.nup").exists() and game.stat().st_mtime_ns == gm and "ACTIVE" in run("--mode", "summary", *R), out)
    up = run("--mode", "list", "--type", "update", *R)
    check("the label no longer says a copy is kept", "(GCM copy kept)" not in [l for l in up.splitlines() if "ACTIVE" in l][0], up)
    out = run("start", *R, "--type", "update", "--operation", "forget", "--tokens", ktok, "--confirm")
    check("REMOVE GCM COPY with no copy left is refused", "no GCM copy" in out, out)
    otok = [l.split("\t")[-1] for l in up.splitlines() if "FOLDER_PREPARED" in l][0]
    out = run("start", *R, "--type", "update", "--operation", "forget", "--tokens", otok, "--confirm")
    check("REMOVE GCM COPY on an item that is only in the store is refused (it is the only copy)", "only one" in out and (store / "update-0400AAAA00010800-v65536.nup").exists(), out)
    pk = N.Nup(game).describe()
    check("the game holds exactly base + update 2.0.0 (1.0.1 moved out)", sorted((p["kind"], p["version"]) for p in pk) == [("base", 0), ("update", 131072)], pk)
    check("update 1.0.1 was saved to the store before it left the game", (store / "update-0400AAAA00010800-v65536.nup").exists())
    check("the rewritten game passes the full check", subprocess.run([sys.executable, "-B", str(HERE / "gcm-nup.py"), "verify", game, "--full"], capture_output=True).returncode == 0)
    dl = run("--mode", "list", "--type", "dlc", *R); dtok = [l.split("\t")[-1] for l in dl.splitlines() if "AVAILABLE" in l][0]
    check("the stored DLC shows as DISABLED (can be activated)", "AVAILABLE\tDISABLED" in dl, dl)
    out = job(*R, "--type", "dlc", "--operation", "apply", "--tokens", dtok); check("APPLY CHANGES with the DLC ticked adds it", field(out, "STATUS") == "PASS" and run("--mode", "summary", *R).strip() == "SUMMARY\tv2.0.0 ACTIVE\t1 ENABLED", out)
    out = job(*R, "--type", "dlc", "--operation", "disable", "--tokens", dtok); check("DISABLE takes the DLC out again; its store copy stays", field(out, "STATUS") == "PASS" and "0 ENABLED" in run("--mode", "summary", *R) and (store / "dlc-0400AAAA00011001-v0.nup").exists(), out)
    out = job(*R, "--type", "dlc", "--operation", "enable", "--tokens", dtok); check("ACTIVATE puts it back", field(out, "STATUS") == "PASS" and "1 ENABLED" in run("--mode", "summary", *R), out)
    out = job(*R, "--type", "dlc", "--operation", "uninstall", "--tokens", dtok)
    check("UNINSTALL removes it from the game; the store copy goes because its source dump still exists", field(out, "STATUS") == "PASS" and "0 ENABLED" in run("--mode", "summary", *R) and not (store / "dlc-0400AAAA00011001-v0.nup").exists(), out)
    up = run("--mode", "list", "--type", "update", *R); utok = [l.split("\t")[-1] for l in up.splitlines() if "ACTIVE" in l][0]
    out = job(*R, "--type", "update", "--operation", "disable", "--tokens", utok)
    check("deactivating the only update is refused only if no base... (base remains: allowed)", field(out, "STATUS") == "PASS" and run("--mode", "summary", *R).strip() == "SUMMARY\tNONE\t0 ENABLED", out)
    out = run("start", *R, "--type", "update", "--operation", "install", "--tokens", "folder:" + "0" * 64, "--confirm")
    check("an unknown selection is refused before any job starts", out.startswith("ERROR") and "no longer available" in out, out)
    (game.with_name("." + game.name + ".gcm-part")).write_bytes(b"x")
    up = run("--mode", "list", "--type", "update", *R); rtok = [l.split("\t")[-1] for l in up.splitlines() if "RECOVERY_REQUIRED" in l]
    check("a left-over work file offers RECOVER", len(rtok) == 1, up)
    out = job(*R, "--type", "update", "--operation", "recover", "--tokens", rtok[0]); check("RECOVER removes it", field(out, "STATUS") == "PASS" and not game.with_name("." + game.name + ".gcm-part").exists(), out)
    out = run("--mode", "summary", "--rom", stub, "--emulator", "citron-emu"); check("other emulators are refused", "ERROR" in out)
    spec2 = importlib.util.spec_from_file_location("b", B); BB = importlib.util.module_from_spec(spec2); spec2.loader.exec_module(BB)
    check("an update with no display version shows as Lichii does (131072 = 2.0.0)", BB.shown_version({"version": 131072}) == "2.0.0" and BB.shown_version({"version": 65536, "display_version": "1.0.1"}) == "1.0.1")
    out = run("capabilities", "--emulator", "lichii"); check("uninstall capability reported", "CAPABILITY\tUNINSTALL_DLC\t1" in out)
print("ALL SWITCH 2 BACKEND TESTS PASS" if not fails else "FAILED: %s" % fails); sys.exit(1 if fails else 0)
