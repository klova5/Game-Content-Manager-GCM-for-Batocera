"""GCM v3.6 read-only adapter discovery and report."""
import json
from collections import Counter
from pathlib import Path
from adapters import rpcs3, cemu

BASE = Path(__file__).resolve().parent
registry = json.loads((BASE / "adapter-registry.json").read_text())
assert registry["adapter_status"] == "discovery_only"

report = {
    "schema_version": 1,
    "installations_enabled": False,
    "adapters": {},
}

for emulator, module in (("rpcs3", rpcs3), ("cemu", cemu)):
    print(f"\n===== {emulator.upper()} DISCOVERY =====")
    results = module.discover()
    report["adapters"][emulator] = results
    counts = Counter(r["classification"] for r in results)
    print("Identified metadata records:", len(results))
    print("Content classification:", dict(counts))
    for record in results[:35]:
        print(
            record["classification"],
            record["title_id"] or "UNKNOWN-ID",
            record["version"] or "UNKNOWN-VERSION",
            record["title"][:55]
        )
        if record.get("metadata_conflict"):
            print("  WARNING: Metadata/path title ID conflict")
    if len(results) > 35:
        print("Additional records:", len(results) - 35)

out = BASE / "discovery-report.json"
out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
print("\n===== REPORT =====")
print(out)
print("No game or emulator files modified")
print("No content installed")
