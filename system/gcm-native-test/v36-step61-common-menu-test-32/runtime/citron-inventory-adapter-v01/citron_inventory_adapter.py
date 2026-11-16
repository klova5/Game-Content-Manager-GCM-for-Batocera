import hashlib
import json
import re
import sys
from pathlib import Path

model_dir = Path(__file__).resolve().parent.parent / "citron-inventory-model-v01"
model_file = model_dir / "citron_registry_model.py"
expected = "cc076c485ed1ce589aaf31cf950314f567b708df5f76253afefcef51ab081977"

if not model_file.is_file() or model_file.is_symlink():
    raise RuntimeError("Verified model missing")

if hashlib.sha256(model_file.read_bytes()).hexdigest() != expected:
    raise RuntimeError("Verified model changed")

sys.path.insert(0, str(model_dir))

from citron_registry_model import ContentRecord, assess

FIELDS = {
    "title_id", "title_type", "base_title_id",
    "base_link_verified", "version", "metadata_valid",
    "referenced_ncas_present"
}

def title_id(value, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{16}", value):
        raise ValueError("Invalid title ID")
    return value.upper()

def build_report(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("Invalid snapshot")

    if set(snapshot) != {"schema_version", "source", "selected_game", "records"}:
        raise ValueError("Invalid snapshot fields")

    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise ValueError("Unsupported schema")

    if snapshot["source"] != "SYNTHETIC":
        raise ValueError("Only synthetic data is accepted")

    game = title_id(snapshot["selected_game"])
    records = snapshot["records"]

    if not isinstance(records, list) or len(records) > 500:
        raise ValueError("Invalid record collection")

    matched = []
    unresolved = 0
    excluded = 0
    seen = set()

    for item in records:
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise ValueError("Invalid record fields")

        tid = title_id(item["title_id"])
        base = title_id(item["base_title_id"], nullable=True)

        if tid in seen:
            raise ValueError("Duplicate content title ID")
        seen.add(tid)

        if type(item["base_link_verified"]) is not bool:
            raise ValueError("Invalid association verification")

        if type(item["metadata_valid"]) is not bool:
            raise ValueError("Invalid metadata validity")

        if item["base_link_verified"] and base is None:
            raise ValueError("Verified association missing base title")

        refs = item["referenced_ncas_present"]

        if not isinstance(refs, list) or len(refs) > 128:
            raise ValueError("Invalid content references")

        if any(type(x) is not bool for x in refs):
            raise ValueError("Invalid reference availability")

        rec = ContentRecord(
            title_id=tid,
            title_type=item["title_type"],
            base_title_id=base,
            base_link_verified=item["base_link_verified"],
            version=item["version"],
            metadata_valid=item["metadata_valid"],
            referenced_ncas_present=tuple(refs),
        )

        result = assess(game, rec)

        if result["association"] == "MATCHED":
            matched.append(result)
        elif result["association"] == "OTHER_GAME":
            excluded += 1
        else:
            unresolved += 1

    return {
        "schema_version": 1,
        "source": "SYNTHETIC_ONLY",
        "selected_game": game,
        "matched_records": matched,
        "excluded_other_game": excluded,
        "unresolved_association": unresolved,
        "live_inventory_verified": False,
    }

if __name__ == "__main__":
    fixture = Path(__file__).resolve().parent / "synthetic_fixture.json"
    snapshot = json.loads(fixture.read_text(encoding="utf-8"))
    report = build_report(snapshot)

    print("===== SYNTHETIC GAME INVENTORY =====")
    print("Game:", report["selected_game"])
    print("Matched records:", len(report["matched_records"]))
    print("Excluded other-game:", report["excluded_other_game"])
    print("Unresolved association:", report["unresolved_association"])

    for rec in report["matched_records"]:
        print(
            rec["type"],
            rec["title_id"],
            rec["content_completeness"],
            "INSTALLED=" + rec["installed"],
            "ACTIVE=" + rec["active"]
        )

    print("PASS: Synthetic inventory report generated")
    print("PASS: Live inventory remains unverified")
