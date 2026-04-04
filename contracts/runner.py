import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import build_column_profile, flatten_week3, flatten_week5, is_iso_datetime, is_uuid, parse_iso8601, read_jsonl, utc_now, write_json


def fail_result(clause: dict, status: str, message: str, **extra: object) -> dict:
    result = {
        "check_id": clause["check_id"],
        "field": clause["field"],
        "check_type": clause["check_type"],
        "severity": clause["severity"],
        "status": status,
        "message": message,
    }
    result.update(extra)
    return result


def infer_dataset_type(contract: dict) -> str:
    if contract.get("dataset_type"):
        return contract["dataset_type"]
    check_id = contract.get("clauses", [{}])[0].get("check_id", "")
    return "week5" if check_id.startswith("week5") else "week3"


def values_for_dataset(records: list[dict], dataset_type: str) -> dict[str, list]:
    if dataset_type == "week3":
        _, values = flatten_week3(records)
    else:
        _, values = flatten_week5(records)
    return values


def run_clause(clause: dict, values: dict[str, list], records: list[dict], dataset_type: str) -> dict:
    field_present = clause["field"] in values
    field_values = values.get(clause["field"], [])
    non_null = [value for value in field_values if value is not None]
    check_type = clause["check_type"]

    if not field_present:
        return fail_result(
            clause,
            "ERROR",
            f"Field {clause['field']} is missing from the dataset projection",
            records_failing=len(records),
        )

    if check_type == "required":
        missing = sum(value is None for value in field_values)
        if missing:
            return fail_result(clause, "FAIL", f"{missing} null values found", records_failing=missing)
        return fail_result(clause, "PASS", "Required field present in all profiled rows")

    if check_type == "uuid":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for UUID validation", records_failing=len(field_values))
        invalid = [value for value in non_null if not is_uuid(value)]
        if invalid:
            return fail_result(clause, "FAIL", "Invalid UUID values found", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "All UUID values are valid")

    if check_type == "datetime":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for datetime validation", records_failing=len(field_values))
        invalid = [value for value in non_null if not is_iso_datetime(value)]
        if invalid:
            return fail_result(clause, "FAIL", "Invalid ISO-8601 timestamps found", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "All timestamps parse as ISO-8601 UTC")

    if check_type == "min":
        actual_min = min(non_null) if non_null else None
        if actual_min is None or actual_min < clause["minimum"]:
            return fail_result(
                clause,
                "FAIL",
                f"Minimum value {actual_min} is below contract minimum {clause['minimum']}",
                expected=clause["minimum"],
                actual_value=actual_min,
            )
        return fail_result(clause, "PASS", "Minimum threshold satisfied", expected=clause["minimum"], actual_value=actual_min)

    if check_type == "range":
        actual_min = min(non_null) if non_null else None
        actual_max = max(non_null) if non_null else None
        if actual_min is None or actual_min < clause["minimum"] or actual_max > clause["maximum"]:
            failing = [value for value in non_null if value < clause["minimum"] or value > clause["maximum"]]
            return fail_result(
                clause,
                "FAIL",
                f"Range violation detected: expected {clause['minimum']}..{clause['maximum']} but found {actual_min}..{actual_max}",
                expected={"minimum": clause["minimum"], "maximum": clause["maximum"]},
                actual_value={"minimum": actual_min, "maximum": actual_max},
                records_failing=len(failing),
                sample_values=failing[:5],
            )
        return fail_result(
            clause,
            "PASS",
            "All values stayed within the expected range",
            expected={"minimum": clause["minimum"], "maximum": clause["maximum"]},
            actual_value={"minimum": actual_min, "maximum": actual_max},
        )

    if check_type == "enum":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for enum validation", records_failing=len(field_values))
        allowed = set(clause["allowed_values"])
        invalid = [value for value in non_null if value not in allowed]
        if invalid:
            return fail_result(clause, "FAIL", "Enum conformance failed", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "Enum conformance passed")

    if check_type == "boolean_truth":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for boolean validation", records_failing=len(field_values))
        invalid = [value for value in non_null if value is not True]
        if invalid:
            return fail_result(clause, "FAIL", "Boolean truth check failed", records_failing=len(invalid))
        return fail_result(clause, "PASS", "All profiled rows satisfied the boolean check")

    if check_type == "pattern":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for pattern validation", records_failing=len(field_values))
        import re

        pattern = re.compile(clause["pattern"])
        invalid = [value for value in non_null if not isinstance(value, str) or not pattern.fullmatch(value)]
        if invalid:
            return fail_result(clause, "FAIL", "Pattern check failed", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "Pattern check passed")

    if check_type == "registry_membership":
        if not non_null:
            return fail_result(clause, "FAIL", "No non-null values available for registry validation", records_failing=len(field_values))
        registry_path = ROOT / clause["registry_path"]
        with open(registry_path, "r", encoding="utf-8") as handle:
            registry = json.load(handle)
        invalid = [value for value in non_null if value not in registry]
        if invalid:
            return fail_result(clause, "FAIL", "Values missing from schema registry", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "All values are registered")

    if check_type == "temporal_order":
        invalid = []
        for record in records:
            left = record.get("recorded_at")
            right = record.get(clause["reference_field"])
            if left is None or right is None:
                invalid.append({"record": record.get("event_id"), "reason": "missing timestamp"})
                continue
            if parse_iso8601(left) < parse_iso8601(right):
                invalid.append({"record": record.get("event_id"), "recorded_at": left, "occurred_at": right})
        if invalid:
            return fail_result(clause, "FAIL", "recorded_at precedes occurred_at", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "Temporal ordering satisfied")

    if check_type == "monotonic_per_aggregate":
        invalid = []
        groups: dict[str, list[int]] = {}
        for record in records:
            groups.setdefault(record["aggregate_id"], []).append(record["sequence_number"])
        for aggregate_id, seqs in groups.items():
            ordered = sorted(seqs)
            expected = list(range(1, len(ordered) + 1))
            if ordered != expected:
                invalid.append({"aggregate_id": aggregate_id, "actual": ordered, "expected": expected})
        if invalid:
            return fail_result(clause, "FAIL", "Sequence numbers are not gap-free per aggregate", records_failing=len(invalid), sample_values=invalid[:3])
        return fail_result(clause, "PASS", "Sequence numbers are monotonic per aggregate")

    if check_type == "superset":
        invalid = []
        required_keys = set(clause["required_keys"])
        for record in records:
            keys = set(record.get("payload", {}).keys())
            if not required_keys.issubset(keys):
                invalid.append({"event_id": record.get("event_id"), "missing_keys": sorted(required_keys - keys)})
        if invalid:
            return fail_result(clause, "FAIL", "Payload keys missing required entries", records_failing=len(invalid), sample_values=invalid[:5])
        return fail_result(clause, "PASS", "Payload contains all required keys")

    return fail_result(clause, "ERROR", f"Unsupported check type: {check_type}")


def run_drift_checks(values: dict[str, list], update_baseline: bool) -> list[dict]:
    baseline_path = ROOT / "schema_snapshots/baselines.json"
    if not baseline_path.exists():
        baselines = {}
    else:
        with open(baseline_path, "r", encoding="utf-8") as handle:
            baselines = json.load(handle).get("columns", {})

    results = []
    current = {}
    for field, field_values in values.items():
        profile = build_column_profile(field_values)
        if "stats" not in profile:
            continue
        current[field] = {"mean": profile["stats"]["mean"], "stddev": profile["stats"]["stddev"]}
        baseline = baselines.get(field)
        if not baseline:
            results.append(
                {
                    "check_id": f"drift.{field}",
                    "field": field,
                    "check_type": "statistical_drift",
                    "severity": "LOW",
                    "status": "BASELINE_SET",
                    "message": (
                        "No baseline existed; current profile stored as baseline."
                        if update_baseline
                        else "No baseline existed; run a clean AUDIT pass to establish one."
                    ),
                }
            )
            continue
        denominator = max(baseline.get("stddev", 0.0), 1e-9)
        z_score = abs(current[field]["mean"] - baseline["mean"]) / denominator
        status = "PASS"
        if z_score > 3:
            status = "FAIL"
        elif z_score > 2:
            status = "WARN"
        results.append(
            {
                "check_id": f"drift.{field}",
                "field": field,
                "check_type": "statistical_drift",
                "severity": "MEDIUM",
                "status": status,
                "message": f"Current mean differs from baseline by {z_score:.2f} stddev.",
                "z_score": round(z_score, 2),
                "expected": baseline,
                "actual_value": current[field],
            }
        )

    if update_baseline:
        merged_columns = dict(baselines)
        merged_columns.update(current)
        write_json(
            baseline_path,
            {
                "written_at": utc_now(),
                "columns": merged_columns,
            },
        )
    return results


def pipeline_action(mode: str, results: list[dict]) -> str:
    has_critical_failures = any(result["status"] in {"FAIL", "ERROR"} and result.get("severity") == "CRITICAL" for result in results)
    has_high_failures = any(result["status"] in {"FAIL", "ERROR"} and result.get("severity") == "HIGH" for result in results)
    has_failures = any(result["status"] in {"FAIL", "ERROR"} for result in results)
    has_warns = any(result["status"] == "WARN" for result in results)
    if mode == "AUDIT":
        return "ALLOW_WITH_FINDINGS" if (has_failures or has_warns) else "ALLOW"
    if mode == "WARN":
        if has_critical_failures:
            return "BLOCK"
        return "QUARANTINE" if (has_failures or has_warns) else "ALLOW"
    # ENFORCE mode
    if has_critical_failures or has_high_failures:
        return "BLOCK"
    if has_warns or has_failures:
        return "QUARANTINE"
    return "ALLOW"


def main() -> None:
    import uuid
    parser = argparse.ArgumentParser(description="Run contract validation against a JSONL dataset.")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--mode", choices=["AUDIT", "WARN", "ENFORCE"], default="AUDIT")
    parser.add_argument("--output")
    args = parser.parse_args()

    with open(args.contract, "r", encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    records = read_jsonl(args.data)
    dataset_type = infer_dataset_type(contract)
    values = values_for_dataset(records, dataset_type)

    structural_results = [run_clause(clause, values, records, dataset_type) for clause in contract.get("clauses", [])]
    structural_failures = any(result["status"] in {"FAIL", "ERROR"} for result in structural_results)
    drift_results = run_drift_checks(values, update_baseline=(args.mode == "AUDIT" and not structural_failures))
    results = structural_results + drift_results

    summary_status = "PASS"
    if any(result["status"] in {"FAIL", "ERROR"} for result in results):
        summary_status = "FAIL"
    elif any(result["status"] == "WARN" for result in results):
        summary_status = "WARN"

    report = {
        "report_id": str(uuid.uuid4()),
        "contract_id": contract.get("contract_id"),
        "snapshot_id": "snap-12345",
        "run_timestamp": utc_now(),
        "generated_at": utc_now(),
        "source_data": args.data,
        "mode": args.mode,
        "status": summary_status,
        "total_checks": len(results),
        "passed": len([r for r in results if r.get("status") == "PASS"]),
        "failed": len([r for r in results if r.get("status") == "FAIL"]),
        "warned": len([r for r in results if r.get("status") == "WARN"]),
        "errored": len([r for r in results if r.get("status") == "ERROR"]),
        "pipeline_action": pipeline_action(args.mode, results),
        "records_validated": len(records),
        "results": results,
    }
    output_path = args.output or str(ROOT / "validation_reports" / f"{Path(args.contract).stem}_report.json")
    write_json(output_path, report)
    print(f"Validation {summary_status}: wrote {output_path}")


if __name__ == "__main__":
    main()
