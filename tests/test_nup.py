import hashlib, importlib.util, json, os, struct, subprocess, sys, tempfile
from pathlib import Path
import zstandard
TOOL = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location("gcmnup", TOOL); N = importlib.util.module_from_spec(spec); spec.loader.exec_module(N)
fails = []
def check(n, c):
    print(("PASS " if c else "FAIL ") + n, flush=True)
    if not c: fails.append(n)
def run(*a):
    r = subprocess.run([sys.executable, "-B", str(TOOL), *map(str, a)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()
def npdm(pid):
    d = bytearray(0x200); d[:4] = b"META"; struct.pack_into("<I", d, 0x70, 0x100); d[0x100:0x104] = b"ACI0"
    struct.pack_into("<Q", d, 0x110, pid); return bytes(d)
def dump(root, pid=None, files=None):
    if pid is not None:
        (root / "exefs").mkdir(parents=True); (root / "exefs/main.npdm").write_bytes(npdm(pid)); (root / "exefs/main").write_bytes(os.urandom(5000))
    for rel, data in (files or {}).items():
        p = root / "romfs" / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
def contents(path):
    """{package key: {path: sha}} by decoding every member, as Lichii would see them."""
    n = N.Nup(path); out = {}
    with open(path, "rb") as f:
        for i, p in enumerate(n.packages):
            m = {}
            def walk(d, pre):
                name, first, cc = n.dirs[d]
                for c in n.children[first:first + cc]:
                    if c < len(n.dirs):
                        walk(c, pre + n.dirs[c][0] + "/")
                    else:
                        fr = n.files[c - len(n.dirs)]
                        f.seek(fr["offset"]); raw = f.read(fr["extent"]) if fr["size"] else b""
                        data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=fr["size"]) if fr["codec"] == 1 else raw
                        m[pre + fr["name"]] = hashlib.sha256(data).hexdigest()
            walk(p["root"], "")
            out[(p["kind"], p["title_id"], p["version"])] = m
    return out
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    base_files = {"Data/a.bin": os.urandom(70000), "Data/empty.txt": b"", "Movie/b.dat": os.urandom(300000), "c.bin": b"hello"}
    dump(t / "base", 0x0400AAAA00010000, base_files)
    code, out = run("pack", t / "base", t / "game.nup", "--kind", "base", "--display-version", "1.0.0", "--title", "Test Game")
    check("pack a base game from a loose dump: " + out, code == 0)
    code, out = run("verify", t / "game.nup", "--full"); check("packed game passes the full check", code == 0)
    d = json.loads(run("list", t / "game.nup")[1])
    check("listed as base 0400AAAA00010000 'Test Game' 1.0.0", d[0]["kind"] == "base" and d[0]["title_id"] == "0400AAAA00010000" and d[0]["title"] == "Test Game" and d[0]["display_version"] == "1.0.0")
    # make the game look like a real one: recompress one member with zstd (codec 1), as Lichii does
    n = N.Nup(t / "game.nup"); specs = [n.package_spec(0)]
    zfile = t / "z.bin"; zdata = os.urandom(1000) * 200; zfile.write_bytes(zstandard.ZstdCompressor().compress(zdata))
    specs[0]["tree"][2].append(("file", "Packed.bin", {"from": zfile, "offset": 0, "extent": zfile.stat().st_size, "size": len(zdata), "sha": hashlib.sha256(zdata).digest(), "codec": 1}))
    # zfile isn't 16-aligned but offset 0 is fine as a copy source
    N.write_nup(specs, t / "real.nup"); code, out = run("verify", t / "real.nup", "--full")
    check("game with a zstd member passes the full check: " + out, code == 0)
    before = contents(t / "real.nup")
    dump(t / "upd", 0x0400AAAA00010000, {"Data/a.bin": os.urandom(80000), "Data/new.bin": os.urandom(1234)})
    check("pack an update", run("pack", t / "upd", t / "upd.nup", "--kind", "update", "--version", "131072", "--display-version", "1.1.0")[0] == 0)
    dump(t / "dlc", None, {"Dlc/x.bin": os.urandom(4096)})
    check("DLC without a title ID is refused", run("pack", t / "dlc", t / "nodlc.nup", "--kind", "dlc")[0] == 1)
    check("pack a DLC", run("pack", t / "dlc", t / "dlc.nup", "--kind", "dlc", "--title-id", "0400AAAA00011001")[0] == 0)
    upd_c, dlc_c = contents(t / "upd.nup"), contents(t / "dlc.nup")
    code, out = run("add", t / "real.nup", t / "upd.nup", t / "dlc.nup"); check("add update and DLC to the game: " + out, code == 0)
    code, out = run("verify", t / "real.nup", "--full"); check("game with update and DLC passes the full check", code == 0)
    after = contents(t / "real.nup")
    check("game content is byte-identical after adding (zstd member copied as-is)", after[(0, 0x0400AAAA00010000, 0)] == before[(0, 0x0400AAAA00010000, 0)])
    check("added update and DLC content identical to their package files", after[(1, 0x0400AAAA00010800, 131072)] == list(upd_c.values())[0] and after[(2, 0x0400AAAA00011001, 0)] == list(dlc_c.values())[0])
    check("no leftover work file", not any(p.name.endswith(".gcm-part") for p in t.iterdir()))
    d = json.loads(run("list", t / "real.nup")[1])
    ui = [x["index"] for x in d if x["kind"] == "update"][0]
    check("split the update out to its own file", run("split", t / "real.nup", ui, t / "store-upd.nup")[0] == 0 and contents(t / "store-upd.nup") == {(1, 0x0400AAAA00010800, 131072): after[(1, 0x0400AAAA00010800, 131072)]})
    size_with = (t / "real.nup").stat().st_size
    code, out = run("remove", t / "real.nup", ui); check("remove the update: " + out, code == 0)
    now = contents(t / "real.nup")
    check("after remove: base and DLC unchanged, update gone, file smaller", set(now) == {(0, 0x0400AAAA00010000, 0), (2, 0x0400AAAA00011001, 0)} and now[(0, 0x0400AAAA00010000, 0)] == before[(0, 0x0400AAAA00010000, 0)] and (t / "real.nup").stat().st_size < size_with)
    check("re-add from the stored copy restores it", run("add", t / "real.nup", t / "store-upd.nup")[0] == 0 and contents(t / "real.nup") == after)
    check("adding the same update again replaces it (no duplicate)", run("add", t / "real.nup", t / "store-upd.nup")[0] == 0 and len(N.Nup(t / "real.nup").packages) == 3)
    bi = [x["index"] for x in json.loads(run("list", t / "real.nup")[1]) if x["kind"] == "base"][0]
    code, out = run("remove", t / "real.nup", bi, [x["index"] for x in json.loads(run("list", t / "real.nup")[1]) if x["kind"] == "update"][0])
    check("removing base and update together is refused (game would not start)", code == 1 and contents(t / "real.nup") == after)
    check("adding a second base game is refused", run("add", t / "real.nup", t / "game.nup")[0] == 1)
    bad = bytearray((t / "real.nup").read_bytes()); bad[0x90] ^= 1; (t / "bad.nup").write_bytes(bad)
    check("damaged file is refused", run("add", t / "bad.nup", t / "dlc.nup")[0] == 1 and (t / "bad.nup").read_bytes() == bytes(bad))
    code, out = run("remove", t / "real.nup", [x["index"] for x in json.loads(run("list", t / "real.nup")[1]) if x["kind"] == "dlc"][0], "--out", t / "copy.nup")
    check("--out writes a separate copy and leaves the game alone: " + out, code == 0 and "compared byte for byte" in out and contents(t / "real.nup") == after and len(contents(t / "copy.nup")) == 2)
    # shared data: two packages pointing at the same bytes stay shared and are copied once
    g = N.Nup(t / "real.nup"); sp = [g.package_spec(i) for i in range(len(g.packages))]
    import copy as _c
    twin = _c.deepcopy([x for x in sp if x["kind"] == 1][0]); twin["version"] = 196608
    sp2 = sp + [twin]; N.write_nup(sp2, t / "shared.nup")
    check("packages sharing data stay shared (file grows only by the table)", (t / "shared.nup").stat().st_size < (t / "real.nup").stat().st_size + 64 * 1024 and run("verify", t / "shared.nup", "--full")[0] == 0)
    # a copied range that differs is caught
    _o, runs = N.write_nup(sp, t / "cmp.nup")
    raw = bytearray((t / "cmp.nup").read_bytes()); raw[runs[0][3] + 5] ^= 0xFF; (t / "cmp.nup").write_bytes(raw)
    try:
        N.check_copies(t / "cmp.nup", runs); caught = False
    except N.NupError:
        caught = True
    check("byte comparison catches a damaged copy", caught)
    # failure while writing leaves the original untouched
    orig = (t / "real.nup").read_bytes()
    os.chmod(t / "upd", 0o755); (t / "upd/romfs/Data/new.bin").unlink()
    st = os.statvfs(t)
    real_free = N.free_space; N.free_space = lambda f: 0
    try:
        N.replace_game(t / "real.nup", [N.Nup(t / "real.nup").package_spec(0)], 1); low = False
    except N.NupError as e:
        low = "free space" in str(e)
    N.free_space = real_free
    check("not enough space: stops before writing, game unchanged", low and (t / "real.nup").read_bytes() == orig)
print("ALL NUP ENGINE TESTS PASS" if not fails else "FAILED: %s" % fails); sys.exit(1 if fails else 0)
