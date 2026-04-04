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


def recommendation_for_result(result: dict) -> str:
    check_id = result.get("check_id", "")
    if "confidence" in check_id:
        return (
            "Update src/extractor.py so extracted_facts.confidence continues to satisfy clause "
            "`week3.extracted_facts.confidence.range` with values in the 0.0-1.0 range, and keep "
            "contracts/runner.py in CI to block future scale drift."
        )
    if "entity_refs" in check_id:
        return (
            "Update src/extractor.py so extracted_facts.entity_refs only reference emitted entity_ids and satisfy "
            "clause `week3.extracted_facts.entity_refs.valid` before Week 3 records are published."
        )
    if "entities.type" in check_id:
        return (
            "Update src/extractor.py so entities.type only emits accepted values for clause "
            "`week3.entities.type.enum` before Week 3 records are published."
        )
    if check_id.startswith("week5."):
        return (
            "Normalize occurred_at, recorded_at, and schema_version in outputs/migrate/create_week5_direct_import.py "
            "and src/events.py so clauses `week5.occurred_at.datetime`, `week5.recorded_at.datetime`, and "
            "`week5.schema_version.enum` pass before ingestion."
        )
    return (
        "Keep contracts/runner.py and contracts/schema_analyzer.py in CI so clause-level structural changes are "
        "caught before downstream consumers ingest them."
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
        recommendation = recommendation_for_result(failure)
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
