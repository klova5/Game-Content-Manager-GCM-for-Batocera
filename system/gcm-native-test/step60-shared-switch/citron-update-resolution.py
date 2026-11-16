
"""Resolve configured Citron update selection from verified registration records."""

GLOBALS = {"Update", "NAND Files/Update"}

def display(item):
    value = item.get("display_version")
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("Verified native display version required for update selection")
    return value

def external_key(item):
    return "External Files/Update v" + display(item)

def resolve(records, disabled, update_title_disabled, *, inventory_complete):
    if inventory_complete is not True:
        raise ValueError("Complete verified installed inventory required")
    disabled = set(disabled)
    title_disabled = set(update_title_disabled)
    if not records:
        return None
    applications = {item["application_id"] for item in records}
    if len(applications) != 1:
        raise ValueError("Selection inventory mixes games")
    application = next(iter(applications))
    for item in records:
        if (
            item["content_type"] != "update"
            or item.get("primary_content_hashes_verified") is not True
            or item.get("present_content_hashes_verified") is not True
            or type(item["internal_version"]) is not int
            or item["internal_version"] < 0
            or int(item["title_id"], 16) != int(application, 16) + 0x800
        ):
            raise ValueError("Invalid verified update candidate")
        if item["origin"] not in ("UserNAND", "External"):
            raise ValueError("Native selection rules need validation for origin: " + item["origin"])
        display(item)
    if disabled & GLOBALS:
        return None

    external_control = [
        item for item in records
        if item["origin"] == "External"
        and not title_disabled & GLOBALS
        and external_key(item) not in title_disabled
    ]
    control_version = None
    if external_control:
        highest = max(item["internal_version"] for item in external_control)
        versions = {display(item) for item in external_control
                    if item["internal_version"] == highest}
        if len(versions) != 1:
            raise ValueError("Ambiguous external Control metadata")
        control_version = next(iter(versions))

    enabled = []
    for item in records:
        if item["origin"] == "External":
            blocked = external_key(item) in disabled
        else:
            native_display = control_version if control_version is not None else display(item)
            blocked = "NAND Files/Update v" + native_display in disabled
        if not blocked:
            enabled.append(item)
    if not enabled:
        return None
    score = lambda item: (
        item["internal_version"], 1 if item["origin"] == "External" else 0)
    best = max(score(item) for item in enabled)
    winners = [item for item in enabled if score(item) == best]
    if len(winners) != 1:
        raise ValueError("Multiple indistinguishable active update registrations")
    return winners[0]
