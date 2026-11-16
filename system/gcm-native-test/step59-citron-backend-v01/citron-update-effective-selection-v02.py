
"""Resolve configured selection for the native-tested Mario Kart update profile."""

def resolve(disabled, update_title_disabled, *, inventory_complete=False,
            external_registered=True):
    if inventory_complete is not True:
        raise ValueError("Complete verified update inventory required")
    for values in (disabled, update_title_disabled):
        if type(values) is not list or any(type(key) is not str for key in values):
            raise ValueError("Invalid disabled settings")

    if type(external_registered) is not bool:
        raise ValueError("Explicit external registration state required")
    global_keys = {"Update", "NAND Files/Update"}
    external_keys = {
        "External Files/Update v3.0.4",
        "External Files/Update v0.20.0",
    }
    base = set(disabled)
    metadata = set(update_title_disabled)

    # Native lookup for the update title can obtain external Control metadata.
    external_control_selected = external_registered and not (
        metadata & (global_keys | external_keys)
    )
    nand_key = (
        "NAND Files/Update v3.0.4" if external_control_selected
        else "NAND Files/Update v4.0.0"
    )

    external_enabled = external_registered and not (base & (global_keys | external_keys))
    nand_enabled = not (base & global_keys) and nand_key not in base

    if nand_enabled:
        return {
            "internal_version": 1441792,
            "display_version": "4.0.0",
            "origin": "UserNAND",
            "token": "citron:update:0100152000022800:1441792:UserNAND",
        }
    if external_enabled:
        return {
            "internal_version": 1310720,
            "display_version": "3.0.4",
            "origin": "External",
            "token": "citron:update:0100152000022800:1310720:External",
        }
    return None
