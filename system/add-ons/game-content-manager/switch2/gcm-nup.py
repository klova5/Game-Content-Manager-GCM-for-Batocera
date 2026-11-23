#!/usr/bin/env python3
"""GCM NUP tool: reads, checks and rewrites Lichii NUP containers (Switch 2 games) so GCM can add and
remove updates and DLC. Existing game data is copied byte for byte (never decoded or re-encoded); new
content is stored uncompressed. A changed game file is written next to the original, checked, and only
then put in its place, so the original stays untouched if anything fails.

  gcm-nup.py list   <file.nup>                          packages as JSON
  gcm-nup.py verify <file.nup> [--full]                 structure and TOC checksum; --full also hashes every file
  gcm-nup.py pack   <dump folder> <out.nup> --kind base|update|dlc [--title-id ID] [--version N] [--display-version V]
                                                        one package from a loose dump (exefs/ and romfs/)
  gcm-nup.py add    <game.nup> <package.nup>...         copy every package of each package file into the game
  gcm-nup.py split  <game.nup> <index> <out.nup>        copy one package of the game into its own file
  gcm-nup.py remove <game.nup> <index>...               remove packages from the game
Every command that changes a file prints one line starting PASS or STOP.
"""
import hashlib, json, os, struct, sys, uuid
from pathlib import Path

SUPER = 0x80
PKG_REC, DIR_REC, FILE_REC, TOC_HDR, TLV_HDR = 0x40, 0x18, 0x50, 0x30, 8
TOC_MAGIC = 0x01434F54
NODIR = 0xFFFFFFFF
KINDS = {0: "base", 1: "update", 2: "dlc"}
KIND_IDS = {v: k for k, v in KINDS.items()}
RAW_NACP = 0x4000
EMPTY_SHA = hashlib.sha256(b"").digest()
COPY_GAP = 1 << 20  # neighbouring extents closer than this are copied as one run
CHUNK = 8 << 20


class NupError(Exception):
    pass


def align(n, a=16):
    return (n + a - 1) // a * a


# ---------------------------------------------------------------- reading
class Nup:
    """A parsed container, validated with the same rules as Lichii's reader (NupContainer::Parse)."""

    def __init__(self, path):
        self.path = Path(path)
        size = self.path.stat().st_size
        with open(self.path, "rb") as f:
            sb = f.read(SUPER)
            if len(sb) < SUPER or sb[:4] != b"NUP\x01":
                raise NupError("not a NUP file")
            hs, hv, toc_off, toc_size = struct.unpack_from("<HHQQ", sb, 4)
            toc_sha = sb[0x18:0x38]
            blob, fsize, pcount, flags = struct.unpack_from("<QQII", sb, 0x38)
            if hs < SUPER or flags != 0:
                raise NupError("NUP header is not valid")
            if fsize != size or toc_off < SUPER or blob > fsize or toc_off > blob or toc_size > blob - toc_off or blob % 16:
                raise NupError("NUP header sizes do not match the file")
            f.seek(toc_off)
            toc = f.read(toc_size)
        if len(toc) != toc_size or hashlib.sha256(toc).digest() != toc_sha:
            raise NupError("NUP table of contents does not match its checksum")
        magic, count, dcount, fcount, pool_off, pool_size, _r = struct.unpack_from("<IIQQQQQ", toc, 0)
        if magic != TOC_MAGIC or count == 0 or count != pcount:
            raise NupError("NUP table of contents is not valid")
        if dcount > len(toc) // DIR_REC or fcount > len(toc) // FILE_REC:
            raise NupError("NUP table counts are not valid")
        needed = TOC_HDR + count * PKG_REC + dcount * DIR_REC + fcount * FILE_REC
        if needed > len(toc) or pool_off < needed or pool_off > len(toc) or pool_size > len(toc) - pool_off or (pool_off - needed) % 4:
            raise NupError("NUP table layout is not valid")
        self.header_version, self.blob_offset, self.size = hv, blob, size
        self.toc_offset, self.toc_size = toc_off, toc_size
        pool = toc[pool_off:pool_off + pool_size]
        nchildren = (pool_off - needed) // 4
        pos = TOC_HDR
        self.packages = []
        for _ in range(count):
            tid, root, exefs, romfs, ver, kind, pflags, _a, _b, moff, msize = struct.unpack_from("<QIIIIBBHIQI", toc, pos)
            pos += PKG_REC
            if kind > 2 or pflags & ~1 or root >= dcount or (exefs != NODIR and exefs >= dcount) or (romfs != NODIR and romfs >= dcount):
                raise NupError("NUP package record is not valid")
            if not (msize > 0 and moff != 0 and moff <= size and msize <= size - moff):
                moff = msize = 0
            self.packages.append({"title_id": tid, "root": root, "exefs": exefs, "romfs": romfs, "version": ver, "kind": kind,
                                  "flags": pflags, "meta_offset": moff, "meta_size": msize})
        self.dirs = []
        for _ in range(dcount):
            noff, nlen, first, cc, _r = struct.unpack_from("<IIIIQ", toc, pos)
            pos += DIR_REC
            if first + cc > nchildren or noff + nlen > pool_size:
                raise NupError("NUP folder record is not valid")
            self.dirs.append((pool[noff:noff + nlen].decode("utf-8", "surrogateescape"), first, cc))
        self.files = []
        for _ in range(fcount):
            noff, nlen, fsz, eoff, esz = struct.unpack_from("<IIQQQ", toc, pos)
            sha = toc[pos + 0x20:pos + 0x40]
            codec, cflags = toc[pos + 0x40], toc[pos + 0x41]
            pos += FILE_REC
            if codec > 1 or cflags or (fsz == 0 and (eoff or esz)) or noff + nlen > pool_size or \
                    (fsz and (esz == 0 or eoff < blob or eoff % 16 or eoff > size or esz > size - eoff)):
                raise NupError("NUP file record is not valid")
            self.files.append({"name": pool[noff:noff + nlen].decode("utf-8", "surrogateescape"), "size": fsz,
                               "offset": eoff, "extent": esz, "sha": sha, "codec": codec})
        self.children = list(struct.unpack_from("<%dI" % nchildren, toc, needed))
        if any(c >= dcount + fcount for c in self.children):
            raise NupError("NUP folder entry is not valid")
        self._check_tree()
        with open(self.path, "rb") as f:
            for p in self.packages:
                p["meta"] = b""
                if p["meta_offset"]:
                    f.seek(p["meta_offset"])
                    p["meta"] = f.read(p["meta_size"])
                p.update(parse_meta(p["meta"]))

    def _check_tree(self):
        state, height = [0] * len(self.dirs), [0] * len(self.dirs)
        for start in range(len(self.dirs)):
            if state[start]:
                continue
            state[start] = 1
            stack = [[start, 0]]
            while stack:
                d, nxt = stack[-1]
                _n, first, cc = self.dirs[d]
                if nxt == cc:
                    state[d] = 2
                    stack.pop()
                    if stack:
                        height[stack[-1][0]] = max(height[stack[-1][0]], height[d] + 1)
                    continue
                stack[-1][1] += 1
                c = self.children[first + nxt]
                if c >= len(self.dirs):
                    continue
                if state[c] == 1:
                    raise NupError("NUP folder tree has a cycle")
                if len(stack) + height[c] + 1 > 128:
                    raise NupError("NUP folder tree is nested too deeply")
                if state[c] == 2:
                    height[d] = max(height[d], height[c] + 1)
                else:
                    state[c] = 1
                    stack.append([c, 0])

    def tree(self, d):
        """('dir', name, [children]) built from folder d; files are ('file', name, extent source)."""
        name, first, cc = self.dirs[d]
        out = []
        for c in self.children[first:first + cc]:
            if c < len(self.dirs):
                out.append(self.tree(c))
            else:
                fr = self.files[c - len(self.dirs)]
                out.append(("file", fr["name"], {"from": self.path, "offset": fr["offset"], "extent": fr["extent"],
                                                 "size": fr["size"], "sha": fr["sha"], "codec": fr["codec"]}))
        return ("dir", name, out)

    def package_spec(self, i):
        """Everything needed to write package i into another container."""
        p = self.packages[i]
        root = self.tree(p["root"])
        exefs = romfs = None
        # exefs/romfs are folders of the root tree (or the root itself); remember them by path
        paths = {}

        def walk(node, path, d_index):
            paths[d_index] = path
            _n, first, cc = self.dirs[d_index]
            for c in self.children[first:first + cc]:
                if c < len(self.dirs):
                    walk(None, path + (self.dirs[c][0],), c)
        walk(root, (), p["root"])
        if p["exefs"] != NODIR:
            exefs = paths.get(p["exefs"])
            if exefs is None:
                raise NupError("package %d: its exefs folder is outside its root folder" % i)
        if p["romfs"] != NODIR:
            romfs = paths.get(p["romfs"])
            if romfs is None:
                raise NupError("package %d: its romfs folder is outside its root folder" % i)
        return {"title_id": p["title_id"], "version": p["version"], "kind": p["kind"], "flags": p["flags"],
                "tree": root, "exefs": exefs, "romfs": romfs, "meta": p["meta"]}

    def describe(self):
        out = []
        for i, p in enumerate(self.packages):
            nfiles, nbytes = 0, 0
            stack = [p["root"]]
            seen = set()
            while stack:
                d = stack.pop()
                if d in seen:
                    continue
                seen.add(d)
                _n, first, cc = self.dirs[d]
                for c in self.children[first:first + cc]:
                    if c < len(self.dirs):
                        stack.append(c)
                    else:
                        nfiles += 1
                        nbytes += self.files[c - len(self.dirs)]["size"]
            out.append({"index": i, "kind": KINDS[p["kind"]], "title_id": "%016X" % p["title_id"], "version": p["version"],
                        "standalone_update": bool(p["flags"] & 1), "title": p.get("title_name"),
                        "display_version": p.get("display_version"), "files": nfiles, "bytes": nbytes,
                        "root": self.dirs[p["root"]][0]})
        return out


def parse_meta(block):
    out, pos = {}, 0
    while pos + TLV_HDR <= len(block):
        typ, _r, ln = struct.unpack_from("<HHI", block, pos)
        pos += TLV_HDR
        if pos + ln > len(block):
            raise NupError("NUP package metadata is not valid")
        data = block[pos:pos + ln]
        pos += ln
        if typ == 5:
            out["title_name"] = data.decode("utf-8", "replace")
        elif typ == 4:
            out["display_version"] = data.decode("utf-8", "replace")
        elif typ == 1:
            out["npdm"] = data
    return out


def tlv(typ, data):
    return struct.pack("<HHI", typ, 0, len(data)) + data


# ---------------------------------------------------------------- loose dumps
def child_ci(path, name):
    for e in sorted(os.listdir(path)):
        if e.lower() == name.lower():
            return path / e
    return None


def disk_tree(path, name):
    items = []
    for e in sorted(os.scandir(path), key=lambda e: e.name):
        if e.name.startswith(".") or e.is_symlink():
            continue
        if e.is_dir():
            items.append(disk_tree(Path(e.path), e.name))
        elif e.is_file():
            items.append(("file", e.name, {"path": Path(e.path), "size": e.stat().st_size}))
    return ("dir", name, items)


def npdm_program_id(data):
    if data[:4] != b"META" or len(data) < 0x80:
        return None
    aci, = struct.unpack_from("<I", data, 0x70)
    if data[aci:aci + 4] != b"ACI0":
        return None
    return struct.unpack_from("<Q", data, aci + 0x10)[0]


def pack_spec(dump, kind, title_id=None, version=None, display_version=None, title=None):
    dump = Path(dump)
    if not dump.is_dir():
        raise NupError("dump folder not found: %s" % dump)
    exefs, romfs = child_ci(dump, "exefs"), child_ci(dump, "romfs")
    npdm = (child_ci(exefs, "main.npdm") if exefs else None)
    meta = b""
    if kind in ("base", "update"):
        if not exefs or not npdm:
            raise NupError("a game or update dump needs exefs/main.npdm")
        data = npdm.read_bytes()
        pid = npdm_program_id(data)
        if pid is None:
            raise NupError("exefs/main.npdm has no program ID")
        meta += tlv(1, data)
        title_id = title_id if title_id is not None else ((pid & ~0xFFF) | (0x800 if kind == "update" else 0))
    elif not romfs:
        raise NupError("a DLC dump needs a romfs folder")
    if title_id is None:
        raise NupError("the DLC title ID is needed (--title-id)")
    nacp = None
    for where in (dump, exefs):
        if where and child_ci(where, "control.nacp") and child_ci(where, "control.nacp").stat().st_size == RAW_NACP:
            nacp = child_ci(where, "control.nacp").read_bytes()
    if nacp:
        meta += tlv(2, nacp)
        if not display_version:
            display_version = nacp[0x3060:0x3070].split(b"\0")[0].decode("utf-8", "replace") or None
        if not title:
            title = nacp[0:0x200].split(b"\0")[0].decode("utf-8", "replace") or None
    if display_version:
        meta += tlv(4, display_version.encode())
    if title:
        meta += tlv(5, title.encode())
    meta += tlv(8, ("GCM: " + dump.name).encode())
    tree = disk_tree(dump, kind)
    return {"title_id": title_id, "version": int(version or 0), "kind": KIND_IDS[kind], "flags": 0, "tree": tree,
            "exefs": (exefs.name,) if exefs else None, "romfs": (romfs.name,) if romfs else None, "meta": meta}


# ---------------------------------------------------------------- writing
def pwrite_all(fd, data, offset):
    view = memoryview(data)
    while view:
        n = os.pwrite(fd, view, offset)
        view, offset = view[n:], offset + n


def copy_range(src_fd, dst_fd, src_off, length, dst_off):
    """Copies length bytes from src_off in src_fd to dst_off in dst_fd (in-kernel when possible)."""
    done = 0
    try:
        while done < length:
            n = os.copy_file_range(src_fd, dst_fd, length - done, src_off + done, dst_off + done)
            if n <= 0:
                break
            done += n
    except (OSError, AttributeError):
        pass
    while done < length:
        block = os.pread(src_fd, min(CHUNK, length - done), src_off + done)
        if not block:
            raise NupError("source ended early while copying")
        pwrite_all(dst_fd, block, dst_off + done)
        done += len(block)


def write_nup(specs, out, header_version=1):
    """Writes a container with the given packages to out (a new file). Returns the written path."""
    if not specs:
        raise NupError("a NUP file needs at least one package")
    dirs, files, children, pool, names = [], [], [], bytearray(), {}

    def name_ref(n):
        b = n.encode("utf-8", "surrogateescape")
        if b not in names:
            names[b] = len(pool)
            pool.extend(b)
        return names[b], len(b)

    records = []
    for spec in specs:
        index_of = {}

        def add_dir(node, path):
            di = len(dirs)
            dirs.append([node[1], 0, 0, node[2]])
            index_of[path] = di
            return di
        root = add_dir(spec["tree"], ())
        order = [(root, spec["tree"], ())]
        # breadth-first: every folder's children sit together in the children array
        k = 0
        while k < len(order):
            di, node, path = order[k]
            k += 1
            entries = []
            for ch in node[2]:
                if ch[0] == "dir":
                    cd = add_dir(ch, path + (ch[1],))
                    order.append((cd, ch, path + (ch[1],)))
                    entries.append(("d", cd))
                else:
                    files.append(ch)
                    entries.append(("f", len(files) - 1))
            dirs[di][1], dirs[di][2] = len(children), len(entries)
            children.extend(entries)
        exefs = index_of.get(tuple(spec["exefs"])) if spec.get("exefs") is not None else NODIR
        romfs = index_of.get(tuple(spec["romfs"])) if spec.get("romfs") is not None else NODIR
        if exefs is None or romfs is None:
            raise NupError("package exefs/romfs folder not found in its tree")
        records.append([spec, root, exefs, romfs])
    for d in dirs:
        d[0] = name_ref(d[0])
    fnames = [name_ref(f[1]) for f in files]
    P, D, F, C = len(records), len(dirs), len(files), len(children)
    pool_off = TOC_HDR + P * PKG_REC + D * DIR_REC + F * FILE_REC + C * 4
    toc_size = pool_off + len(pool)
    blob = align(SUPER + toc_size)

    # place extents: copied ones in runs (keeping their relative layout), new ones after
    placed = [None] * F
    cursor = blob
    copied = sorted((i for i in range(F) if "from" in files[i][2] and files[i][2]["size"]),
                    key=lambda i: (str(files[i][2]["from"]), files[i][2]["offset"]))
    runs, run = [], None
    for i in copied:
        s = files[i][2]
        if run and run["from"] == s["from"] and run["start"] <= s["offset"] <= run["end"] + COPY_GAP:
            run["end"] = max(run["end"], s["offset"] + s["extent"])
            run["files"].append(i)
        else:
            run = {"from": s["from"], "start": s["offset"], "end": s["offset"] + s["extent"], "files": [i]}
            runs.append(run)
    for r in runs:
        r["at"] = cursor
        for i in r["files"]:
            placed[i] = cursor + (files[i][2]["offset"] - r["start"])
        cursor = align(cursor + (r["end"] - r["start"]))
    new_files = [i for i in range(F) if "path" in files[i][2] and files[i][2]["size"]]
    for i in new_files:
        placed[i] = cursor
        cursor = align(cursor + files[i][2]["size"])
    metas = []
    for rec in records:
        m = rec[0]["meta"]
        metas.append(cursor if m else 0)
        cursor = align(cursor + len(m)) if m else cursor
    total = cursor

    out = Path(out)
    tmp_hashes = {}
    fd = os.open(out, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        for r in runs:
            src = os.open(r["from"], os.O_RDONLY)
            try:
                copy_range(src, fd, r["start"], r["end"] - r["start"], r["at"])
            finally:
                os.close(src)
        for i in new_files:
            s = files[i][2]
            h, done = hashlib.sha256(), 0
            with open(s["path"], "rb") as src:
                while True:
                    block = src.read(CHUNK)
                    if not block:
                        break
                    h.update(block)
                    pwrite_all(fd, block, placed[i] + done)
                    done += len(block)
            if done != s["size"]:
                raise NupError("%s changed size while it was being copied" % s["path"])
            tmp_hashes[i] = h.digest()
        for rec, off in zip(records, metas):
            if off:
                pwrite_all(fd, rec[0]["meta"], off)
        os.ftruncate(fd, total)
        toc = bytearray(struct.pack("<IIQQQQQ", TOC_MAGIC, P, D, F, pool_off, len(pool), 0))
        for (spec, root, exefs, romfs), moff in zip(records, metas):
            toc += struct.pack("<QIIIIBBHIQI", spec["title_id"], root, exefs, romfs, spec["version"], spec["kind"], spec["flags"],
                               0, 0, moff, len(spec["meta"]) if moff else 0) + b"\0" * 20
        for (noff_len, first, cc, _node) in dirs:
            toc += struct.pack("<IIIIQ", noff_len[0], noff_len[1], first, cc, 0)
        for i, f in enumerate(files):
            s = f[2]
            if "from" in s:
                size, ext, sha, codec = s["size"], s["extent"], s["sha"], s["codec"]
            else:
                size, ext, sha, codec = s["size"], s["size"], tmp_hashes.get(i, EMPTY_SHA), 0
            off = placed[i] if size else 0
            toc += struct.pack("<IIQQQ", fnames[i][0], fnames[i][1], size, off, ext if size else 0) + sha
            toc += bytes([codec, 0]) + b"\0" * 14
        for kind, idx in children:
            toc += struct.pack("<I", idx if kind == "d" else D + idx)
        toc += pool
        assert len(toc) == toc_size
        sb = b"NUP\x01" + struct.pack("<HHQQ", SUPER, header_version, SUPER, toc_size) + hashlib.sha256(toc).digest()
        sb += struct.pack("<QQII", blob, total, P, 0) + uuid.uuid4().bytes + b"\0" * 0x20
        pwrite_all(fd, sb + bytes(toc), 0)
        os.fsync(fd)
    finally:
        os.close(fd)
    return out, [(r["from"], r["start"], r["end"] - r["start"], r["at"]) for r in runs]


# ---------------------------------------------------------------- checks
def verify(path, full=False, expect=None):
    """Parses the file (Lichii's rules) and checks every member: copied/raw members by SHA-256 of their bytes
    when full, else a sample. expect: [(kind, title_id, version)] the packages it must contain."""
    nup = Nup(path)
    if expect is not None:
        have = sorted((p["kind"], p["title_id"], p["version"]) for p in nup.packages)
        if have != sorted(expect):
            raise NupError("the written file does not hold the expected packages")
    new_decoder = None
    try:
        from compression import zstd  # Python 3.14
        new_decoder = zstd.ZstdDecompressor
    except ImportError:
        try:
            import zstandard
            new_decoder = lambda: zstandard.ZstdDecompressor(max_window_size=1 << 31).decompressobj()
        except ImportError:
            pass
    members = [f for f in nup.files if f["size"]]
    if not full:
        members = members[:: max(1, len(members) // 64)]
    skipped = 0
    with open(path, "rb") as f:
        for m in members:
            if m["codec"] == 1 and not new_decoder:
                skipped += 1
                continue
            f.seek(m["offset"])
            h, left = hashlib.sha256(), m["extent"]
            dec = new_decoder() if m["codec"] == 1 else None
            out_len = 0
            while left:
                block = f.read(min(CHUNK, left))
                if not block:
                    raise NupError("%s: short read" % m["name"])
                left -= len(block)
                if dec:
                    block = dec.decompress(block)
                h.update(block)
                out_len += len(block)
            if out_len != m["size"] or h.digest() != m["sha"]:
                raise NupError("%s: contents do not match their checksum" % m["name"])
    return nup, len(members) - skipped, skipped


def check_copies(path, runs):
    """Compares every copied byte range of the new file with its source (no decoding needed)."""
    done = 0
    with open(path, "rb") as new:
        for src_path, start, length, at in runs:
            with open(src_path, "rb") as src:
                src.seek(start)
                new.seek(at)
                left = length
                while left:
                    n = min(CHUNK, left)
                    if src.read(n) != new.read(n):
                        raise NupError("a copied range differs from the original (at byte %d)" % (at + length - left))
                    left -= n
            done += length
    return done


def same_package(a, b):
    return a["kind"] == b["kind"] and a["title_id"] == b["title_id"] and a["version"] == b["version"]


def free_space(folder):
    st = os.statvfs(folder)
    return st.f_bavail * st.f_frsize


def replace_game(game, specs, header_version, out=None):
    """Writes specs to a new file beside game, checks it, then puts it in game's place (or at out)."""
    game = Path(game)
    target = Path(out) if out else game
    if out and target.exists():
        raise NupError("%s already exists" % target)
    need = sum(spec_bytes(s) for s in specs) + (64 << 20)
    if free_space(target.parent) < need:
        raise NupError("not enough free space: %.1f GB needed next to the game" % (need / 1e9))
    part = target.with_name("." + target.name + ".gcm-part")
    try:
        _p, runs = write_nup(specs, part, header_version)
        verify(part, full=True, expect=[(s["kind"], s["title_id"], s["version"]) for s in specs])
        copied = check_copies(part, runs)
        os.replace(part, target)
        return copied
    finally:
        if part.exists():
            part.unlink()


def spec_bytes(spec):
    total, stack = 0, [spec["tree"]]
    while stack:
        node = stack.pop()
        for ch in node[2]:
            if ch[0] == "dir":
                stack.append(ch)
            else:
                total += ch[2].get("extent", ch[2]["size"]) + 16
    return total + len(spec["meta"])


# ---------------------------------------------------------------- commands
def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    out_path = None
    if cmd in ("add", "remove") and "--out" in args:
        k = args.index("--out")
        out_path = args[k + 1]
        args = args[:k] + args[k + 2:]
    try:
        if cmd == "list":
            print(json.dumps(Nup(args[0]).describe(), indent=1))
        elif cmd == "verify":
            nup, checked, skipped = verify(args[0], full="--full" in args)
            print("PASS: %s is valid: %d packages, %d files; %d member checksums checked%s" % (
                Path(args[0]).name, len(nup.packages), len(nup.files), checked,
                ", %d compressed members not checked (no zstd module)" % skipped if skipped else ""))
        elif cmd == "pack":
            opts = dict(zip(args[2::2], args[3::2]))
            kind = opts.get("--kind")
            if kind not in KIND_IDS:
                raise NupError("--kind must be base, update or dlc")
            tid = int(opts["--title-id"], 16) if "--title-id" in opts else None
            spec = pack_spec(args[0], kind, tid, opts.get("--version"), opts.get("--display-version"), opts.get("--title"))
            out = Path(args[1])
            if out.exists():
                raise NupError("%s already exists" % out)
            part = out.with_name("." + out.name + ".gcm-part")
            try:
                write_nup([spec], part)
                verify(part, full=True)
                os.replace(part, out)
            finally:
                if part.exists():
                    part.unlink()
            print("PASS: packed %s %016X v%d into %s" % (kind, spec["title_id"], spec["version"], out))
        elif cmd == "add":
            game = Nup(args[0])
            specs = [game.package_spec(i) for i in range(len(game.packages))]
            added = []
            for extra in args[1:]:
                src = Nup(extra)
                for i in range(len(src.packages)):
                    s = src.package_spec(i)
                    if s["kind"] == 0 and any(x["kind"] == 0 for x in specs):
                        raise NupError("%s holds a base game; the game file already has one" % Path(extra).name)
                    specs = [x for x in specs if not same_package(x, s)]
                    specs.append(s)
                    added.append("%s %016X v%d" % (KINDS[s["kind"]], s["title_id"], s["version"]))
            copied = replace_game(args[0], specs, game.header_version, out_path)
            print("PASS: added %s to %s (%.1f GB copied and compared byte for byte)" % (", ".join(added), Path(out_path or args[0]).name, copied / 1e9))
        elif cmd == "split":
            game = Nup(args[0])
            i, out = int(args[1]), Path(args[2])
            if out.exists():
                raise NupError("%s already exists" % out)
            s = game.package_spec(i)
            part = out.with_name("." + out.name + ".gcm-part")
            try:
                _p, runs = write_nup([s], part, game.header_version)
                verify(part, full=True, expect=[(s["kind"], s["title_id"], s["version"])])
                check_copies(part, runs)
                os.replace(part, out)
            finally:
                if part.exists():
                    part.unlink()
            print("PASS: copied %s %016X v%d to %s" % (KINDS[s["kind"]], s["title_id"], s["version"], out))
        elif cmd == "remove":
            game = Nup(args[0])
            drop = {int(a) for a in args[1:]}
            if not drop or max(drop) >= len(game.packages):
                raise NupError("package index out of range")
            specs = [game.package_spec(i) for i in range(len(game.packages)) if i not in drop]
            if not any(s["kind"] in (0, 1) for s in specs):
                raise NupError("removing these would leave the game without a program")
            copied = replace_game(args[0], specs, game.header_version, out_path)
            print("PASS: removed %d package(s) from %s (%.1f GB copied and compared byte for byte)" % (len(drop), Path(out_path or args[0]).name, copied / 1e9))
        else:
            print(__doc__)
            return 2
    except (NupError, OSError, ValueError, KeyError, IndexError) as exc:
        print("STOP: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
