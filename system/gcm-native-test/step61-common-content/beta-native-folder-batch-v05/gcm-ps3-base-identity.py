"""Internal PS3 base identity for ISO and extracted-folder games."""
import hashlib
import importlib.util
import os
import re
import selectors
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOOL = Path('/usr/bin/7z')
TOOL_SHA = '6c71233b5e47e03717a87af4c5d8f982d74e9f859f59e2754008e3ad87f03e7e'

def bounded(args, limit):
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + 45
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() > deadline:
                raise ValueError("PS3 metadata query timed out")
            for key, _ in selector.select(0.5):
                block = os.read(key.fileobj.fileno(), min(65536, limit + 1 - len(output)))
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(block)
                if len(output) > limit:
                    raise ValueError("PS3 metadata response exceeds limit")
        if proc.wait(timeout=max(0.1, deadline - time.monotonic())):
            raise ValueError("PS3 archive metadata query failed")
        return bytes(output)
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()

def reader():
    path = ROOT / "gcm-sfo-metadata.py"
    expected = "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Shared SFO reader changed")
    spec = importlib.util.spec_from_file_location("ps3_base_sfo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def identify(rom):
    rom = Path(rom)
    if rom.is_symlink():
        raise ValueError("Symlink base source rejected")
    rom = rom.resolve(strict=True)
    parser = reader()
    if rom.is_dir():
        source = rom / "PS3_GAME/PARAM.SFO"
        if source.parent.is_symlink():
            raise ValueError("Symlink game metadata directory rejected")
        report = parser.read(source)
        fields = report["fields"]
        source_identity = report["stat_identity"]
        metadata_hash = report["metadata_sha256"]
    elif rom.is_file() and rom.suffix.lower() == ".iso":
        source = rom
        source_identity = parser.identity(source.stat())
        if hashlib.sha256(TOOL.read_bytes()).hexdigest() != TOOL_SHA:
            raise ValueError("Archive reader executable changed")
        listing = bounded([str(TOOL), "l", "-slt", str(rom),
                           "PS3_GAME/PARAM.SFO"], 1024 * 1024).decode("utf-8", "replace")
        entries = [
            block for block in re.split(r"\r?\n\r?\n", listing)
            if re.search(r"(?m)^Path = PS3_GAME/PARAM\.SFO\r?$", block)
        ]
        if len(entries) != 1:
            raise ValueError("Exactly one internal PS3 SFO required")
        size_match = re.search(r"(?m)^Size = ([0-9]+)\r?$", entries[0])
        if not size_match or not 20 <= int(size_match.group(1)) <= 4 * 1024 * 1024:
            raise ValueError("Internal SFO size outside bounds")
        data = bounded([str(TOOL), "x", "-so", str(rom),
                        "PS3_GAME/PARAM.SFO"], 4 * 1024 * 1024)
        if len(data) != int(size_match.group(1)):
            raise ValueError("Internal SFO length differs from listing")
        fields = parser.parse(data)
        metadata_hash = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(TOOL.read_bytes()).hexdigest() != TOOL_SHA:
            raise ValueError("Archive reader changed during inspection")
    else:
        raise ValueError("PS3 ISO or extracted game folder required")
    title = fields.get("TITLE_ID", "")
    if not isinstance(title, str) or not re.fullmatch(r"[A-Z]{4}[0-9]{5}", title):
        raise ValueError("Invalid internal PS3 title ID")
    if fields.get("CATEGORY") != "DG":
        raise ValueError("Internal PS3 disc-game category required")
    if parser.identity(source.stat()) != list(source_identity):
        raise ValueError("Base identity source changed during inspection")
    return {
        "base": str(rom), "identity_source": str(source),
        "stat_identity": list(source_identity),
        "application_id": title,
        "metadata_sha256": metadata_hash,
        "fields": fields,
        "base_game_integrity_verified": False,
    }
