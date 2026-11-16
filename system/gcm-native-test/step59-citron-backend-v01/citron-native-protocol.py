
"""Format Citron inventory for the shared GCM native menu."""

def field(value):
    if (
        type(value) is not str or not value or len(value) > 256
        or any(char in value for char in "\t\r\n\0")
    ):
        raise ValueError("Invalid native protocol field")
    return value

def title(value):
    import re
    if type(value) is not str or not re.fullmatch(r"[0-9A-F]{16}", value):
        raise ValueError("Invalid title ID")
    return value

def format_update(row):
    title(row["application_id"])
    tid = title(row["title_id"])
    if int(tid, 16) != int(row["application_id"], 16) + 0x800:
        raise ValueError("Update/game relationship mismatch")
    if row.get("non_delta_content_hashes_verified") is not True:
        raise ValueError("Update content is not verified")
    if row.get("origin") != "UserNAND":
        raise ValueError("Unsupported update evidence origin")
    version = field(row["display_version"])
    # Configured enabled does not establish which update is selected.
    if row.get("active_status") != "UNKNOWN":
        raise ValueError("Unexpected active-state claim")
    token = "citron:update:" + tid
    return "\t".join(("ROW", "AVAILABLE", "INSTALLED", "v" + version, token))

def format_dlc(row):
    title(row["application_id"])
    tid = title(row["title_id"])
    if row.get("registered_files_verified") is not True:
        raise ValueError("Registered DLC files are not verified")
    if type(row.get("configured_enabled")) is not bool:
        raise ValueError("DLC configured state unavailable")
    if row.get("in_game_availability") != "UNKNOWN":
        raise ValueError("Unexpected in-game availability claim")
    label = field(row["selection_key"])
    expected = f"DLC {int(tid, 16) & 0x7FF:04d}"
    if label != expected:
        raise ValueError("DLC selection key mismatch")
    state = "ENABLED" if row["configured_enabled"] else "DISABLED"
    token = "citron:dlc:" + tid
    return "\t".join(("ROW", "AVAILABLE", state, label, token))
