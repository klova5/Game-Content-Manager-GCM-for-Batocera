from dataclasses import dataclass
import re

TITLE_ID = re.compile(r"^[0-9A-Fa-f]{16}$")

@dataclass(frozen=True)
class ContentRecord:
    title_id: str
    title_type: str
    base_title_id: str | None
    base_link_verified: bool
    version: int | None
    metadata_valid: bool
    referenced_ncas_present: tuple[bool, ...]

def assess(selected_game, record):
    if not TITLE_ID.fullmatch(selected_game):
        raise ValueError("Invalid selected game title ID")

    if not TITLE_ID.fullmatch(record.title_id):
        raise ValueError("Invalid content title ID")

    if record.title_type not in ("Update", "AOC"):
        raise ValueError("Unsupported content type")

    if record.base_title_id is not None:
        if not TITLE_ID.fullmatch(record.base_title_id):
            raise ValueError("Invalid associated game title ID")

    if record.version is not None:
        if type(record.version) is not int:
            raise ValueError("Invalid version")
        if not 0 <= record.version < 2**32:
            raise ValueError("Version outside u32 range")

    if any(type(x) is not bool for x in record.referenced_ncas_present):
        raise ValueError("Invalid NCA availability data")

    association = "UNKNOWN"

    if record.base_link_verified:
        if record.base_title_id == selected_game:
            association = "MATCHED"
        elif record.base_title_id is not None:
            association = "OTHER_GAME"

    completeness = "UNKNOWN"

    if association == "MATCHED" and record.metadata_valid:
        refs = record.referenced_ncas_present

        if not refs:
            completeness = "UNKNOWN"
        elif all(refs):
            completeness = "COMPLETE_BY_INPUT"
        else:
            completeness = "INCOMPLETE"

    return {
        "title_id": record.title_id,
        "type": record.title_type,
        "version": record.version,
        "association": association,
        "content_completeness": completeness,
        "installed": "UNKNOWN",
        "enabled": "UNKNOWN",
        "active": "UNKNOWN",
    }

def self_test():
    base = "0100AAAAAAAAA000"

    good = ContentRecord(
        "0100AAAAAAAAA800",
        "Update",
        base,
        True,
        65536,
        True,
        (True, True),
    )

    result = assess(base, good)
    assert result["association"] == "MATCHED"
    assert result["content_completeness"] == "COMPLETE_BY_INPUT"
    assert result["installed"] == "UNKNOWN"
    assert result["active"] == "UNKNOWN"

    missing = ContentRecord(
        "0100AAAAAAAAB001",
        "AOC",
        base,
        True,
        0,
        True,
        (True, False),
    )

    result = assess(base, missing)
    assert result["content_completeness"] == "INCOMPLETE"
    assert result["installed"] == "UNKNOWN"

    unverified = ContentRecord(
        "0100AAAAAAAAB002",
        "AOC",
        base,
        False,
        0,
        True,
        (True,),
    )

    assert assess(base, unverified)["association"] == "UNKNOWN"

    empty = ContentRecord(
        "0100AAAAAAAAA800",
        "Update",
        base,
        True,
        0,
        True,
        (),
    )

    assert assess(base, empty)["content_completeness"] == "UNKNOWN"

    other = ContentRecord(
        "0100AAAAAAAAC800",
        "Update",
        "0100AAAAAAAAC000",
        True,
        0,
        True,
        (True,),
    )

    assert assess(base, other)["association"] == "OTHER_GAME"

    print("PASS: Complete synthetic update")
    print("PASS: Incomplete synthetic DLC")
    print("PASS: Unverified game association")
    print("PASS: Empty content references")
    print("PASS: Other-game exclusion")
    print("PASS: Installed and active remain UNKNOWN")

if __name__ == "__main__":
    self_test()
