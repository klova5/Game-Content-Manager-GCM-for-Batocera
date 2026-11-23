import base64, json, os, subprocess, sys, tempfile
from pathlib import Path
R = Path(sys.argv[1]).resolve(); fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else " :: " + str(x)[:600])); c or fails.append(n)
def rows(out, kind=None):
    r = [l.split("\t") for l in out.splitlines() if l]
    return [x for x in r if kind is None or x[0] == kind]
with tempfile.TemporaryDirectory() as t:
    t = Path(t); env = dict(os.environ, GCM_SCAN_ROOT=str(t))
    run = lambda *a: subprocess.run([sys.executable, "-B", str(R), *a], capture_output=True, text=True, env=env).stdout
    runargs = lambda args: run(*args.split())
    (t / "usr/share/emulationstation").mkdir(parents=True)
    (t / "usr/share/emulationstation/es_systems.cfg").write_text("<systemList>"
        "<system><name>windows</name><fullname>Windows</fullname><path>/userdata/roms/n64</path></system><system><name>n64</name><fullname>Nintendo 64</fullname><path>/userdata/roms/n64real</path></system>"
        "<system><name>xbox</name><fullname>Xbox</fullname><path>/userdata/roms/snes</path></system><system><name>amiga</name><fullname>Amiga</fullname><path>/userdata/roms/amiga</path></system>"
        "<system><name>switch</name><fullname>Switch</fullname><path>/userdata/roms/switch</path></system>"
        "<system><name>gba</name><fullname>Game Boy Advance</fullname><path>/userdata/roms/gba</path></system></systemList>")
    n64 = t / "userdata/roms/n64"; (n64 / "images").mkdir(parents=True); (n64 / "dlc").mkdir()
    (n64 / "Zelda.z64").write_bytes(b"z" * 5000); (n64 / "Mario.z64").write_bytes(b"m" * 4000)
    (n64 / "Zelda (Update v1.1).zip").write_bytes(b"u" * 3000); (n64 / "dlc/Expansion.bin").write_bytes(b"d" * 2000)
    (n64 / "images/Zelda.png").write_bytes(b"png"); (n64 / "gamelist.xml").write_text("<gameList/>")
    (t / "userdata/roms/snes").mkdir(parents=True); (t / "userdata/roms/snes/Game.sfc").write_bytes(b"s")
    (t / "userdata/roms/gba").mkdir(parents=True)
    (t / "userdata/roms/switch").mkdir(parents=True); (t / "userdata/roms/switch/x.nsp").write_bytes(b"x")
    (t / "userdata/system/add-ons/game-content-manager").mkdir(parents=True)
    for d in ("n64real", "amiga"): (t / "userdata/roms" / d).mkdir(parents=True); (t / "userdata/roms" / d / "x.rom").write_bytes(b"r")
    out = run(); check("summary links MANUAL CONTENT", "PAGE\tMANUAL CONTENT\tMARK, PAIR AND COPY FILES YOURSELF\tMANUAL CONTENT\tmc" in out, out)
    out = run("mc"); pages = rows(out, "PAGE")
    check("only Windows is listed (not Xbox, N64, Amiga, Switch)",
          [p[1] for p in pages] == ["WINDOWS"] and pages[0][2] == "4 FILES", pages)
    c = run(*pages[0][4].split()); cp = rows(c, "PAGE"); groups = [g[1] for g in rows(c, "GROUP")]
    check("files are sorted by GCM's guess: 1 update, 1 DLC (dlc folder), 2 games; images and gamelist skipped",
          groups == ["UPDATES (1)", "DLC (1)", "GAMES (2)"] and len(cp) == 4, (groups, cp))
    upd = [p for p in cp if p[1].startswith("Zelda (Update")][0]
    check("an unpaired guess says so", upd[2] == "GUESSED · NO GAME", upd)
    f = run(*upd[4].split())
    check("file page: type is GCM's guess; offers marks, FOR GAME and COPY TO A FOLDER",
          "TYPE\tUPDATE (GCM'S GUESS FROM ITS NAME)" in f and "MARK AS DLC" in f and "PAGE\tFOR GAME\tNONE CHOSEN" in f and "COPY TO A FOLDER" in f, f)
    games = [x for x in rows(f, "PAGE") if x[1] == "FOR GAME"][0]
    gp = run(*games[4].split()); zelda = [x for x in rows(gp, "ACTION") if x[1] == "Zelda"][0]
    check("the game picker lists the 2 games", sorted(x[1] for x in rows(gp, "ACTION")) == ["Mario", "Zelda"], gp)
    r = runargs(zelda[4]); check("pair the update with Zelda", r.startswith("OK") and "Zelda" in r, r)
    c = run(*pages[0][4].split()); upd = [p for p in rows(c, "PAGE") if p[1].startswith("Zelda (Update")][0]
    check("the console page shows the pairing", upd[2] == "GUESSED · FOR ZELDA", upd)
    mario = [p for p in rows(c, "PAGE") if p[1] == "Mario.z64"][0]
    mf = run(*mario[4].split()); md = [x for x in rows(mf, "ACTION") if x[1] == "MARK AS DLC"][0]
    r = runargs(md[4]); check("mark a file as DLC", r.startswith("OK"), r)
    c = run(*pages[0][4].split()); check("it moves to DLC as MARKED", "DLC (2)" in c and any(p[1] == "Mario.z64" and p[2].startswith("MARKED") for p in rows(c, "PAGE")), c)
    mf = run(*mario[4].split()); clr = [x for x in rows(mf, "ACTION") if x[1] == "CLEAR MY MARK"]
    check("CLEAR MY MARK is offered and works", clr and runargs(clr[0][4]).startswith("OK") and "GAMES (2)" in run(*pages[0][4].split()))
    f = run(*upd[4].split()); cpy = [x for x in rows(f, "PAGE") if x[1] == "COPY TO A FOLDER"][0]
    st = run(*cpy[4].split()); starts = [x[1] for x in rows(st, "PAGE") + rows(st, "ACTION")]
    check("folder choices start with the game's folder, then the console's folders",
          starts[:2] == ["THE GAME'S FOLDER", "ALL ROM FOLDERS"] or starts[0] == "THE GAME'S FOLDER", starts)
    manual = [x for x in rows(st, "ACTION") if x[1] == "GCM'S MANUAL FOLDER"][0]
    check("the GCM manual folder is offered (created on copy), with the warning", "GCM CANNOT CHECK THIS FILE" in manual[3], manual)
    gf = [x for x in rows(st, "PAGE") if x[1] == "THE GAME'S FOLDER"][0]
    b = run(*gf[4].split()); here = [x for x in rows(b, "ACTION") if x[1] == "COPY INTO THIS FOLDER"][0]
    check("browsing the game's folder offers COPY INTO THIS FOLDER, its subfolders, and warns about the game list",
          "MAY APPEAR IN THE GAME LIST" in here[3] and any(x[1] == "dlc" for x in rows(b, "PAGE")), b)
    sub = [x for x in rows(b, "PAGE") if x[1] == "dlc"][0]; b2 = run(*sub[4].split())
    up = [x for x in rows(b2, "PAGE") if x[1].startswith("..")]
    check("a subfolder can go back up", up and up[0][2] == "/userdata/roms/n64", b2)
    here2 = [x for x in rows(b2, "ACTION") if x[1] == "COPY INTO THIS FOLDER"][0]
    r = runargs(here2[4]); dest = n64 / "dlc/Zelda (Update v1.1).zip"
    check("copy into the chosen folder: exact copy, original kept", r.startswith("OK") and dest.read_bytes() == (n64 / "Zelda (Update v1.1).zip").read_bytes(), r)
    check("copying again is refused (no overwrite)", runargs(here2[4]).startswith("ERROR"))
    f = run(*upd[4].split()); cop = [x for x in rows(f, "ACTION") if x[1] == "COPIED TO"]
    check("the file page lists the copy with REMOVE", len(cop) == 1 and cop[0][2] == "/userdata/roms/n64/dlc/Zelda (Update v1.1).zip", f)
    c = run(*pages[0][4].split()); check("the console page counts the copy and the copy itself is listed as a file", "1 COPIED" in c, c)
    r = runargs(cop[0][4]); check("REMOVE deletes the copy only", r.startswith("OK") and not dest.exists() and (n64 / "Zelda (Update v1.1).zip").exists(), r)
    r = runargs(manual[4]); check("copy to GCM's manual folder creates it", r.startswith("OK") and (t / "userdata/system/game-content/windows/manual/Zelda (Update v1.1).zip").exists(), r)
    bad = base64.urlsafe_b64encode(b"/userdata/system/add-ons").decode().rstrip("=")
    r = run("mc-copy", "--console", "windows", "--token", upd[4].split()[-1], "--folder", bad)
    check("a folder outside the allowed places is refused", r.startswith("ERROR") and not (t / "userdata/system/add-ons/Zelda (Update v1.1).zip").exists(), r)
    trav = base64.urlsafe_b64encode(b"/userdata/roms/../system/add-ons").decode().rstrip("=")
    r = run("mc-copy", "--console", "windows", "--token", upd[4].split()[-1], "--folder", trav)
    check("a path with .. is refused", r.startswith("ERROR"), r)
    (t / "userdata/roms/linked").symlink_to(t / "userdata/system/add-ons")
    lk = base64.urlsafe_b64encode(b"/userdata/roms/linked").decode().rstrip("=")
    r = run("mc-copy", "--console", "windows", "--token", upd[4].split()[-1], "--folder", lk)
    check("a folder reached through a link is refused", r.startswith("ERROR"), r)
    r = run("mc-console", "--console", "switch"); check("a console GCM manages is refused here", "ERROR" in r, r)
    r = run("mc-console", "--console", "n64"); check("a console without updates or DLC is refused here", "ERROR" in r, r)
    r = run("mc-console", "--console", "xbox"); check("Xbox is not covered", "ERROR" in r, r)
    big = t / "userdata/roms/n64"
    for i in range(2100): (big / ("%s game %04d.sfc" % ("ABC"[i % 3], i))).write_bytes(b"g")
    (big / "1942.sfc").write_bytes(b"g"); (big / "Big (Update).zip").write_bytes(b"u")
    out = run("mc"); sn = [x for x in rows(out, "PAGE") if x[1] == "WINDOWS"][0]
    check("a big library shows 2000+ FILES", sn[2] == "2000+ FILES", sn)
    c = run(*sn[4].split()); lp = [x for x in rows(c, "PAGE") if len(x[1]) == 1]
    check("its games are split by first letter (A, B, C, #)", [x[1] for x in lp] == ["A", "B", "C", "#"] and "ONLY THE FIRST 2000 FILES" in c, [x[1] for x in lp])
    a = run(*lp[0][4].split()); check("a letter page lists that letter's games", len(rows(a, "PAGE")) == sum(1 for x in rows(a, "PAGE") if x[1].startswith("A")) > 100, len(rows(a, "PAGE")))
    hs = run(*[x for x in lp if x[1] == "#"][0][4].split()); check("# holds names that do not start with a letter", [x[1] for x in rows(hs, "PAGE")] == ["1942.sfc"], hs)
    bu = [x for x in rows(c, "PAGE") if x[1] == "Big (Update).zip"]
    if bu:
        f = run(*bu[0][4].split()); fg = [x for x in rows(f, "PAGE") if x[1] == "FOR GAME"][0]
        gp = run(*fg[4].split()); gl = [x for x in rows(gp, "PAGE") if len(x[1]) == 1]
        check("the game picker is split by letter too", [x[1] for x in gl][:3] == ["A", "B", "C"], gp[:300])
        gpa = run(*gl[0][4].split()); check("and a letter lists games to pair with", len(rows(gpa, "ACTION")) > 100)
    else:
        check("the update is listed on the console page", False, c[:500])
print("ALL MANUAL CONTENT TESTS PASS" if not fails else "FAILED %s" % fails); sys.exit(1 if fails else 0)
