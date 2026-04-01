import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import write_json


def load_snapshots(contract_id: str) -> list[dict]:
    prefix = contract_id.replace("-", "_")
    paths = sorted((ROOT / "schema_snapshots").glob(f"{prefix}_*.json"))
    return [json.load(open(path, "r", encoding="utf-8")) for path in paths]


def compare_profiles(previous: dict, current: dict) -> list[dict]:
    changes = []
    previous_cols = previous.get("column_profiles", {})
    current_cols = current.get("column_profiles", {})
    all_fields = sorted(set(previous_cols) | set(current_cols))
    for field in all_fields:
        if field not in previous_cols:
            changes.append({"field": field, "change_type": "ADDED_FIELD", "compatibility": "FORWARD", "impact": "New field added."})
            continue
        if field not in current_cols:
            changes.append({"field": field, "change_type": "REMOVED_FIELD", "compatibility": "BREAKING", "impact": "Field removed from current snapshot."})
            continue
        before = previous_cols[field]
        after = current_cols[field]
        if before["dtype"] != after["dtype"]:
            changes.append(
                {
                    "field": field,
                    "change_type": "TYPE_CHANGE",
                    "compatibility": "BREAKING",
                    "impact": f"Type changed from {before['dtype']} to {after['dtype']}.",
                }
            )
        if before.get("stats") and after.get("stats"):
            before_max = before["stats"]["max"]
            after_max = after["stats"]["max"]
            if field.endswith("confidence") and before_max <= 1.0 and after_max > 1.0:
                changes.append(
                    {
                        "field": field,
                        "change_type": "SEMANTIC_RANGE_SHIFT",
                        "compatibility": "BREAKING",
                        "impact": f"Confidence max moved from {before_max} to {after_max}, indicating a 0.0-1.0 to 0-100 scale break.",
                    }
                )
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze contract snapshot evolution.")
    parser.add_argument("--contract-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    snapshots = load_snapshots(args.contract_id)
    if len(snapshots) < 2:
        raise SystemExit("At least two snapshots are required.")

    previous = snapshots[-2]
    current = snapshots[-1]
    changes = compare_profiles(previous, current)
    compatibility = "BREAKING" if any(change["compatibility"] == "BREAKING" for change in changes) else "COMPATIBLE"
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "contract_id": args.contract_id,
        "snapshot_count": len(snapshots),
        "previous_snapshot": previous["generated_at"],
        "current_snapshot": current["generated_at"],
        "compatibility_verdict": compatibility,
        "changes": changes,
        "migration_impact_report": [
            "Update week3 consumers to expect confidence values as floats in the 0.0-1.0 range.",
            "Backfill bad records before re-running downstream lineage and event publication jobs.",
            "Add pre-merge validation for extraction payloads to catch enum and entity-ref drift earlier.",
        ],
        "rollback_plan": [
            "Restore the last known-good extractor release.",
            "Re-run contract validation on the clean extraction dataset.",
            "Replay only validated events into the week5 event store.",
        ],
    }
    write_json(args.output, report)
    print(f"Wrote schema evolution report to {args.output}")


if __name__ == "__main__":
    main()
