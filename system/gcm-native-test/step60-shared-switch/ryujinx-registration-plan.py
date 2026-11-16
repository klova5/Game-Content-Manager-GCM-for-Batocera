
"""Plan native Ryujinx registration while preserving existing activation."""
import copy
import os
import re
from pathlib import Path

def plan(game, managed, proof, nca_names=()):
    snapshot = game["native_snapshot"]
    app = snapshot["application_id"]
    kind = proof.get("content_type")
    title = proof.get("title_id", "")
    managed = str(Path(managed))
    if (
        proof.get("application_id") != app
        or not re.fullmatch(r"[0-9A-F]{16}", app)
        or not re.fullmatch(r"[0-9A-F]{16}", title)
        or kind not in ("update", "dlc")
        or not Path(managed).is_absolute()
        or proof.get("primary_content_hashes_verified") is not True
        or proof.get("present_content_hashes_verified") is not True
    ):
        raise ValueError("Matching verified managed content required")
    before = {
        name: copy.deepcopy(item["document"])
        for name, item in snapshot["configuration"].items()
    }
    after = copy.deepcopy(before)
    migration_enabled = None

    if kind == "update":
        if int(title, 16) != int(app, 16) + 0x800:
            raise ValueError("Update title does not match application")
        paths = after["updates"].get("paths", [])
        if not any(os.path.realpath(path) == os.path.realpath(managed)
                   for path in paths):
            paths.append(managed)
        after["updates"]["paths"] = paths
        # Preserve the selected path exactly.
        after["updates"]["selected"] = before["updates"].get("selected")
        target = "updates"
    else:
        if any(item["content_type"] == "dlc" for item in game["problems"]):
            raise ValueError("Unverified registered DLC prevents safe replacement")
        same_title = [
            item for item in game["records"]
            if item["content_type"] == "dlc" and item["title_id"] == title
        ]
        if any(item["internal_version"] != proof["internal_version"]
               or item["sha256"] != proof["sha256"] for item in same_title):
            raise ValueError("Different DLC revision requires explicit replacement")
        matching = []
        kept = []
        for entry in before["dlc"]:
            tids = [int(item["title_id"])
                    for item in entry.get("dlc_nca_list", [])]
            (matching if int(title, 16) in tids else kept).append(copy.deepcopy(entry))
        if matching:
            if len(matching) != 1 or not same_title:
                raise ValueError("Ambiguous or unverified existing DLC registration")
            template = matching[0]
            flags = {
                bool(item.get("is_enabled", True))
                for item in template.get("dlc_nca_list", [])
            }
            if len(flags) != 1:
                raise ValueError("Mixed DLC NCA states require explicit migration")
            migration_enabled = next(iter(flags))
        else:
            if len(nca_names) != 1 or proof.get("verified_primary_records") != 1:
                raise ValueError("Verified single-NCA native DLC mapping required")
            name = nca_names[0]
            if (not isinstance(name, str) or Path(name).name != name
                or not re.fullmatch(r"[0-9a-fA-F]{32}\.nca", name)):
                raise ValueError("Invalid verified DLC NCA member")
            template = {
                "dlc_nca_list": [{
                    "path": "/" + name, "title_id": int(title, 16),
                    "is_enabled": False,
                }]
            }
        template["path"] = managed
        for item in template["dlc_nca_list"]:
            item["is_enabled"] = bool(migration_enabled)
        after["dlc"] = kept + [template]
        target = "dlc"

    return {
        "application_id": app, "content_type": kind,
        "target_configuration": target,
        "before": before, "after": after,
        "migration_enabled": migration_enabled,
        "active_preserved": True,
    }
