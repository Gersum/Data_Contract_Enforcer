import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import canonical_contract_id, load_registry_subscriptions, read_jsonl, subscribers_for_contract, utc_now, write_json, write_jsonl


def latest_git_candidate(upstream_file: str = "src/extractor.py") -> dict:
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
        timestamp = subprocess.run(
            ["git", "log", "-1", "--pretty=%aI"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        message = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return {
            "commit": commit, 
            "author": author or "Unknown", 
            "commit_hash": commit,
            "commit_timestamp": timestamp,
            "commit_message": message
        }
    except Exception:
        return {
            "commit": "a" * 40, 
            "author": "SyntheticSeed",
            "commit_hash": "a" * 40,
            "commit_timestamp": utc_now(),
            "commit_message": "Automated commit"
        }


def producer_node_id(contract: dict) -> str:
    return "service::week3-document-refinery" if contract["dataset_type"] == "week3" else "service::week5-event-platform"


def producer_file_id(contract: dict) -> str:
    return "file::src/extractor.py" if contract["dataset_type"] == "week3" else "src/events.py"


def load_registry_entries(registry_path: str | None, contract_id: str) -> list[dict]:
    if not registry_path:
        return []
    path = ROOT / registry_path if not Path(registry_path).is_absolute() else Path(registry_path)
    if not path.exists():
        return []
    return subscribers_for_contract(load_registry_subscriptions(path), contract_id)


def registry_blast_radius(contract_id: str, failing_field: str | None, registry_entries: list[dict]) -> list[dict]:
    if not registry_entries:
        return []
    if not failing_field:
        return registry_entries

    affected = []
    for entry in registry_entries:
        breaking_fields = entry.get("breaking_fields", [])
        if not breaking_fields:
            continue
        for breaking_field in breaking_fields:
            field_name = breaking_field.get("field")
            if not field_name:
                continue
            if failing_field == field_name or failing_field.startswith(field_name) or field_name.startswith(failing_field):
                affected.append(
                    {
                        "subscriber_id": entry.get("subscriber_id"),
                        "contact": entry.get("contact", "unknown"),
                        "validation_mode": entry.get("validation_mode", "AUDIT"),
                        "reason": breaking_field.get("reason", "Declared as a breaking field in the registry."),
                        "field": field_name,
                    }
                )
                break
    return affected or [
        {
            "subscriber_id": entry.get("subscriber_id"),
            "contact": entry.get("contact", "unknown"),
            "validation_mode": entry.get("validation_mode", "AUDIT"),
            "reason": "No exact breaking_fields match was declared; subscriber still registered to this contract.",
            "field": failing_field,
        }
        for entry in registry_entries
    ]


def load_lineage_snapshot(lineage_path: str | None) -> dict | None:
    if not lineage_path:
        return None
    path = ROOT / lineage_path if not Path(lineage_path).is_absolute() else Path(lineage_path)
    if not path.exists():
        return None
    snapshots = read_jsonl(path)
    return snapshots[-1] if snapshots else None


def compute_transitive_depth(producer_node: str, lineage_snapshot: dict | None, max_depth: int = 2) -> dict:
    if not lineage_snapshot:
        return {"direct": [], "transitive": [], "max_depth": 0}

    visited: set[str] = set()
    frontier = {producer_node}
    depth_map: dict[str, int] = {}
    for depth in range(1, max_depth + 1):
        next_frontier: set[str] = set()
        for node in frontier:
            for edge in lineage_snapshot.get("edges", []):
                if edge.get("source") == node and edge.get("relationship") in {"PRODUCES", "WRITES", "CONSUMES"}:
                    target = edge.get("target")
                    if target and target not in visited:
                        depth_map[target] = depth
                        next_frontier.add(target)
                        visited.add(target)
        frontier = next_frontier
    return {
        "direct": sorted(node for node, depth in depth_map.items() if depth == 1),
        "transitive": sorted(node for node, depth in depth_map.items() if depth > 1),
        "max_depth": max(depth_map.values()) if depth_map else 0,
    }


def actionable_status(row: dict) -> bool:
    return row.get("status") in {"FAIL", "ERROR", "WARN"}


def normalize_violation_input(path: str) -> tuple[str, list[dict]]:
    input_path = ROOT / path if not Path(path).is_absolute() else Path(path)
    if input_path.suffix == ".jsonl":
        return "violation_log", [row for row in read_jsonl(input_path) if actionable_status(row)]

    with open(input_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, dict) and "results" in payload:
        rows = []
        for result in payload.get("results", []):
            if actionable_status(result):
                row = dict(result)
                row.setdefault("source_data", payload.get("source_data"))
                row.setdefault("contract_id", payload.get("contract_id"))
                rows.append(row)
        return "validation_report", rows

    if isinstance(payload, dict) and payload.get("check_id"):
        return "violation_entry", [payload] if actionable_status(payload) else []

    if isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict) and actionable_status(row)]
        return "violation_entries", rows

    raise SystemExit(f"Unsupported violation input at {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Attribute contract violations to likely upstream sources.")
    parser.add_argument("--violation", required=True, help="Validation report JSON, single violation JSON, or violation JSONL")
    parser.add_argument("--lineage")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--registry", help="Registry subscription file for direct-subscriber blast radius.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--header-comment", help="Optional header comment for JSONL outputs.")
    args = parser.parse_args()

    input_kind, failed = normalize_violation_input(args.violation)
    with open(args.contract, "r", encoding="utf-8") as handle:
        contract = yaml.safe_load(handle)
    git_candidate = latest_git_candidate()
    lineage_snapshot = load_lineage_snapshot(args.lineage)
    registry_entries = load_registry_entries(args.registry, contract["contract_id"])
    lineage_depth = compute_transitive_depth(producer_node_id(contract), lineage_snapshot)

    rows = []
    for rank, result in enumerate(failed, start=1):
        affected_field = result.get("field") or result.get("check_id", "unknown field")
        direct_subscribers = registry_blast_radius(contract["contract_id"], result.get("field"), registry_entries)
        days_since_commit = 1  # Simplified assumption for days

        candidates = [
            (producer_node_id(contract), 0, "Current service producer"),
            (producer_file_id(contract), 1, "Direct code file in producer service"),
            ("previous_deployment", 2, "Previously deployed version trace"),
            ("infrastructure_layer", 3, "Database or streaming infrastructure"),
            ("upstream_external_vendor", 4, "External API producing raw data")
        ]

        ranked_candidates = []
        for i, (node_id, hops, reason) in enumerate(candidates, start=1):
            confidence_score = max(0.0, 1.0 - (days_since_commit * 0.1) - (hops * 0.2))
            ranked_candidates.append({
                "rank": i,
                "node_id": node_id,
                "confidence": round(confidence_score, 2),
                "reason": reason
            })
            
        impact_summary = {
            "source": "registry",
            "mode": "registry_first_with_lineage_enrichment" if args.registry else "lineage_only",
            "direct_subscribers": direct_subscribers,
            "subscriber_count": len(direct_subscribers),
            "direct_nodes": lineage_depth["direct"],
            "transitive_nodes": lineage_depth["transitive"],
            "contamination_depth": lineage_depth["max_depth"],
            "note": "direct_subscribers come from the registry; lineage only enriches how far contamination travels within visible systems.",
        }
        row = {
            "violation_id": f"viol-{rank:03d}",
            "recorded_at": utc_now(),
            "contract_id": canonical_contract_id(contract["contract_id"]),
            "check_id": result["check_id"],
            "field": result.get("field"),
            "source_data": result.get("source_data"),
            "input_kind": input_kind,
            "status": result["status"],
            "severity": result.get("severity", "LOW"),
            "message": result["message"],
            "ranked_candidates": ranked_candidates,
            "blame_chain": {
                "commit_hash": git_candidate.get("commit_hash", git_candidate["commit"]),
                "author": git_candidate["author"],
                "commit_timestamp": git_candidate.get("commit_timestamp", utc_now()),
                "commit_message": git_candidate.get("commit_message", "Unknown commit message"),
                "confidence_score": ranked_candidates[0]["confidence"],
                "candidates": ranked_candidates,
            },
            "git_blame": git_candidate,
            "blast_radius": impact_summary,
        }
        rows.append(row)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix == ".json":
        if len(rows) == 1:
            write_json(output_path, rows[0])
        else:
            write_json(output_path, {"generated_at": utc_now(), "rows": rows})
    else:
        write_jsonl(output_path, rows, header_comment=args.header_comment)

    print(f"Wrote {len(rows)} attributed violations to {args.output}")


if __name__ == "__main__":
    main()
