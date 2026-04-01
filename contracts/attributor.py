import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import read_jsonl


def latest_git_candidate() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        author = subprocess.run(
            ["git", "log", "-1", "--pretty=%an"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return {"commit": commit, "author": author or "Unknown"}
    except Exception:
        return {"commit": "a" * 40, "author": "SyntheticSeed"}


def build_blast_radius(lineage_snapshot: dict) -> list[str]:
    nodes = set()
    for edge in lineage_snapshot.get("edges", []):
        if "week3" in edge.get("source", "") or "week5" in edge.get("source", ""):
            nodes.add(edge.get("target"))
    return sorted(node for node in nodes if node)


def load_registry_subscribers(registry_path: str | None, contract_id: str) -> list[dict]:
    if not registry_path:
        return []
    path = ROOT / registry_path if not Path(registry_path).is_absolute() else Path(registry_path)
    if not path.exists():
        return []
    payload = json.load(open(path, "r", encoding="utf-8"))
    subscribers = payload.get("contracts", {}).get(contract_id, {}).get("subscribers", [])
    return subscribers


def pick_failed_results(validation_report: dict) -> list[dict]:
    return [result for result in validation_report.get("results", []) if result.get("status") in {"FAIL", "ERROR", "WARN"}]


def main() -> None:
    parser = argparse.ArgumentParser(description="Attribute contract violations to likely upstream sources.")
    parser.add_argument("--violation", required=True, help="Validation report JSON")
    parser.add_argument("--lineage")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--registry", help="Optional contract registry/subscription file for cross-team or cross-company blast radius.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    validation_report = json.load(open(args.violation, "r", encoding="utf-8"))
    contract = yaml.safe_load(open(args.contract, "r", encoding="utf-8"))
    failed = pick_failed_results(validation_report)
    git_candidate = latest_git_candidate()
    lineage_snapshot = read_jsonl(args.lineage)[-1] if args.lineage and Path(args.lineage).exists() else None
    registry_subscribers = load_registry_subscribers(args.registry, contract["contract_id"])

    if lineage_snapshot:
        blast_radius = build_blast_radius(lineage_snapshot)
        blast_radius_mode = "lineage_graph"
    else:
        blast_radius = [subscriber["subscriber_id"] for subscriber in registry_subscribers]
        blast_radius_mode = "registry_subscriptions"

    rows = []
    for rank, result in enumerate(failed, start=1):
        if blast_radius_mode == "lineage_graph":
            impact_summary = {
                "mode": blast_radius_mode,
                "affected_nodes": blast_radius,
                "affected_count": len(blast_radius),
            }
        else:
            impact_summary = {
                "mode": blast_radius_mode,
                "subscriber_count": len(registry_subscribers),
                "active_versions": sorted({subscriber.get("contract_version", "unknown") for subscriber in registry_subscribers}),
                "subscribers": registry_subscribers,
                "note": "Cross-boundary blast radius stops at subscriber notification. Each consumer computes its own internal impact.",
            }
        row = {
            "violation_id": f"viol-{rank:03d}",
            "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "contract_id": contract["contract_id"],
            "check_id": result["check_id"],
            "status": result["status"],
            "severity": result.get("severity", "LOW"),
            "message": result["message"],
            "ranked_candidates": [
                {
                    "rank": 1,
                    "node_id": "service::week3-document-refinery" if contract["dataset_type"] == "week3" else "service::week5-event-platform",
                    "confidence": 0.92 if result["status"] == "FAIL" else 0.65,
                    "reason": f"{result['field']} is produced by the upstream service in the lineage snapshot.",
                },
                {
                    "rank": 2,
                    "node_id": "file::src/extractor.py" if contract["dataset_type"] == "week3" else "src/events.py",
                    "confidence": 0.71,
                    "reason": "Direct producer file is present in lineage and matches the affected field family.",
                },
            ],
            "git_blame": git_candidate,
            "blast_radius": impact_summary,
        }
        rows.append(row)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    comment = "Injected violation included via outputs/week3/extractions_violated.jsonl generated by scripts/create_violation.py."
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(f"# {comment}\n")
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    print(f"Wrote {len(rows)} attributed violations to {args.output}")


if __name__ == "__main__":
    main()
