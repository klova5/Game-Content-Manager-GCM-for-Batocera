import json, os, subprocess, sys, tempfile
from pathlib import Path
T = Path(sys.argv[1]).resolve(); fails = []
def check(n, c, x=""):
    print(("PASS " if c else "FAIL ") + n + ("" if c else " :: " + str(x)[:500])); c or fails.append(n)
with tempfile.TemporaryDirectory() as t:
    t = Path(t); mods = t / "mods"; mods.mkdir()
    (mods / "gcm-ps4-extraction-rehearsal.py").write_text('''
from pathlib import Path
def rehearse(src, work):
    out = Path(work) / "output"; (out / "sce_sys").mkdir(parents=True)
    data = Path(src).read_bytes()
    (out / "sce_sys/param.sfo").write_bytes(b"SFO" + data[:8]); (out / "content.bin").write_bytes(data)
''')
    env = dict(os.environ, GCM_PS4_ROOT=str(t), GCM_PS4_TEST_MODULES=str(mods), GCM_PS4_TEST_GAME="CUSA08809")
    run = lambda *a: subprocess.run([sys.executable, "-B", str(T), *map(str, a)], capture_output=True, text=True, env=env).stdout
    roms = t / "userdata/roms/ps4"; (roms / "CUSA08809/sce_sys").mkdir(parents=True)
    (t / "userdata/system/gcm-native-test/step61-common-content").mkdir(parents=True)
    us = roms / "dlc-us/CUSA08034/ANCIENTBOW000001"; (us / "sce_sys").mkdir(parents=True); (us / "a.dat").write_bytes(os.urandom(4000)); (us / "sce_sys/param.sfo").write_bytes(b"x")
    pkg = roms / "CUSA08034_CLOAK.pkg"; pkg.write_bytes(os.urandom(5000))
    upd = roms / "SotC-1.01.pkg"; upd.write_bytes(os.urandom(6000))
    own = roms / "DLC/CUSA08809/LIFESWORD0000001"; own.mkdir(parents=True); (own / "x").write_bytes(b"o")
    nolabel = roms / "odd-dlc"; nolabel.mkdir(); (nolabel / "f").write_bytes(b"n")
    ins = t / "userdata/system/add-ons/game-content-manager"; ins.mkdir(parents=True)
    (ins / "content-inspection.json").write_text(json.dumps({"content": [
        {"file": "/userdata/roms/ps4/dlc-us/CUSA08034/ANCIENTBOW000001", "console": "ps4", "kind": "dlc", "format": "ps4-folder", "game_id": "CUSA08034"},
        {"file": "/userdata/roms/ps4/CUSA08034_CLOAK.pkg", "console": "ps4", "kind": "dlc", "format": "ps4-package", "game_id": "CUSA08034", "content_id": "UP9000-CUSA08034_00-CLOAKOFFATE00001"},
        {"file": "/userdata/roms/ps4/SotC-1.01.pkg", "console": "ps4", "kind": "update", "format": "ps4-package", "game_id": "CUSA08809", "content_id": "EP9000-CUSA08809_00-SOTC0000000000EU"},
        {"file": "/userdata/roms/ps4/DLC/CUSA08809/LIFESWORD0000001", "console": "ps4", "kind": "dlc", "format": "ps4-folder", "game_id": "CUSA08809"},
        {"file": "/userdata/roms/ps4/odd-dlc", "console": "ps4", "kind": "dlc", "format": "ps4-folder", "game_id": "CUSA00001"}]}))
    R = ["--rom", roms / "CUSA08809.sh"]
    rows = lambda out: [l.split("\t") for l in out.splitlines() if l.startswith("ROW")]
    d = rows(run("list", *R, "--type", "dlc"))
    m = [r for r in d if r[1] == "MANUAL"]
    check("the US DLC folder and package are offered for the EU game, marked ANOTHER ID", sorted(r[3].split(" ")[0] for r in m) == ["ANCIENTBOW000001/", "CUSA08034_CLOAK.pkg"] and all(r[2] == "ANOTHER ID" for r in m), d)
    check("the game's own installed DLC is not offered", not any("LIFESWORD" in r[3] for r in d))
    check("a DLC whose label GCM cannot tell is shown but blocked", any(r[1] == "BLOCKED" and r[2] == "NO DLC LABEL" for r in d), d)
    check("the warning names the target and the region caveat", "/userdata/roms/ps4/DLC/CUSA08809/ANCIENTBOW000001" in m[0][5] + m[1][5] and "ANOTHER REGION" in m[0][5], m)
    folder_tok = [r for r in m if r[3].startswith("ANCIENTBOW")][0][4]; pkg_tok = [r for r in m if r[3].startswith("CUSA08034_CLOAK")][0][4]
    out = run("install", *R, "--type", "dlc", "--token", folder_tok)
    tgt = roms / "DLC/CUSA08809/ANCIENTBOW000001"
    check("a DLC folder is copied into this game's DLC folder, checked", out.startswith("OK") and (tgt / "a.dat").read_bytes() == (us / "a.dat").read_bytes(), out)
    out = run("install", *R, "--type", "dlc", "--token", pkg_tok)
    tgt2 = roms / "DLC/CUSA08809/CLOAKOFFATE00001"
    check("a DLC package is unpacked into this game's DLC folder", out.startswith("OK") and (tgt2 / "content.bin").read_bytes() == pkg.read_bytes(), out)
    check("no work folders are left behind", not list((t / "userdata/system/gcm-native-test/step61-common-content").glob("step61-ps4-extraction-*")))
    d = rows(run("list", *R, "--type", "dlc"))
    check("both installs are listed with REMOVE and no longer offered", len([r for r in d if r[1] == "INSTALLED"]) == 2 and not [r for r in d if r[1] == "MANUAL"], d)
    check("installing again is refused", run("install", *R, "--type", "dlc", "--token", folder_tok).startswith("ERROR"))
    u = [r for r in rows(run("list", *R, "--type", "update")) if r[1] == "MANUAL"]
    check("an update for this game's ID is offered, going to <GAME>-UPDATE", len(u) == 1 and u[0][2] == "THIS GAME'S ID" and "CUSA08809-UPDATE" in u[0][5], u)
    out = run("install", *R, "--type", "update", "--token", u[0][4])
    check("the update is unpacked into CUSA08809-UPDATE", out.startswith("OK") and (roms / "CUSA08809-UPDATE/content.bin").exists(), out)
    inst = [r for r in rows(run("list", *R, "--type", "dlc")) if r[1] == "INSTALLED"]
    (tgt / "a.dat").write_bytes(b"changed")
    rid = [r for r in inst if "ANCIENTBOW" in r[3]][0][4]
    check("a changed install is not removed", run("remove", *R, "--type", "dlc", "--token", rid).startswith("ERROR") and tgt.exists())
    (tgt / "a.dat").write_bytes((us / "a.dat").read_bytes())
    out = run("remove", *R, "--type", "dlc", "--token", rid)
    check("REMOVE takes the install out and keeps the original", out.startswith("OK") and not tgt.exists() and us.exists(), out)
    d = rows(run("list", *R, "--type", "dlc"))
    check("after REMOVE the DLC is offered again", any(r[1] == "MANUAL" and r[3].startswith("ANCIENTBOW") for r in d), d)
print("ALL PS4 MANUAL INSTALL TESTS PASS" if not fails else "FAILED %s" % fails); sys.exit(1 if fails else 0)
