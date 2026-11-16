
"""Plan Citron update disabled keys without performing writes."""
import re

def plan_selection(application_id, candidates, selected_token, disabled, *, inventory_complete=False):
    if not re.fullmatch(r"[0-9A-F]{16}", application_id):
        raise ValueError("Invalid application ID")
    if inventory_complete is not True:
        raise ValueError("Complete update inventory required")
    if type(candidates) is not list or not 1 <= len(candidates) <= 128:
        raise ValueError("Invalid update candidates")
    if type(disabled) is not list or any(type(x) is not str for x in disabled):
        raise ValueError("Invalid disabled keys")

    known = {}
    ownership = {}
    for item in candidates:
        if item["application_id"] != application_id:
            raise ValueError("Other-game update candidate")
        if int(item["title_id"], 16) != int(application_id, 16) + 0x800:
            raise ValueError("Update title relationship mismatch")
        if item.get("content_verified") is not True:
            raise ValueError("Unverified update candidate")
        if type(item["internal_version"]) is not int or not 0 <= item["internal_version"] < 2**32:
            raise ValueError("Invalid update version")
        token = item["token"]
        keys = item["disable_keys"]
        if type(token) is not str or not token or token in known:
            raise ValueError("Invalid or duplicate update token")
        if (
            type(keys) is not list or not keys
            or len(set(keys)) != len(keys)
            or any(
                type(key) is not str
                or not key.startswith(("NAND Files/Update v", "External Files/Update v"))
                or any(c in key for c in "\r\n\t\0")
                for key in keys
            )
        ):
            raise ValueError("Invalid native update keys")
        for key in keys:
            if key in ownership:
                raise ValueError("Ambiguous native update keys")
            ownership[key] = token
        known[token] = item

    if selected_token not in known:
        raise ValueError("Selected update is outside verified inventory")

    # Remove global blockers and known per-version keys, preserving everything else.
    managed = {"Update", "NAND Files/Update"} | set(ownership)
    result = [key for key in disabled if key not in managed]

    # Block every known competing candidate using its native keys.
    for token, item in known.items():
        if token != selected_token:
            result.extend(item["disable_keys"])

    return result
