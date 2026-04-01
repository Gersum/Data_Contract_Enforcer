import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import write_json


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


def compute_health_score(validation_reports: list[dict]) -> int:
    deductions = {"CRITICAL": 20, "HIGH": 10, "MEDIUM": 5, "LOW": 1}
    score = 100
    for report in validation_reports:
        for result in report.get("results", []):
            if result.get("status") in {"FAIL", "ERROR"}:
                score -= deductions.get(result.get("severity", "LOW"), 1)
            elif result.get("status") == "WARN":
                score -= max(1, deductions.get(result.get("severity", "LOW"), 1) // 2)
    return max(0, min(100, score))


def plain_language_violation(result: dict) -> str:
    field = result.get("field") or result.get("column_name", "unknown field")
    expected = result.get("expected", "contract expectation")
    actual = result.get("actual_value", "observed value")
    count = result.get("records_failing", "unknown")
    return f"The {field} field failed a {result.get('check_type', 'validation')} check. Expected {expected} but found {actual}. This affects {count} records."


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Data Contract Enforcer report.")
    parser.add_argument("--reports-dir", default="validation_reports")
    parser.add_argument("--violations-dir", default="violation_log")
    parser.add_argument("--output", default="enforcer_report/report_data.json")
    args = parser.parse_args()

    reports = load_validation_reports(ROOT / args.reports_dir)
    violations = load_violations(ROOT / args.violations_dir / "violations.jsonl")
    all_failures = [
        result
        for report in reports
        for result in report.get("results", [])
        if result.get("status") in {"FAIL", "ERROR", "WARN"}
    ]
    sorted_failures = sorted(
        all_failures,
        key=lambda result: ["CRITICAL", "HIGH", "MEDIUM", "LOW"].index(result.get("severity", "LOW")),
    )
    ai_report_path = ROOT / args.reports_dir / "ai_extensions.json"
    ai_report = json.load(open(ai_report_path, "r", encoding="utf-8")) if ai_report_path.exists() else {"results": []}

    health_score = compute_health_score(reports)
    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "period": f"{(datetime.now(timezone.utc) - timedelta(days=7)).date()} to {datetime.now(timezone.utc).date()}",
        "data_health_score": health_score,
        "health_narrative": (
            f"Score of {health_score}/100. "
            + (
                "No critical violations detected in the reporting window."
                if health_score >= 90
                else f"{len([r for r in sorted_failures if r.get('severity') == 'CRITICAL'])} critical issues require immediate attention."
            )
        ),
        "top_violations": [plain_language_violation(result) for result in sorted_failures[:3]],
        "total_violations_by_severity": {
            severity: len([result for result in sorted_failures if result.get("severity") == severity])
            for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        },
        "violation_count": len(violations),
        "schema_changes_detected": [],
        "ai_system_risk_assessment": ai_report.get("results", []),
        "recommended_actions": [
            "Update src/extractor.py and upstream generation logic so extracted_facts.confidence remains a float in the 0.0-1.0 range.",
            "Reject week3 records with dangling entity_refs or invalid entity types before they reach lineage and event consumers.",
            "Add contract generation and runner commands to CI so week3 and week5 interfaces are validated on every change.",
        ],
    }

    schema_report_path = ROOT / args.reports_dir / "schema_evolution.json"
    if schema_report_path.exists():
        schema_report = json.load(open(schema_report_path, "r", encoding="utf-8"))
        report["schema_changes_detected"] = schema_report.get("changes", [])

    write_json(ROOT / args.output, report)
    print(f"Wrote report to {args.output}")


if __name__ == "__main__":
    main()
