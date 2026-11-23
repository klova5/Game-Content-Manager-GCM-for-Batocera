"""Fixtures for gcm-emulator-scan.py in a scratch Batocera layout."""
import json, os, subprocess, sys, tempfile
from pathlib import Path
SCAN = Path(sys.argv[1]).resolve(); fails = []
def check(n, c):
    print(('PASS ' if c else 'FAIL ') + n, flush=True)
    if not c: fails.append(n)
def system(name, *emus):
    return "<system><fullname>%s</fullname><name>%s</name><emulators>%s</emulators></system>" % (name, name, "".join(
        '<emulator name="%s"><cores>%s</cores></emulator>' % (e, "".join('<core>%s</core>' % c for c in cores)) for e, cores in emus))
with tempfile.TemporaryDirectory() as t:
    r = Path(t); u = r / "userdata/system"
    (r / "usr/share/emulationstation").mkdir(parents=True); (r / "usr/share/batocera").mkdir(parents=True)
    (r / "usr/share/batocera/batocera.version").write_text("44 2026/11/16 12:25\n")
    (r / "usr/share/emulationstation/es_systems.cfg").write_text("<systemList>%s</systemList>" % "".join([
        system("ps4", ("shadps4", ["shadps4"])),
        system("3ds", ("azahar", ["azahar"]), ("libretro", ["citra"]), ("lime3ds", ["lime3ds"])),
        system("xbox", ("xemu", ["xemu"])),
        system("snes", ("libretro", ["snes9x"]))]))
    (u / "configs/emulationstation").mkdir(parents=True)
    (u / "configs/emulationstation/es_systems_switch.cfg").write_text("<systemList>%s</systemList>" % system("switch",
        ("citron", ["citron"]), ("ryujinx", ["ryujinx"]), ("nextendo", ["nextendo"]), ("eden", ["eden"]),
        ("sudachi", ["sudachi"]), ("ryubing", ["ryubing"]), ("mystery", ["mystery"]), ("torzu", ["torzu"]), ("kenjinx", ["kenjinx"])))
    yuzu = u / "configs/yuzu"; yuzu.mkdir(parents=True)
    (yuzu / "qt-config.ini").write_text("[Data%20Storage]\nnand_directory=/userdata/system/configs/citron/nand\n[UI]\nPaths\\external_content_dirs\\size=1\n")
    (u / "configs/eden").symlink_to(yuzu)               # as on the author's device: Eden shares Citron's folder
    (u / "configs/sudachi").mkdir()                     # name hints yuzu, but no settings files
    torzu = u / "configs/torzu"; torzu.mkdir()          # a yuzu fork with its own settings and storage
    (torzu / "qt-config.ini").write_text((yuzu / "qt-config.ini").read_text())
    ryujinx = u / "configs/Ryujinx"; (ryujinx / "games").mkdir(parents=True)
    (ryujinx / "Config.json").write_text('{"game_dirs": []}')
    (u / "configs/kenjinx").symlink_to(ryujinx)         # a Ryujinx fork sharing Ryujinx's folder
    ryu = u / "configs/Ryubing"; (ryu / "bis").mkdir(parents=True)
    (ryu / "Config.json").write_text(json.dumps({"version": 60, "game_dirs": ["/userdata/roms/switch"]}))
    (r / "userdata/saves/3ds/lime3ds/sdmc/Nintendo 3DS").mkdir(parents=True)
    # Switch 2 add-on as on the author's device: own console, launch command, Lichii files deep in the add-on
    (u / "configs/emulationstation/es_systems_switch2.cfg").write_text(
        "<systemList><system><name>switch2</name><path>/userdata/roms/switch2</path>"
        "<command>/userdata/system/add-ons/switch2/eglantine/launch-eglantine.sh %ROM% %EMULATOR%</command>"
        "<emulators><emulator name=\"lichii\"><cores><core>lichii</core></cores></emulator>"
        "<emulator name=\"lichii-366\"><cores><core>lichii-366</core></cores></emulator></emulators></system></systemList>")
    lic = u / "add-ons/switch2/eglantine/master/isolated/config/lichii"; lic.mkdir(parents=True)
    (u / "add-ons/switch2/eglantine/launch-eglantine.sh").write_text("#!/bin/bash\n")
    (lic / "qt-config.ini").write_text("[Data%20Storage]\nnand_directory=/x/nand\n[UI]\nPaths\\external_content_dirs\\size=0\n")
    # an add-on console with only a launch command and nothing GCM recognises
    (u / "configs/emulationstation/es_systems_retro2.cfg").write_text(
        "<systemList><system><name>retro2</name><command>/userdata/system/add-ons/retro2/run.sh %ROM%</command></system></systemList>")
    (u / "add-ons/retro2").mkdir(parents=True); (u / "add-ons/retro2/run.sh").write_text("#!/bin/bash\n")
    env = dict(os.environ, GCM_SCAN_ROOT=str(r))
    out = subprocess.run([sys.executable, "-B", str(SCAN)], capture_output=True, text=True, env=env)
    print(out.stdout[-2500:], out.stderr[-1500:])
    reg = json.loads((u / "add-ons/game-content-manager/emulator-registry.json").read_text())
    st = {(e["system"], e["emulator"]): e for e in reg["emulators"]}
    s = lambda sy, em: st.get((sy, em), {}).get("status")
    check("Citron and Ryujinx are built-in", s("switch", "citron") == s("switch", "ryujinx") == "built-in")
    check("shadPS4 and Azahar are built-in", s("ps4", "shadps4") == s("3ds", "azahar") == "built-in")
    check("a libretro core known by name (citra) is built-in", s("3ds", "libretro") == "built-in")
    check("Eden (linked to Citron's folder) is compatible and routed as citron-emu",
          s("switch", "eden") == "compatible" and st[("switch", "eden")]["route_as"] == "citron-emu")
    check("Torzu (yuzu fork with its own settings) is found but not enabled",
          s("switch", "torzu") == "compatible-own-storage" and st[("switch", "torzu")]["route_as"] is None)
    check("Kenjinx (linked to Ryujinx's folder) is compatible and routed as ryujinx-emu",
          s("switch", "kenjinx") == "compatible" and st[("switch", "kenjinx")]["route_as"] == "ryujinx-emu")
    check("Sudachi with no settings files stays default (a name alone is not enough)", s("switch", "sudachi") == "default")
    check("Ryubing (Ryujinx fork with its own storage) is found but not enabled", s("switch", "ryubing") == "compatible-own-storage")
    check("an unknown emulator with nothing on disk stays default", s("switch", "mystery") == "default")
    check("Lime3DS (own 3DS storage) is found but not enabled", s("3ds", "lime3ds") == "compatible-own-storage")
    check("built-in emulators are routed under their own name", st[("switch", "citron")]["route_as"] == "citron")
    check("nextendo is excluded", s("switch", "nextendo") == "excluded")
    check("consoles GCM does not manage are not listed", ("snes", "libretro") not in st and ("xbox", "xemu") not in st)
    for emu in ("lichii", "lichii-366"):
        row = st.get(("switch2", emu), {})
        check("Switch 2 add-on console: %s is supported by GCM's Switch 2 backend" % emu,
              row.get("status") == "built-in" and row.get("family") == "lichii" and row.get("backend") == "switch2")
    row = st.get(("retro2", "(launch command)"), {})
    check("an add-on console with only a launch command is listed, with no family", row.get("status") == "new-console" and row.get("family") is None)
    check("report has a section for new add-on consoles", "ADD-ON CONSOLES AND EMULATORS" in (u / "logs/gcm-emulator-scan.txt").read_text())
    check("report written", (u / "logs/gcm-emulator-scan.txt").is_file())
    (u / "add-ons/game-content-manager/emulator-registry-overrides.json").write_text('{"eden": "off", "mystery": "ryujinx"}')
    subprocess.run([sys.executable, "-B", str(SCAN)], capture_output=True, text=True, env=env)
    st = {(e["system"], e["emulator"]): e for e in json.loads((u / "add-ons/game-content-manager/emulator-registry.json").read_text())["emulators"]}
    check("an override turns Eden off", st[("switch", "eden")]["status"] == "default")
    check("an override naming a family still needs proof on disk (mystery stays default)", st[("switch", "mystery")]["status"] == "default")
    before = (u / "add-ons/game-content-manager/emulator-registry.json").read_text()
    out = subprocess.run([sys.executable, "-B", str(SCAN), "--dry-run"], capture_output=True, text=True, env=env)
    check("--dry-run writes nothing", "DRY RUN" in out.stdout and (u / "add-ons/game-content-manager/emulator-registry.json").read_text() == before)
print("ALL SCANNER FIXTURES PASS" if not fails else "FAILED: %s" % fails)
sys.exit(1 if fails else 0)
