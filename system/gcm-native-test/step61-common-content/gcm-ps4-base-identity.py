"""Read-only shadPS4 launcher-to-game identity resolver.

Never executes launcher scripts or modifies source packages.
Only exact CUSA-ID launchers are automatically resolved.
"""

import re
from pathlib import Path

import hashlib
import importlib.util

QueryError = ValueError

def sfo_reader():
    path = Path(__file__).resolve().parent / "gcm-sfo-metadata.py"
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != \
            "282a3d70dcac1ba554ede4b588910dd437b2a9b5bf236d9c8e3734786e524b41":
        raise ValueError("Shared SFO dependency changed")
    spec = importlib.util.spec_from_file_location("ps4_base_sfo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def parse_sfo(data):
    return sfo_reader().parse(data)

def identity(fields):
    title = fields.get("TITLE_ID")
    if not isinstance(title, str) or not re.fullmatch(r"CUSA[0-9]{5}", title):
        raise ValueError("Internal PS4 title ID required")
    content = fields.get("CONTENT_ID")
    conflict = False
    if content:
        conflict = (
            not isinstance(content, str)
            or not re.fullmatch(
                r"[A-Z]{2}[0-9]{4}-CUSA[0-9]{5}_[0-9]{2}-[A-Z0-9]{16}",
                content)
            or content[7:16] != title
        )
    return {
        "title_id": title,
        "identity_conflict": conflict,
        "category": fields.get("CATEGORY"),
    }


ROOT = Path("/userdata/roms/ps4")
TITLE_ID = re.compile(r"CUSA[0-9]{5}", re.I)
LAUNCHER = re.compile(r"(CUSA[0-9]{5})\.sh", re.I)

GAME_ARGUMENT = re.compile(
    r"""(?:^|\s)-g\s+(["'])(CUSA[0-9]{5})\1(?=\s|$)""",
    re.I,
)


def verify_extracted_game(title_id):
    title_id = title_id.upper()

    if not TITLE_ID.fullmatch(title_id):
        raise QueryError("Invalid PS4 title ID")

    folder = ROOT / title_id
    metadata_file = folder / "sce_sys" / "param.sfo"

    if (
        folder.is_symlink()
        or not folder.is_dir()
        or metadata_file.is_symlink()
        or not metadata_file.is_file()
    ):
        raise QueryError(
            "Verified extracted PS4 game unavailable"
        )

    if metadata_file.stat().st_size > 8 * 1024 * 1024:
        raise QueryError("PS4 metadata file exceeds size limit")

    try:
        parsed = parse_sfo(metadata_file.read_bytes())
        details = identity(parsed)
    except (ValueError, KeyError, OSError) as exc:
        raise QueryError(
            "Unable to verify extracted game metadata"
        ) from exc

    if (
        details["title_id"] != title_id
        or details["identity_conflict"]
        or details["category"] not in ("gd", "gp")
    ):
        raise QueryError(
            "Extracted game title ID or identity mismatch"
        )

    return folder



def title_key(value):
    """Normalize presentation differences, not title identity."""
    import unicodedata

    if not isinstance(value, str) or not value.strip():
        return ""

    value = (
        value.replace("™", "")
             .replace("®", "")
             .replace("©", "")
    )

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        c for c in value
        if not unicodedata.combining(c)
    )

    return re.sub(r"[^a-z0-9]", "", value.casefold())


def resolve_named_launcher(source):
    """Require filename, -g target, and unique SFO title match."""

    if source.suffix.lower() != ".sh":
        raise QueryError("Unsupported PS4 launcher")

    if source.name.casefold() in ("dlc.sh", "images.sh"):
        raise QueryError("PS4 utility launcher is not a game")

    if source.stat().st_size > 1024 * 1024:
        raise QueryError("PS4 launcher exceeds size limit")

    script = source.read_text(
        encoding="utf-8",
        errors="replace",
    )

    name_argument = re.compile(
        r"""(?:^|\s)-g\s+(["'])([^"'\r\n]+)\1(?=\s|$)"""
    )

    targets = []

    for line in script.splitlines():
        if line.lstrip().startswith("#"):
            continue

        if "shadps4plus.AppImage" not in line:
            continue

        targets.extend(
            target.strip()
            for _, target in name_argument.findall(line)
        )

    if len(targets) != 1:
        raise QueryError(
            "Missing or ambiguous PS4 launcher target"
        )

    target = targets[0]
    wanted = title_key(target)

    if not wanted or title_key(source.stem) != wanted:
        raise QueryError(
            "PS4 launcher filename and target disagree"
        )

    matches = []

    for candidate in ROOT.iterdir():
        if (
            candidate.is_symlink()
            or not candidate.is_dir()
            or not TITLE_ID.fullmatch(candidate.name)
        ):
            continue

        try:
            folder = verify_extracted_game(candidate.name)

            sfo = folder / "sce_sys" / "param.sfo"
            values = parse_sfo(sfo.read_bytes())

            metadata_title = values.get("TITLE", "")

            if title_key(metadata_title) == wanted:
                matches.append(folder)

        except (QueryError, OSError, ValueError, KeyError):
            continue

    if len(matches) != 1:
        raise QueryError(
            "PS4 title metadata match is missing or ambiguous"
        )

    return matches[0]


def resolve_ps4_game_path(game_path):
    """Resolve an exact CUSA launcher or verified directory."""

    source = Path(game_path)

    if not source.is_absolute():
        raise QueryError("PS4 game path must be absolute")

    if source.is_symlink():
        raise QueryError("Symlinked PS4 game paths are unsupported")

    if source.parent.resolve() != ROOT.resolve():
        raise QueryError(
            "PS4 game must be directly inside the game root"
        )

    if source.is_dir():
        if not TITLE_ID.fullmatch(source.name):
            raise QueryError(
                "Not a verified PS4 game directory"
            )

        return verify_extracted_game(source.name)

    if not source.is_file():
        raise QueryError("PS4 launcher does not exist")

    match = LAUNCHER.fullmatch(source.name)

    if not match:
        return resolve_named_launcher(source)

    title_id = match.group(1).upper()

    if source.stat().st_size > 1024 * 1024:
        raise QueryError("PS4 launcher exceeds size limit")

    script = source.read_text(
        encoding="utf-8",
        errors="replace",
    )

    targets = []

    for line in script.splitlines():
        stripped = line.lstrip()

        if stripped.startswith("#"):
            continue

        if "shadps4plus.AppImage" not in line:
            continue

        for _, target in GAME_ARGUMENT.findall(line):
            targets.append(target.upper())

    if targets != [title_id]:
        raise QueryError(
            "PS4 launcher title ID is missing or ambiguous"
        )

    return verify_extracted_game(title_id)


def identify(rom):
    source = Path(rom).absolute()
    for ancestor in (source, *source.parents):
        if ancestor.is_symlink():
            raise ValueError("Symlink in PS4 base path")
    reader = sfo_reader()
    before = reader.identity(source.stat())
    folder = resolve_ps4_game_path(source)
    metadata = folder / "sce_sys" / "param.sfo"
    report = reader.read(metadata)
    details = identity(report["fields"])
    if details["identity_conflict"] or details["category"] not in ("gd", "gp"):
        raise ValueError("Conflicting PS4 base metadata")
    if (
        resolve_ps4_game_path(source) != folder
        or reader.read(metadata) != report
        or reader.identity(source.stat()) != before
    ):
        raise ValueError("PS4 base changed during resolution")
    return {
        "base": str(source.resolve(strict=True)),
        "base_stat_identity": before,
        "metadata_path": str(metadata.resolve(strict=True)),
        "application_id": details["title_id"],
        "metadata_evidence": report,
        "base_payload_integrity_verified": False,
    }
