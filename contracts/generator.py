import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import (
    build_column_profile,
    CANONICAL_CONTRACT_IDS,
    canonical_contract_id,
    flatten_week3,
    flatten_week5,
    histogram,
    load_registry_subscriptions,
    read_jsonl,
    sha256_text,
    subscribers_for_contract,
    summarize_enum,
    utc_now,
    write_json,
)


def profile_records(records: list[dict], dataset_type: str) -> tuple[list[dict], dict[str, dict]]:
    if dataset_type == "week3":
        _, values_by_column = flatten_week3(records)
    elif dataset_type == "week5":
        _, values_by_column = flatten_week5(records)
    else:
        raise ValueError(f"Unsupported dataset type: {dataset_type}")

    profiles = {column: build_column_profile(values) for column, values in values_by_column.items()}
    return records, profiles


def infer_dataset_type(source_path: str) -> str:
    lowered = source_path.lower()
    if "week3" in lowered or "extraction" in lowered:
        return "week3"
    if "week5" in lowered or "event" in lowered:
        return "week5"
    raise ValueError("Could not infer dataset type from source path. Use week3/week5 naming.")


def default_contract_id(dataset_type: str) -> str:
    return CANONICAL_CONTRACT_IDS[dataset_type]


def base_contract(contract_id: str, source_path: str, dataset_type: str) -> dict:
    return {
        "contract_id": contract_id,
        "dataset_type": dataset_type,
        "source_path": source_path,
        "generated_at": utc_now(),
        "version": "1.0.0",
        "clauses": [],
        "lineage": {"upstream": [], "downstream": []},
    }


def append_clause(contract: dict, check_id: str, field: str, check_type: str, severity: str, **extra: object) -> None:
    clause = {
        "check_id": check_id,
        "field": field,
        "check_type": check_type,
        "severity": severity,
    }
    clause.update(extra)
    contract["clauses"].append(clause)


def add_week3_clauses(contract: dict, records: list[dict], profiles: dict[str, dict]) -> None:
    append_clause(contract, "week3.doc_id.required", "doc_id", "required", "CRITICAL", expected=True)
    append_clause(contract, "week3.doc_id.uuid", "doc_id", "uuid", "CRITICAL")
    append_clause(contract, "week3.extracted_at.datetime", "extracted_at", "datetime", "HIGH")
    append_clause(contract, "week3.processing_time_ms.positive", "processing_time_ms", "min", "HIGH", minimum=1)
    append_clause(
        contract,
        "week3.extracted_facts.confidence.range",
        "extracted_facts.confidence",
        "range",
        "CRITICAL",
        minimum=0.0,
        maximum=1.0,
        description="Confidence score must remain a float in the 0.0-1.0 range.",
    )
    append_clause(
        contract,
        "week3.entities.type.enum",
        "entities.type",
        "enum",
        "HIGH",
        allowed_values=["PERSON", "ORG", "LOCATION", "DATE", "AMOUNT", "OTHER"],
    )
    append_clause(
        contract,
        "week3.extracted_facts.entity_refs.valid",
        "extracted_facts.entity_refs_valid",
        "boolean_truth",
        "CRITICAL",
        expected=True,
    )
    append_clause(contract, "week3.fact_count.non_zero", "fact_count", "min", "HIGH", minimum=1)
    append_clause(contract, "week3.entity_count.non_zero", "entity_count", "min", "HIGH", minimum=1)
    append_clause(contract, "week3.token_count.input.positive", "token_count.input", "min", "MEDIUM", minimum=1)
    append_clause(contract, "week3.token_count.output.positive", "token_count.output", "min", "MEDIUM", minimum=1)
    append_clause(
        contract,
        "week3.extraction_model.enum",
        "extraction_model",
        "enum",
        "LOW",
        allowed_values=summarize_enum([record.get("extraction_model") for record in records]) or [],
    )

    contract["summary"] = {
        "record_count": len(records),
        "fact_confidence_profile": profiles.get("extracted_facts.confidence", {}),
        "entity_type_distribution": histogram([entity.get("type", "UNKNOWN") for record in records for entity in record.get("entities", [])]),
    }


def add_week5_clauses(contract: dict, records: list[dict], profiles: dict[str, dict]) -> None:
    append_clause(contract, "week5.event_id.uuid", "event_id", "uuid", "CRITICAL")
    append_clause(contract, "week5.aggregate_id.uuid", "aggregate_id", "uuid", "CRITICAL")
    append_clause(contract, "week5.event_type.pascal_case", "event_type", "pattern", "CRITICAL", pattern="^[A-Z][A-Za-z0-9]+$")
    append_clause(contract, "week5.event_type.registry", "event_type", "registry_membership", "CRITICAL", registry_path="schemas/event_registry.json")
    append_clause(contract, "week5.aggregate_type.pascal_case", "aggregate_type", "pattern", "HIGH", pattern="^[A-Z][A-Za-z0-9]+$")
    append_clause(contract, "week5.sequence_number.positive", "sequence_number", "min", "CRITICAL", minimum=1)
    append_clause(contract, "week5.sequence_number.monotonic", "sequence_number", "monotonic_per_aggregate", "CRITICAL")
    append_clause(contract, "week5.occurred_at.datetime", "occurred_at", "datetime", "HIGH")
    append_clause(contract, "week5.recorded_at.datetime", "recorded_at", "datetime", "HIGH")
    append_clause(contract, "week5.recorded_at.after_occurred_at", "recorded_at", "temporal_order", "CRITICAL", reference_field="occurred_at")
    append_clause(
        contract,
        "week5.schema_version.enum",
        "schema_version",
        "enum",
        "MEDIUM",
        allowed_values=summarize_enum([record.get("schema_version") for record in records]) or [],
    )
    append_clause(
        contract,
        "week5.metadata.source_service.enum",
        "metadata.source_service",
        "enum",
        "MEDIUM",
        allowed_values=summarize_enum([record.get("metadata", {}).get("source_service") for record in records]) or [],
    )
    append_clause(
        contract,
        "week5.payload.required_keys",
        "payload_keys",
        "superset",
        "HIGH",
        required_keys=["document_id", "status", "fact_count"],
    )
    contract["summary"] = {
        "record_count": len(records),
        "sequence_profile": profiles.get("sequence_number", {}),
        "event_type_distribution": histogram([record.get("event_type", "UNKNOWN") for record in records]),
    }


def inject_context(contract: dict, lineage_path: str | None, registry_path: str | None) -> None:
    producer_nodes = {
        "week3": "service::week3-document-refinery",
        "week5": "service::week5-event-platform",
    }
    producer_node_id = producer_nodes[contract["dataset_type"]]
    lineage_context = {
        "upstream": [],
        "downstream": [],
        "downstream_nodes_from_lineage": [],
        "registry_subscribers": [],
        "note": "Blast radius uses registry_subscribers as the primary source. Lineage nodes are enrichment only.",
    }

    if lineage_path and Path(lineage_path).exists():
        snapshots = read_jsonl(lineage_path)
        if snapshots:
            latest = snapshots[-1]
            upstream = []
            downstream = []
            for edge in latest.get("edges", []):
                if edge.get("target") == producer_node_id:
                    upstream.append(
                        {
                            "id": edge.get("source"),
                            "relationship": edge.get("relationship"),
                            "confidence": edge.get("confidence"),
                        }
                    )
                if edge.get("source") == producer_node_id and edge.get("relationship") in {"PRODUCES", "WRITES", "CONSUMES"}:
                    downstream.append(
                        {
                            "node_id": edge.get("target"),
                            "relationship": edge.get("relationship"),
                            "confidence": edge.get("confidence"),
                        }
                    )
            lineage_context["upstream"] = upstream
            lineage_context["downstream"] = downstream
            lineage_context["downstream_nodes_from_lineage"] = downstream

    if registry_path and Path(registry_path).exists():
        registry_subscriptions = subscribers_for_contract(load_registry_subscriptions(registry_path), contract["contract_id"])
        lineage_context["registry_subscribers"] = [
            {
                "subscriber_id": subscription.get("subscriber_id"),
                "validation_mode": subscription.get("validation_mode", "AUDIT"),
                "contact": subscription.get("contact"),
                "breaking_fields": subscription.get("breaking_fields", []),
            }
            for subscription in registry_subscriptions
        ]

    contract["lineage"] = lineage_context
    contract["registry"] = {
        "path": registry_path,
        "contract_id": canonical_contract_id(contract["contract_id"]),
    }


def contract_filename(contract_id: str) -> str:
    return contract_id.replace("-", "_")


def contract_aliases(contract_id: str, dataset_type: str) -> list[str]:
    aliases = {contract_filename(contract_id)}
    if dataset_type == "week3":
        aliases.add("week3_extractions")
    elif dataset_type == "week5":
        aliases.add("week5_events")
    return sorted(aliases)


def add_column_test(columns: list[dict], field: str, test: object) -> None:
    column = next((candidate for candidate in columns if candidate["name"] == field), None)
    if column is None:
        column = {"name": field, "tests": []}
        columns.append(column)
    if test not in column["tests"]:
        column["tests"].append(test)


def load_registry_values(registry_path: str) -> list[str]:
    path = ROOT / registry_path
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return sorted(payload.keys())


def dbt_tests_for_clause(clause: dict) -> tuple[list[tuple[str, object]], list[object]]:
    column_tests: list[tuple[str, object]] = []
    model_tests: list[object] = []

    if clause["check_type"] == "required":
        column_tests.append((clause["field"], "not_null"))
    elif clause["check_type"] == "uuid":
        column_tests.append(
            (
                clause["field"],
                {
                    "dbt_expectations.expect_column_values_to_match_regex": {
                        "regex": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
                    }
                },
            )
        )
    elif clause["check_type"] == "datetime":
        column_tests.append(
            (
                clause["field"],
                {
                    "dbt_expectations.expect_column_values_to_match_regex": {
                        "regex": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"
                    }
                },
            )
        )
    elif clause["check_type"] == "min":
        column_tests.append(
            (
                clause["field"],
                {
                    "dbt_expectations.expect_column_values_to_be_between": {
                        "min_value": clause["minimum"]
                    }
                },
            )
        )
    elif clause["check_type"] == "range":
        column_tests.append(
            (
                clause["field"],
                {
                    "dbt_expectations.expect_column_values_to_be_between": {
                        "min_value": clause["minimum"],
                        "max_value": clause["maximum"],
                    }
                },
            )
        )
    elif clause["check_type"] == "enum":
        column_tests.append((clause["field"], {"accepted_values": {"values": clause["allowed_values"]}}))
    elif clause["check_type"] == "boolean_truth":
        column_tests.append((clause["field"], {"accepted_values": {"values": [True]}}))
    elif clause["check_type"] == "pattern":
        column_tests.append(
            (
                clause["field"],
                {
                    "dbt_expectations.expect_column_values_to_match_regex": {
                        "regex": clause["pattern"]
                    }
                },
            )
        )
    elif clause["check_type"] == "registry_membership":
        allowed_values = load_registry_values(clause["registry_path"])
        if allowed_values:
            column_tests.append((clause["field"], {"accepted_values": {"values": allowed_values}}))
    elif clause["check_type"] == "temporal_order":
        model_tests.append(
            {
                "dbt_utils.expression_is_true": {
                    "expression": f"{clause['field']} >= {clause['reference_field']}"
                }
            }
        )
    elif clause["check_type"] == "monotonic_per_aggregate":
        model_tests.append(
            {
                "dbt_utils.unique_combination_of_columns": {
                    "combination_of_columns": ["aggregate_id", "sequence_number"]
                }
            }
        )

    return column_tests, model_tests


def write_dbt_counterpart(contract: dict, output_dir: Path) -> Path:
    model_name = contract_filename(contract["contract_id"])
    columns: list[dict] = []
    model_tests: list[object] = []
    for clause in contract["clauses"]:
        clause_column_tests, clause_model_tests = dbt_tests_for_clause(clause)
        for field, test in clause_column_tests:
            add_column_test(columns, field, test)
        for model_test in clause_model_tests:
            if model_test not in model_tests:
                model_tests.append(model_test)

    model_payload = {"name": model_name, "columns": columns}
    if model_tests:
        model_payload["tests"] = model_tests
    payload = {"version": 2, "models": [model_payload]}
    output_path = output_dir / f"{model_name}_dbt.yml"
    with open(output_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a YAML data contract from JSONL records.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--contract-id")
    parser.add_argument("--lineage")
    parser.add_argument("--registry")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    dataset_type = infer_dataset_type(args.source)
    contract_id = args.contract_id or default_contract_id(dataset_type)
    records = read_jsonl(args.source)
    if not records:
        raise SystemExit("No records found in source JSONL.")

    records, profiles = profile_records(records, dataset_type)
    contract = base_contract(contract_id, args.source, dataset_type)
    contract["profile_hash"] = sha256_text(json.dumps(profiles, sort_keys=True, default=str))
    contract["column_profiles"] = profiles

    if dataset_type == "week3":
        add_week3_clauses(contract, records, profiles)
    elif dataset_type == "week5":
        add_week5_clauses(contract, records, profiles)

    inject_context(contract, args.lineage, args.registry)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    yaml_paths = []
    for alias in contract_aliases(contract_id, dataset_type):
        yaml_path = output_dir / f"{alias}.yaml"
        with open(yaml_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(contract, handle, sort_keys=False, allow_unicode=False)
        yaml_paths.append(yaml_path)

    dbt_path = write_dbt_counterpart(contract, output_dir)
    alias_map = {"week3": "week3_extractions_dbt.yml", "week5": "week5_events_dbt.yml"}
    expected_dbt_path = output_dir / alias_map[dataset_type]
    with open(dbt_path, "r", encoding="utf-8") as handle:
        dbt_payload = yaml.safe_load(handle)
    with open(expected_dbt_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(dbt_payload, handle, sort_keys=False, allow_unicode=False)

    snapshot = {
        "contract_id": contract["contract_id"],
        "generated_at": contract["generated_at"],
        "profile_hash": contract["profile_hash"],
        "column_profiles": contract["column_profiles"],
        "lineage": contract.get("lineage", {}),
    }
    snapshot_name = contract["generated_at"].replace(":", "").replace("-", "")
    snapshot_path = ROOT / "schema_snapshots" / f"{contract_filename(contract_id)}_{snapshot_name}.json"
    snapshot_dir_path = ROOT / "schema_snapshots" / contract_filename(contract_id) / f"{snapshot_name}.json"
    write_json(snapshot_path, snapshot)
    write_json(snapshot_dir_path, snapshot)

    print(f"Generated {yaml_paths[0]}")


if __name__ == "__main__":
    main()
