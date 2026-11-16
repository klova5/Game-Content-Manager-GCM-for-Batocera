"""Bounded PS4 PFS tree manifests; no extraction or installation."""
import hashlib
import struct

BLOCK = 65536
STRIDE = 0xa8

def nodes(reader):
    header = reader.read(0, 80)
    version, magic = struct.unpack_from("<QQ", header)
    block = struct.unpack_from("<I", header, 0x20)[0]
    count = struct.unpack_from("<Q", header, 0x30)[0]
    table_blocks = struct.unpack_from("<Q", header, 0x40)[0]
    superroot = struct.unpack_from("<Q", header, 0x48)[0]
    per_block = BLOCK // STRIDE
    if (
        version != 1 or magic != 0x1332a0b or block != BLOCK
        or not 1 <= count <= 100000 or superroot >= count
        or table_blocks != (count + per_block - 1) // per_block
    ):
        raise ValueError("Unsupported PFS inode-table layout")
    result, ranges = [], []
    for index in range(count):
        page, slot = divmod(index, per_block)
        raw = reader.read((1 + page) * BLOCK + slot * STRIDE, STRIDE)
        mode, links, flags, size = struct.unpack_from("<HHIq", raw)
        words = struct.unpack_from("<18I", raw, 0x60)
        blocks, location = words[:2]
        kind = mode & 0xf000
        if kind not in (0x4000, 0x8000) or links < 1 or size < 0 or flags & 1:
            raise ValueError("Unsupported PFS inode")
        if (
            blocks != (size + BLOCK - 1) // BLOCK
            or any(value not in (0, 0xffffffff) for value in words[2:13])
            or any(words[13:])
        ):
            raise ValueError("Non-contiguous PFS inode requires separate support")
        if blocks:
            if (
                location < 1 + table_blocks
                or (location + blocks) * BLOCK > reader.pfsc.logical_size
            ):
                raise ValueError("PFS inode extent outside data area")
            ranges.append((location, location + blocks))
        result.append({
            "index": index, "kind": kind, "size": size,
            "blocks": blocks, "location": location,
        })
    ranges.sort()
    if any(right[0] < left[1] for left, right in zip(ranges, ranges[1:])):
        raise ValueError("Overlapping PFS inode extents")
    return result, superroot

def directory(reader, node, count):
    if node["kind"] != 0x4000 or not 0 < node["size"] <= 8 * 1024 * 1024:
        raise ValueError("Invalid bounded PFS directory")
    data = reader.read(node["location"] * BLOCK, node["size"])
    entries, names = [], set()
    for page in range(0, len(data), BLOCK):
        offset, end = page, min(page + BLOCK, len(data))
        while offset + 16 <= end:
            inode, kind, length, extent = struct.unpack_from("<iiii", data, offset)
            if inode == 0 and extent == 0:
                if any(data[offset:end]):
                    raise ValueError("Unexpected PFS directory padding")
                break
            if (
                not 0 <= inode < count or kind not in (2, 3, 4, 5)
                or extent < 16 or extent % 8 or offset + extent > end
                or not 1 <= length <= min(255, extent - 16)
            ):
                raise ValueError("Invalid PFS directory record")
            name = data[offset + 16:offset + 16 + length].decode("utf-8")
            if (
                name in names or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(c in name for c in ("/", "\\", ":", chr(0)))
            ):
                raise ValueError("Unsafe or duplicate PFS name")
            names.add(name)
            entries.append((inode, kind, name))
            if len(entries) > 100000:
                raise ValueError("Excessive PFS directory entries")
            offset += extent
    return entries

def manifest(reader):
    table, superroot = nodes(reader)
    top = directory(reader, table[superroot], len(table))
    roots = [inode for inode, kind, name in top if kind == 3 and name == "uroot"]
    if len(roots) != 1:
        raise ValueError("Unique PFS user root required")
    internal = {superroot}
    for inode, kind, name in top:
        if name == "uroot" and kind == 3:
            continue
        if name not in ("flat_path_table", "collision_resolver") or kind != 2:
            raise ValueError("Unsupported PFS superroot entry")
        if table[inode]["kind"] != 0x8000 or inode in internal:
            raise ValueError("Invalid internal PFS inode")
        internal.add(inode)

    root = roots[0]
    seen, paths, entries = set(internal), set(), []
    pending = [(root, root, "", 0)]
    while pending:
        index, parent, prefix, depth = pending.pop()
        if index in seen or depth > 64:
            raise ValueError("PFS cycle, hard link or excessive depth")
        seen.add(index)
        node = table[index]
        children = directory(reader, node, len(table))
        current = [(inode, name) for inode, kind, name in children if kind == 4]
        parents = [(inode, name) for inode, kind, name in children if kind == 5]
        if current != [(index, ".")] or parents != [(parent, "..")]:
            raise ValueError("PFS directory ancestry mismatch")
        for inode, kind, name in children:
            if kind in (4, 5):
                continue
            if name in (".", ".."):
                raise ValueError("Unsafe PFS path component")
            relative = prefix + name
            folded = relative.casefold()
            if folded in paths:
                raise ValueError("Conflicting PFS output paths")
            paths.add(folded)
            target = table[inode]
            if kind == 3:
                if target["kind"] != 0x4000:
                    raise ValueError("PFS directory type mismatch")
                entries.append({"path": relative, "kind": "directory"})
                pending.append((inode, index, relative + "/", depth + 1))
            else:
                if target["kind"] != 0x8000 or inode in seen:
                    raise ValueError("PFS file type mismatch or hard link")
                seen.add(inode)
                digest = hashlib.sha256()
                remaining, offset = target["size"], target["location"] * BLOCK
                while remaining:
                    size = min(1024 * 1024, remaining)
                    digest.update(reader.read(offset, size))
                    remaining -= size
                    offset += size
                entries.append({
                    "path": relative, "kind": "file",
                    "inode": inode,
                    "logical_offset": target["location"] * BLOCK,
                    "size": target["size"], "sha256": digest.hexdigest(),
                })
    if seen != set(range(len(table))):
        raise ValueError("Unreferenced PFS inodes require inspection")
    reader.unchanged()
    return {
        "schema": "GCM_PS4_PFS_MANIFEST_V1",
        "status": "SOURCE_BOUND_PFS_MANIFEST",
        "package_sha256": reader.proof["sha256"],
        "stat_identity": reader.stat_identity,
        "entries": sorted(entries, key=lambda item: item["path"]),
        "installation_authorized": False,
    }
