"""Fixtures for gcm-content-inspect.py with sample files built to each format's layout."""
import hashlib, json, os, struct, subprocess, sys, tempfile
from pathlib import Path
INS = Path(sys.argv[1]).resolve(); fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
def tlv(t, s): b = s.encode(); return struct.pack("<HHI", t, 0, len(b)) + b
def nup(packages):
    """packages: (title_id, version, kind, flags, title, display_version)"""
    count = len(packages); toc_size = 0x30 + count * 0x40 + 0x18
    meta_base = 0x80 + toc_size; metas, recs, off = [], b"", meta_base
    for tid, ver, kind, flags, title, disp in packages:
        m = tlv(5, title) + tlv(4, disp); metas.append(m)
        recs += struct.pack("<QIIIIBBHIQI", tid, 0, 0xFFFFFFFF, 0xFFFFFFFF, ver, kind, flags, 0, 0, off, len(m)) + b"\0" * 20
        off += len(m)
    toc = struct.pack("<IIQQQQQ", 0x01434F54, count, 1, 0, 0x30 + count * 0x40 + 0x18, 0, 0) + recs + b"\0" * 0x18
    blob = (off + 15) // 16 * 16; size = blob + 32
    sb = b"NUP\x01" + struct.pack("<HHQQ", 0x80, 1, 0x80, len(toc)) + hashlib.sha256(toc).digest() + struct.pack("<QQII", blob, size, count, 0) + b"\0" * 0x30
    data = sb + toc + b"".join(metas); return data + b"\0" * (size - len(data))
def pfs0(names):
    pool = b"".join(n.encode() + b"\0" for n in names); entries, o = b"", 0
    for n in names:
        entries += struct.pack("<QQII", 0, 0, o, 0); o += len(n) + 1
    return b"PFS0" + struct.pack("<III", len(names), len(pool), 0) + entries + pool
def sfo(d):
    keys, vals, ents = b"", b"", b""
    for k, v in d.items():
        vb = v.encode() + b"\0"
        ents += struct.pack("<HHIII", len(keys), 0x0204, len(vb), len(vb), len(vals))
        keys += k.encode() + b"\0"; vals += vb
    ks = 20 + len(ents)
    return b"\0PSF" + struct.pack("<IIII", 0x101, ks, ks + len(keys), len(d)) + ents + keys + vals
def ps4pkg(cid, ctype, cflags, category=None):
    h = bytearray(0x1000); h[:4] = b"\x7fCNT"; h[0x40:0x40 + len(cid)] = cid.encode()
    struct.pack_into(">II", h, 0x74, ctype, cflags)
    if category:
        data = sfo({"CATEGORY": category, "TITLE": "Game " + cid[7:16], "APP_VER": "01.03"})
        struct.pack_into(">I", h, 0x10, 1); struct.pack_into(">I", h, 0x18, 0x400)
        struct.pack_into(">IIIIII", h, 0x400, 0x1000, 0, 0, 0, 0x800, len(data)); h[0x800:0x800 + len(data)] = data
    return bytes(h)
def ps3pkg(cid, ctype):
    h = bytearray(0x400); h[:4] = b"\x7fPKG"; h[0x30:0x30 + len(cid)] = cid.encode()
    struct.pack_into(">II", h, 8, 0xC0, 2); struct.pack_into(">III", h, 0xC0, 1, 4, 2); struct.pack_into(">III", h, 0xCC, 2, 4, ctype)
    return bytes(h)
def wad(tid, ver):
    h = bytearray(0x1000); struct.pack_into(">IIIIII", h, 0, 0x20, 0x49730000, 0x40, 0, 0x40, 0x208)
    struct.pack_into(">Q", h, 0x40 + 0x40 + 0x40 + 0x18C, tid); struct.pack_into(">H", h, 0xC0 + 0x1DC, ver); return bytes(h)
def npdm(pid):
    d = bytearray(0x200); d[:4] = b"META"; struct.pack_into("<I", d, 0x70, 0x100); d[0x100:0x104] = b"ACI0"
    struct.pack_into("<Q", d, 0x110, pid); return bytes(d)
with tempfile.TemporaryDirectory() as t:
    r = Path(t); u = r / "userdata"
    (r / "usr/share/emulationstation").mkdir(parents=True)
    (r / "usr/share/emulationstation/es_systems.cfg").write_text("<systemList><system><name>switch</name><path>/userdata/roms/switch</path></system>"
        "<system><name>snes</name><path>/userdata/roms/snes</path></system><system><name>xbox360</name><path>/userdata/roms/xbox360</path></system><system><name>ps4</name><path>/userdata/roms/ps4</path></system>"
        "<system><name>ps3</name><path>/userdata/roms/ps3</path></system><system><name>wii</name><path>/userdata/roms/wii</path></system></systemList>")
    (u / "system/configs/emulationstation").mkdir(parents=True)
    (u / "system/configs/emulationstation/es_systems_switch2.cfg").write_text("<systemList><system><name>switch2</name><path>/userdata/roms/switch2</path>"
        "<emulators><emulator name=\"lichii\"/></emulators></system></systemList>")
    s2 = u / "roms/switch2"; s2.mkdir(parents=True); sw = u / "roms/switch"; sw.mkdir(parents=True)
    (s2 / "Mario Kart World [0400C3F00006E000].nup").write_bytes(nup([
        (0x0400C3F00006E000, 0, 0, 0, "Mario Kart World", "1.0.0"),
        (0x0400C3F00006E800, 131072, 1, 0, "Mario Kart World", "2.0.0"),
        (0x0400C3F00006F001, 0, 2, 0, "Mario Kart World DLC", "1.0")]))
    (s2 / "Zelda OoT [0400486024DEC000][v131072].nup").write_bytes(nup([(0x0400486024DEC800, 131072, 1, 1, "Zelda Ocarina of Time", "2.0.0")]))
    bad = bytearray(nup([(0x0400E88025B1A000, 0, 0, 0, "Star Fox", "1.0")])); bad[0x90] ^= 0xFF
    (s2 / "Star Fox [0400E88025B1A000].nup").write_bytes(bytes(bad))
    (s2 / "Mario Kart World.switch2").write_text("Mario Kart World - Eglantine direct launch")
    (sw / "renamed-file.bin").write_bytes(pfs0(["0100152000022800000000000000000a.tik", "abc.cnmt.nca", "def.nca"]))
    cart = bytearray(0x400); cart[0x100:0x104] = b"HEAD"; (sw / "Mario Kart 8 Deluxe [0100152000022000].xci").write_bytes(bytes(cart))
    ext = u / "system/game-content/switch/updates"; ext.mkdir(parents=True)
    (ext / "Smash [01006A800016E800][v65536].nsp").write_bytes(pfs0(["a.cnmt.nca"]))
    (u / "roms/switch/Mystery Game [UPD] v2.bin").write_bytes(b"\x00" * 64)
    (u / "roms/switch/random-notes.dat").write_bytes(b"hello")
    (sw / "copy").mkdir(); (sw / "copy/Smash [01006A800016E800][v65536].nsp").write_bytes(pfs0(["a.cnmt.nca"]))
    x = u / "roms/xbox360"; x.mkdir(parents=True)
    stfs = bytearray(0x400); stfs[:4] = b"LIVE"; stfs[0x344:0x348] = struct.pack(">I", 2); stfs[0x360:0x364] = struct.pack(">I", 0x4D5307E6)
    (x / "dlc-pack").write_bytes(bytes(stfs))
    yuzu = u / "system/configs/yuzu"; yuzu.mkdir(parents=True)
    (yuzu / "qt-config.ini").write_text("[UI]\nPaths\\external_content_dirs\\1\\path=/userdata/system/game-content/switch/updates\n")
    reg = u / "system/add-ons/game-content-manager"; reg.mkdir(parents=True)
    (reg / "emulator-registry.json").write_text(json.dumps({"schema": "GCM_EMULATOR_REGISTRY_V1", "emulators": [
        {"system": "switch", "emulator": "citron-emu", "family": "yuzu", "config": "userdata/system/configs/yuzu/qt-config.ini"}]}))
    p4 = u / "roms/ps4"; (p4 / "updates").mkdir(parents=True)
    (p4 / "Last Guardian Game.pkg").write_bytes(ps4pkg("UP9000-CUSA03627_00-LASTGUARDIANUS00", 0x1A, 0x0A000000, "gd"))
    (p4 / "renamed.pkg").write_bytes(ps4pkg("UP9000-CUSA03627_00-LASTGUARDIANUS00", 0x1A, 0x40000000))
    (p4 / "CUSA08034_ANCIENT_BOW.pkg").write_bytes(ps4pkg("UP9000-CUSA08034_00-ANCIENTBOW000001", 0x1B, 0))
    (p4 / "DLC.sh.keys").write_bytes(b"k" * 100)
    g = p4 / "CUSA00870"; (g / "sce_sys").mkdir(parents=True); (g / "sce_sys/param.sfo").write_bytes(sfo({"CATEGORY": "gd", "TITLE_ID": "CUSA00870", "TITLE": "Tales", "APP_VER": "01.00"}))
    for i in range(50): (g / ("data%d.ttarch2" % i)).write_bytes(b"x" * 70000)
    up = p4 / "CUSA00870-UPDATE"; (up / "sce_sys").mkdir(parents=True); (up / "sce_sys/param.sfo").write_bytes(sfo({"CATEGORY": "gp", "TITLE_ID": "CUSA00870", "APP_VER": "01.02"}))
    for i in range(30): (up / ("Borderlands102_%d.ttarch2" % i)).write_bytes(b"x" * 70000)
    loose = p4 / "updates/SomeGame-patch"; loose.mkdir(parents=True)
    for i in range(20): (loose / ("chunk%d.bin" % i)).write_bytes(b"x" * 70000)
    p3 = u / "roms/ps3"; p3.mkdir(parents=True)
    (p3 / "UP9000-BCUS98174_00-LASTOFUSPATCH111-A0111-V0100-PE.pkg").write_bytes(ps3pkg("UP9000-BCUS98174_00-LASTOFUSPATCH111", 4))
    (p3 / "some-addon.pkg").write_bytes(ps3pkg("UP9000-BCUS98174_00-LEFTBEHIND000001", 4))
    w = u / "roms/wii"; w.mkdir(parents=True)
    (w / "Guitar Hero World Tour - Song (Update).wad").write_bytes(wad(0x00010005735A4145, 2))
    (w / "GH song 2.wad").write_bytes(wad(0x00010005735A4145, 2) + b"different song")
    mg = p4 / "CUSA05555"; (mg / "sce_sys").mkdir(parents=True); (mg / "eboot.bin").write_bytes(b"e")
    (mg / "sce_sys/param.sfo").write_bytes(sfo({"CATEGORY": "gp", "TITLE_ID": "CUSA05555", "TITLE": "Merged", "APP_VER": "01.07"}))
    (w / "Copter Crisis (USA) (WiiWare).wad").write_bytes(wad(0x0001000157435045, 0))
    iso = bytearray(0x400); iso[:6] = b"SXAE52"; iso[0x18:0x1C] = b"\x5D\x1C\x9E\xA3"; (w / "Guitar Hero World Tour.iso").write_bytes(bytes(iso))
    mkw = s2 / "Donkey Kong Bananza [loose]"
    for sub in ("base/exefs", "updates/131072/exefs", "dlc/0400AAAA00011001/0/romfs", "base/romfs/a/b"):
        (mkw / sub).mkdir(parents=True)
    (mkw / "base/exefs/main.npdm").write_bytes(npdm(0x0400AAAA00010000)); (mkw / "updates/131072/exefs/main.npdm").write_bytes(npdm(0x0400AAAA00010000))
    for i in range(300): (mkw / ("base/romfs/a/b/f%d.bin" % i)).write_bytes(b"r")
    hd = r / "media/GAMES/roms"; (hd / "switch_update/dlc").mkdir(parents=True); (hd / "ps4").mkdir(parents=True)
    (hd / "switch_update/dlc/Smash DLC [01006A800016F0AA][v0].nsp").write_bytes(pfs0(["01006A800016F0AA000000000000000a.tik"]))
    (hd / "ps4/backup-dlc.pkg").write_bytes(ps4pkg("UP9000-CUSA09999_00-EXTRA00000000001", 0x1B, 0))
    (r / "media/unplugged/roms/ps4").mkdir(parents=True); (r / "media/unplugged/roms/ps4/x.pkg").write_bytes(ps4pkg("UP9000-CUSA00001_00-X000000000000001", 0x1B, 0))
    (r / "mounts").write_text("/dev/sdb1 /media/GAMES ntfs rw 0 0\n")
    env = dict(os.environ, GCM_SCAN_ROOT=str(r), GCM_MOUNTS=str(r / "mounts"))
    out = subprocess.run([sys.executable, "-B", str(INS)], capture_output=True, text=True, env=env)
    print(out.stdout[-3500:], out.stderr[-1500:])
    res = json.loads((reg / "content-inspection.json").read_text()); rows = res["content"]
    f = lambda **k: [x for x in rows if all(x.get(a) == b for a, b in k.items())]
    check("Switch 2 NUP base found (Mario Kart World)", len(f(console="switch2", kind="base", title_id="0400C3F00006E000")) == 1)
    upd = f(console="switch2", kind="update", title_id="0400C3F00006E800")
    check("its update found with version and display version, matched to the game", len(upd) == 1 and upd[0]["version"] == 131072 and upd[0]["display_version"] == "2.0.0" and upd[0]["game"] == "Mario Kart World")
    check("its DLC found and matched to the game", len(f(console="switch2", kind="dlc", game_id="0400C3F00006E000")) == 1)
    oot = f(console="switch2", kind="update", title_id="0400486024DEC800")
    check("standalone update NUP recognised", len(oot) == 1 and oot[0]["standalone_update"])
    check("damaged NUP reported as unreadable (checksum)", len(f(console="switch2", kind="unreadable")) == 1)
    nsp = f(console="switch", kind="update", title_id="0100152000022800")
    check("renamed NSP identified from its ticket (not its name)", len(nsp) == 1 and "ticket" in nsp[0]["evidence"][0] and nsp[0]["game_id"] == "0100152000022000")
    sm = f(console="switch", kind="update", title_id="01006A800016E800")
    check("the same update in two places is listed once, with its copy", len(sm) == 1 and len(sm[0].get("copies", [])) == 1)
    check("XCI cartridge is a base game", len(f(console="switch", kind="base", title_id="0100152000022000")) == 1)
    check("update in Citron's external content folder found", len(f(console="switch", kind="update", title_id="01006A800016E800")) == 1)
    check("unknown format with an update tag listed as possible-update", len(f(kind="possible-update")) == 1)
    check("unknown file with no evidence ignored", not any("random-notes" in x["file"] for x in rows))
    check("launcher stubs (.switch2) are not content", not any(x["file"].endswith(".switch2") for x in rows))
    check("Xbox 360 DLC package identified", len(f(console="xbox360", kind="dlc", game_id="4D5307E6")) == 1)
    check("Batocera console GCM does not manage (snes) is skipped", "snes" not in res["consoles"])
    check("PS4 base game from its param.sfo", len(f(console="ps4", kind="base", content_id="UP9000-CUSA03627_00-LASTGUARDIANUS00")) == 1)
    check("renamed PS4 patch identified from its header flags", len(f(console="ps4", kind="update", game_id="CUSA03627")) == 1 and "header" in f(console="ps4", kind="update", game_id="CUSA03627")[0]["evidence"][0])
    check("PS4 DLC from its content type", len(f(console="ps4", kind="dlc", game_id="CUSA08034")) == 1)
    check("tiny DLC.sh.keys ignored", not any("DLC.sh.keys" in x["file"] for x in rows))
    check("unpacked PS4 game folder is one base item", [x["kind"] for x in rows if x["file"].endswith("roms/ps4/CUSA00870")] == ["base"])
    check("unpacked PS4 update folder is one update item, matched to the game", [x.get("game") for x in rows if x["file"].endswith("CUSA00870-UPDATE")] == ["Tales"])
    check("loose files in an update folder grouped into one row", [x.get("files") for x in rows if x["file"].endswith("updates/SomeGame-patch")] == [20])
    check("no row for each loose file inside unpacked folders", not any("ttarch2" in x["file"] or "chunk" in x["file"] for x in rows))
    check("PS3 patch package is an update (01.11)", [x.get("version") for x in f(console="ps3", kind="update")] == ["01.11"])
    check("PS3 game data without patch markers is DLC", len(f(console="ps3", kind="dlc")) == 1)
    check("Wii DLC WAD from its title type, not its name; two songs with one title ID stay separate", len(f(console="wii", kind="dlc", title_id="00010005735A4145")) == 2)
    check("PS4 game folder that already includes an update is a game", [x["kind"] for x in rows if x["file"].endswith("ps4/CUSA05555")] == ["base"])
    check("update or DLC without its game is marked", any("no matching game" in e for x in f(console="ps4", game_id="CUSA08034") for e in x["evidence"]))
    check("WiiWare WAD is a base title", len(f(console="wii", kind="base", title_id="0001000157435045")) == 1)
    check("Wii disc image read for its game ID", len(f(console="wii", kind="base", game_id="SXAE")) == 1)
    lk = [x for x in rows if "[loose]" in x["file"]]
    check("Switch 2 loose dump: base, update 131072 and DLC read as 3 items", sorted((x["kind"], str(x.get("version"))) for x in lk) == [("base", "None"), ("dlc", "None"), ("update", "131072")])
    check("loose dump update title ID from main.npdm", any(x.get("title_id") == "0400AAAA00010800" for x in lk))
    check("loose dump romfs files not counted one by one", res["consoles"]["switch2"]["files"] < 50 and not res["consoles"]["switch2"]["stopped"])
    check("loose DLC matched to its game", any(x.get("kind") == "dlc" and x.get("game") == "Donkey Kong Bananza" for x in lk))
    hdd = [x for x in rows if x.get("drive") == "GAMES"]
    check("connected backup drive: its Switch DLC and PS4 DLC are found and marked", sorted((x["console"], x["kind"]) for x in hdd) == [("ps4", "dlc"), ("switch", "dlc")] and all(any("copy it to GCM" in e for e in x["evidence"]) for x in hdd))
    check("a folder under /media that is not a mounted drive is ignored", not any("unplugged" in x["file"] for x in rows))
    before = (reg / "content-inspection.json").read_text()
    out = subprocess.run([sys.executable, "-B", str(INS), "--dry-run"], capture_output=True, text=True, env=env)
    check("--dry-run writes nothing", "DRY RUN" in out.stdout and (reg / "content-inspection.json").read_text() == before)
print("ALL INSPECTOR FIXTURES PASS" if not fails else "FAILED: %s" % fails)
sys.exit(1 if fails else 0)
