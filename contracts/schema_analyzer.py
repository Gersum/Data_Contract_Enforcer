import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import load_registry_subscriptions, subscribers_for_contract, write_json


def load_snapshots(contract_id: str) -> list[dict]:
    prefix = contract_id.replace("-", "_")
    nested_paths = sorted((ROOT / "schema_snapshots" / prefix).glob("*.json"))
    flat_paths = sorted((ROOT / "schema_snapshots").glob(f"{prefix}_*.json"))
    paths = nested_paths or flat_paths
    return [json.load(open(path, "r", encoding="utf-8")) for path in paths]


def load_snapshot(path: str) -> dict:
    snapshot_path = ROOT / path if not Path(path).is_absolute() else Path(path)
    with open(snapshot_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def compare_profiles(previous: dict, current: dict) -> list[dict]:
    changes = []
    previous_cols = previous.get("column_profiles", {})
    current_cols = current.get("column_profiles", {})
    all_fields = sorted(set(previous_cols) | set(current_cols))
    for field in all_fields:
        if field not in previous_cols:
            required = current_cols[field].get("null_fraction", 1.0) == 0.0
            changes.append(
                {
                    "field": field,
                    "change_type": "ADDED_REQUIRED_FIELD" if required else "ADDED_NULLABLE_FIELD",
                    "compatibility": "BREAKING" if required else "COMPATIBLE",
                    "impact": "New required field added." if required else "New nullable field added.",
                }
            )
            continue
        if field not in current_cols:
            changes.append({"field": field, "change_type": "REMOVED_FIELD", "compatibility": "BREAKING", "impact": "Field removed from current snapshot."})
            continue
        before = previous_cols[field]
        after = current_cols[field]
        before_required = before.get("null_fraction", 1.0) == 0.0
        after_required = after.get("null_fraction", 1.0) == 0.0
        if before_required != after_required:
            changes.append(
                {
                    "field": field,
                    "change_type": "REQUIREDNESS_TIGHTENED" if after_required else "REQUIREDNESS_RELAXED",
                    "compatibility": "BREAKING" if after_required else "COMPATIBLE",
                    "impact": "Field became required." if after_required else "Field became nullable.",
                }
            )
        if before["dtype"] != after["dtype"]:
            numeric_types = {"int", "float", "number", "integer"}
            is_narrow = (before["dtype"] == "float" and after["dtype"] == "int") or (
                before["dtype"] in numeric_types and after["dtype"] in numeric_types and before["dtype"] != after["dtype"]
            )
            changes.append(
                {
                    "field": field,
                    "change_type": "TYPE_CHANGE",
                    "compatibility": "CRITICAL" if is_narrow else "BREAKING",
                    "impact": f"Type changed from {before['dtype']} to {after['dtype']}.{' Narrowing conversion detected.' if is_narrow else ''}",
                }
            )
        if before.get("stats") and after.get("stats"):
            before_max = before["stats"]["max"]
            after_max = after["stats"]["max"]
            if field.endswith("confidence") and ((before_max <= 1.0 < after_max) or (after_max <= 1.0 < before_max)):
                changes.append(
                    {
                        "field": field,
                        "change_type": "SEMANTIC_RANGE_SHIFT",
                        "compatibility": "BREAKING",
                        "impact": f"Confidence max moved from {before_max} to {after_max}, indicating a 0.0-1.0 to 0-100 scale break.",
                    }
                )
            before_values = set(before.get("sample_values", []))
            after_values = set(after.get("sample_values", []))
            if before["dtype"] == "string" and before.get("cardinality_estimate", 999) <= 8 and after.get("cardinality_estimate", 999) <= 8:
                removed_values = sorted(before_values - after_values)
                added_values = sorted(after_values - before_values)
                if removed_values:
                    changes.append(
                        {
                            "field": field,
                            "change_type": "ENUM_VALUE_REMOVED",
                            "compatibility": "BREAKING",
                            "impact": f"Enum-like values removed: {removed_values}",
                        }
                    )
                if added_values:
                    changes.append(
                        {
                            "field": field,
                            "change_type": "ENUM_VALUE_ADDED",
                            "compatibility": "COMPATIBLE",
                            "impact": f"Enum-like values added: {added_values}",
                        }
                    )
    return changes


def consumer_failure_modes(contract_id: str, registry_path: Path) -> list[dict]:
    if not registry_path.exists():
        return []
    subscriptions = subscribers_for_contract(load_registry_subscriptions(registry_path), contract_id)
    analyses = []
    for subscription in subscriptions:
        analyses.append(
            {
                "subscriber_id": subscription.get("subscriber_id"),
                "validation_mode": subscription.get("validation_mode", "AUDIT"),
                "fields_consumed": subscription.get("fields_consumed", []),
                "breaking_fields": subscription.get("breaking_fields", []),
                "failure_mode": "Consumer must update parsing, thresholds, or downstream joins before accepting the new shape.",
            }
        )
    return analyses


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze contract snapshot evolution.")
    parser.add_argument("--contract-id")
    parser.add_argument("--previous")
    parser.add_argument("--current")
    parser.add_argument("--since")
    parser.add_argument("--registry", default="contract_registry/subscriptions.yaml")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.previous and args.current:
        previous = load_snapshot(args.previous)
        current = load_snapshot(args.current)
        snapshots = [previous, current]
        contract_id = current.get("contract_id") or previous.get("contract_id") or "unknown-contract"
    elif args.contract_id:
        snapshots = load_snapshots(args.contract_id)
        if len(snapshots) < 2:
            raise SystemExit("At least two snapshots are required.")
        
        if args.since:
            since_date = datetime.fromisoformat(args.since.replace('Z', '+00:00')).astimezone(timezone.utc)
            valid_snapshots = [s for s in snapshots if datetime.fromisoformat(s["generated_at"].replace('Z', '+00:00')).astimezone(timezone.utc) >= since_date]
            if len(valid_snapshots) >= 2:
                previous = valid_snapshots[0]
                current = valid_snapshots[-1]
            else:
                previous = snapshots[-2]
                current = snapshots[-1]
        else:
            previous = snapshots[-2]
            current = snapshots[-1]
        contract_id = args.contract_id
    else:
        raise SystemExit("Provide --contract-id or both --previous and --current.")

    changes = compare_profiles(previous, current)
    compatibility = "BREAKING" if any(change["compatibility"] in {"BREAKING", "CRITICAL"} for change in changes) else "COMPATIBLE"
    registry_path = ROOT / args.registry if not Path(args.registry).is_absolute() else Path(args.registry)
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "contract_id": contract_id,
        "snapshot_count": len(snapshots),
        "previous_snapshot": previous["generated_at"],
        "current_snapshot": current["generated_at"],
        "compatibility_verdict": compatibility,
        "changes": changes,
        "blast_radius": {
            "subscriber_count": len(consumer_failure_modes(contract_id, registry_path)),
            "subscribers": consumer_failure_modes(contract_id, registry_path),
        },
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
        "consumer_failure_modes": consumer_failure_modes(contract_id, registry_path),
    }
    write_json(args.output, report)
    print(f"Wrote schema evolution report to {args.output}")


if __name__ == "__main__":
    main()
