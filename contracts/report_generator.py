import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import canonical_contract_id, load_registry_subscriptions, subscribers_for_contract, utc_now, write_json


def load_validation_reports(reports_dir: Path) -> list[dict]:
    reports = []
    for path in sorted(reports_dir.glob("*.json")):
        reports.append(json.load(open(path, "r", encoding="utf-8")))
    return reports


def load_violations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip() and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def index_violations(rows: list[dict]) -> dict[tuple[str, str, str], dict]:
    indexed = {}
    for row in rows:
        key = (
            canonical_contract_id(row.get("contract_id", "")),
            row.get("check_id", ""),
            row.get("field", ""),
        )
        indexed[key] = row
    return indexed


def latest_timestamp(report: dict) -> str:
    return report.get("generated_at", "")


def unique_findings(validation_reports: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str, str, str], dict] = {}
    reports = sorted(validation_reports, key=latest_timestamp)
    for report in reports:
        source_data = report.get("source_data", "")
        contract_id = canonical_contract_id(report.get("contract_id", ""))
        for result in report.get("results", []):
            if result.get("status") not in {"FAIL", "ERROR", "WARN"}:
                continue
            key = (source_data, contract_id, result.get("check_id", ""), result.get("status", ""))
            deduped[key] = {**result, "contract_id": contract_id, "source_data": source_data}
    return list(deduped.values())


def latest_results(validation_reports: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str, str], dict] = {}
    reports = sorted(validation_reports, key=latest_timestamp)
    for report in reports:
        source_data = report.get("source_data", "")
        contract_id = canonical_contract_id(report.get("contract_id", ""))
        for result in report.get("results", []):
            key = (source_data, contract_id, result.get("check_id", ""))
            deduped[key] = {**result, "contract_id": contract_id, "source_data": source_data}
    return list(deduped.values())


def compute_health_summary(results: list[dict]) -> dict:
    total_checks = len(results)
    passed_checks = len([result for result in results if result.get("status") in {"PASS", "BASELINE_SET"}])
    critical_violations = len(
        [result for result in results if result.get("status") in {"FAIL", "ERROR"} and result.get("severity") == "CRITICAL"]
    )
    pass_rate = round((passed_checks / total_checks) * 100, 2) if total_checks else 100.0
    critical_penalty_points = critical_violations * 20
    final_score = max(0, min(100, round(pass_rate - critical_penalty_points)))
    return {
        "formula": "(checks_passed / total_checks) * 100 - (critical_violations * 20)",
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "pass_rate_percent": pass_rate,
        "critical_violations": critical_violations,
        "critical_penalty_points": critical_penalty_points,
        "final_score": final_score,
    }


def plain_language_violation(result: dict, registry_subscriptions: list[dict]) -> str:
    field = result.get("field") or result.get("column_name", "unknown field")
    expected = result.get("expected", "contract expectation")
    actual = result.get("actual_value", "observed value")
    count = result.get("records_failing", "unknown")
    subscribers = subscribers_for_contract(registry_subscriptions, result.get("contract_id", ""))
    subscriber_ids = ", ".join(subscription.get("subscriber_id", "unknown") for subscription in subscribers) or "no registered subscribers"
    return (
        f"The {field} field failed a {result.get('check_type', 'validation')} check. "
        f"Expected {expected} but found {actual}. "
        f"Downstream subscribers affected: {subscriber_ids}. "
        f"Records failing: {count}."
    )


def candidate_file_path(violation: dict | None) -> str | None:
    if not violation:
        return None
    candidates = []
    candidates.extend(violation.get("ranked_candidates", []))
    blame_chain = violation.get("blame_chain", {})
    candidates.extend(blame_chain.get("candidates", []))
    for candidate in candidates:
        node_id = candidate.get("node_id", "")
        if node_id.startswith("file::"):
            return node_id.replace("file::", "", 1)
        if node_id.startswith("src/"):
            return node_id
    git_blame = violation.get("git_blame", {})
    file_path = git_blame.get("file_path")
    return file_path


def recommended_fix_hint(check_id: str, field: str) -> str:
    if "confidence" in check_id:
        return f"keep `{field}` on the 0.0-1.0 scale"
    if "entity_refs" in check_id:
        return f"ensure every `{field}` value resolves to an emitted entity_id"
    if "entities.type" in check_id:
        return f"restrict `{field}` to the accepted enum values"
    if "datetime" in check_id:
        return f"emit `{field}` as valid ISO-8601 timestamps"
    if "schema_version" in check_id:
        return f"emit `{field}` using the accepted schema-version enum"
    if "payload" in check_id:
        return f"include the required payload keys for `{field}`"
    return f"restore `{field}` to the contract-defined behavior"


def recommendation_for_result(result: dict, violation_lookup: dict[tuple[str, str, str], dict]) -> str:
    contract_id = canonical_contract_id(result.get("contract_id", ""))
    check_id = result.get("check_id", "")
    field = result.get("field") or "unknown_field"
    lookup_key = (contract_id, check_id, field)
    violation = violation_lookup.get(lookup_key)
    producer_path = candidate_file_path(violation) or result.get("source_data") or "upstream producer"
    fix_hint = recommended_fix_hint(check_id, field)
    return (
        f"Update `{producer_path}` so `{field}` satisfies clause `{check_id}` and {fix_hint}. "
        f"Keep `contracts/runner.py` enforcing this contract before downstream ingestion."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Data Contract Enforcer report.")
    parser.add_argument("--reports-dir", default="validation_reports")
    parser.add_argument("--violations-dir", default="violation_log")
    parser.add_argument("--registry", default="contract_registry/subscriptions.yaml")
    parser.add_argument("--output", default="enforcer_report/report_data.json")
    args = parser.parse_args()

    reports = load_validation_reports(ROOT / args.reports_dir)
    violations = load_violations(ROOT / args.violations_dir / "violations.jsonl")
    registry_path = ROOT / args.registry if not Path(args.registry).is_absolute() else Path(args.registry)
    registry_subscriptions = load_registry_subscriptions(registry_path) if registry_path.exists() else []
    violation_lookup = index_violations(violations)
    all_failures = unique_findings(reports)
    all_results = latest_results(reports)
    sorted_failures = sorted(
        all_failures,
        key=lambda result: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(result.get("severity", "LOW")),
    )
    ai_report_path = ROOT / args.reports_dir / "ai_extensions.json"
    ai_report = json.load(open(ai_report_path, "r", encoding="utf-8")) if ai_report_path.exists() else {"results": []}

    health_summary = compute_health_summary(all_results)
    health_score = health_summary["final_score"]
    recommendations = []
    for failure in sorted_failures[:3]:
        recommendation = recommendation_for_result(failure, violation_lookup)
        if recommendation not in recommendations:
            recommendations.append(recommendation)
    if len(recommendations) < 3:
        fallback = "Add contracts/runner.py and contracts/schema_analyzer.py to CI so producer-side changes and consumer-side ingestion checks run on every merge."
        if fallback not in recommendations:
            recommendations.append(fallback)
    report = {
        "generated_at": utc_now(),
        "period": f"{(datetime.now(timezone.utc) - timedelta(days=7)).date()} to {datetime.now(timezone.utc).date()}",
        "data_health_score": health_score,
        "health_score_calculation": health_summary,
        "health_narrative": (
            f"Score of {health_score}/100. "
            + (
                "No critical violations detected in the reporting window."
                if health_summary["critical_violations"] == 0
                else f"{health_summary['critical_violations']} critical issues require immediate attention."
            )
        ),
        "top_violations": [plain_language_violation(result, registry_subscriptions) for result in sorted_failures[:3]],
        "violations_by_severity": {
            severity: len([result for result in sorted_failures if result.get("severity") == severity])
            for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        },
        "total_violations_by_severity": {
            severity: len([result for result in sorted_failures if result.get("severity") == severity])
            for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        },
        "violation_count": len(violations),
        "schema_changes_detected": [],
        "ai_system_risk_assessment": ai_report.get("results", []),
        "recommended_actions": recommendations[:3],
    }

    schema_report_path = ROOT / args.reports_dir / "schema_evolution.json"
    if schema_report_path.exists():
        schema_report = json.load(open(schema_report_path, "r", encoding="utf-8"))
        report["schema_changes_detected"] = schema_report.get("changes", [])

    write_json(ROOT / args.output, report)
    print(f"Wrote report to {args.output}")


if __name__ == "__main__":
    main()
