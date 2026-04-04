import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PASCAL_CASE_RE = re.compile(r"^[A-Z][A-Za-z0-9]+$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

CANONICAL_CONTRACT_IDS = {
    "week3": "week3-document-refinery-extractions",
    "week5": "week5-event-records",
}

CONTRACT_ID_ALIASES = {
    "week3-document-refinery-extractions": {
        "week3-document-refinery-extractions",
        "week3-extractions",
        "week3_document_refinery_extractions",
        "week3_extractions",
    },
    "week5-event-records": {
        "week5-event-records",
        "week5-events",
        "week5-event-sourcing-events",
        "week5_event_records",
        "week5_events",
        "week5_event_sourcing_events",
    },
}


def ensure_parent(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]], header_comment: str | None = None) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        if header_comment:
            handle.write(f"# {header_comment}\n")
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    ensure_parent(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)


def read_structured_file(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        suffix = Path(path).suffix.lower()
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(handle) or {}
        return json.load(handle)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_iso8601(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_contract_id(contract_id: str) -> str:
    for canonical, aliases in CONTRACT_ID_ALIASES.items():
        if contract_id in aliases:
            return canonical
    return contract_id


def contract_id_candidates(contract_id: str) -> set[str]:
    canonical = canonical_contract_id(contract_id)
    return CONTRACT_ID_ALIASES.get(canonical, {contract_id, canonical})


def load_registry_subscriptions(path: str | Path) -> list[dict[str, Any]]:
    payload = read_structured_file(path)
    if isinstance(payload, dict) and "subscriptions" in payload:
        return payload.get("subscriptions", [])

    subscriptions: list[dict[str, Any]] = []
    for contract_id, contract_payload in payload.get("contracts", {}).items():
        for subscriber in contract_payload.get("subscribers", []):
            subscriptions.append(
                {
                    "contract_id": contract_id,
                    "subscriber_id": subscriber.get("subscriber_id"),
                    "fields_consumed": subscriber.get("fields_consumed", []),
                    "breaking_fields": subscriber.get("breaking_fields", []),
                    "validation_mode": subscriber.get("validation_mode", "AUDIT"),
                    "registered_at": subscriber.get("registered_at"),
                    "contact": subscriber.get("contact") or subscriber.get("notification_target"),
                    "organization": subscriber.get("organization"),
                    "contract_version": subscriber.get("contract_version"),
                    "tier": subscriber.get("tier"),
                }
            )
    return subscriptions


def subscribers_for_contract(subscriptions: list[dict[str, Any]], contract_id: str) -> list[dict[str, Any]]:
    candidates = contract_id_candidates(contract_id)
    return [subscription for subscription in subscriptions if subscription.get("contract_id") in candidates]


def is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(UUID_RE.fullmatch(value))


def is_semver(value: Any) -> bool:
    return isinstance(value, str) and bool(SEMVER_RE.fullmatch(value))


def is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not ISO_Z_RE.fullmatch(value):
        return False
    try:
        parse_iso8601(value)
    except ValueError:
        return False
    return True


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def infer_type(values: list[Any]) -> str:
    observed = [value for value in values if value is not None]
    if not observed:
        return "string"
    if all(isinstance(value, bool) for value in observed):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in observed):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in observed):
        return "number"
    if all(isinstance(value, list) for value in observed):
        return "array"
    if all(isinstance(value, dict) for value in observed):
        return "object"
    return "string"


def flatten_week3(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    rows: list[dict[str, Any]] = []
    profile: defaultdict[str, list[Any]] = defaultdict(list)
    for record in records:
        base = {
            "doc_id": record.get("doc_id"),
            "source_path": record.get("source_path"),
            "source_hash": record.get("source_hash"),
            "extraction_model": record.get("extraction_model"),
            "processing_time_ms": record.get("processing_time_ms"),
            "token_count.input": record.get("token_count", {}).get("input"),
            "token_count.output": record.get("token_count", {}).get("output"),
            "extracted_at": record.get("extracted_at"),
            "entity_count": len(record.get("entities", [])),
            "fact_count": len(record.get("extracted_facts", [])),
        }
        for key, value in base.items():
            profile[key].append(value)

        entity_ids = {entity.get("entity_id") for entity in record.get("entities", [])}
        for entity in record.get("entities", []):
            profile["entities.entity_id"].append(entity.get("entity_id"))
            profile["entities.type"].append(entity.get("type"))
            profile["entities.name"].append(entity.get("name"))
            profile["entities.canonical_value"].append(entity.get("canonical_value"))
        for fact in record.get("extracted_facts", []):
            row = dict(base)
            row.update(
                {
                    "extracted_facts.fact_id": fact.get("fact_id"),
                    "extracted_facts.confidence": fact.get("confidence"),
                    "extracted_facts.page_ref": fact.get("page_ref"),
                    "extracted_facts.text": fact.get("text"),
                    "extracted_facts.source_excerpt": fact.get("source_excerpt"),
                    "extracted_facts.entity_ref_count": len(fact.get("entity_refs", [])),
                    "extracted_facts.entity_refs_valid": all(ref in entity_ids for ref in fact.get("entity_refs", [])),
                }
            )
            rows.append(row)
            for key, value in row.items():
                profile[key].append(value)
    return rows, dict(profile)


def flatten_week5(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    rows: list[dict[str, Any]] = []
    profile: defaultdict[str, list[Any]] = defaultdict(list)
    for record in records:
        row = {
            "event_id": record.get("event_id"),
            "event_type": record.get("event_type"),
            "aggregate_id": record.get("aggregate_id"),
            "aggregate_type": record.get("aggregate_type"),
            "sequence_number": record.get("sequence_number"),
            "schema_version": record.get("schema_version"),
            "occurred_at": record.get("occurred_at"),
            "recorded_at": record.get("recorded_at"),
            "metadata.correlation_id": record.get("metadata", {}).get("correlation_id"),
            "metadata.causation_id": record.get("metadata", {}).get("causation_id"),
            "metadata.source_service": record.get("metadata", {}).get("source_service"),
            "payload_keys": sorted(record.get("payload", {}).keys()),
            "payload_size": len(record.get("payload", {})),
        }
        rows.append(row)
        for key, value in row.items():
            profile[key].append(value)
    return rows, dict(profile)


def build_column_profile(values: list[Any]) -> dict[str, Any]:
    observed = [value for value in values if value is not None]
    inferred = infer_type(values)
    profile = {
        "dtype": inferred,
        "null_fraction": 0.0 if not values else round(sum(value is None for value in values) / len(values), 4),
        "cardinality_estimate": len({json.dumps(value, sort_keys=True) for value in observed}),
        "sample_values": observed[:5],
    }
    if inferred in {"integer", "number"} and observed:
        numeric = [float(value) for value in observed]
        profile["stats"] = {
            "min": min(numeric),
            "max": max(numeric),
            "mean": mean(numeric),
            "p25": percentile(numeric, 0.25),
            "p50": percentile(numeric, 0.50),
            "p75": percentile(numeric, 0.75),
            "p95": percentile(numeric, 0.95),
            "p99": percentile(numeric, 0.99),
            "stddev": stddev(numeric),
        }
    return profile


def summarize_enum(values: list[Any], limit: int = 10) -> list[Any] | None:
    observed = [value for value in values if value is not None]
    uniques = list({json.dumps(value, sort_keys=True): value for value in observed}.values())
    if len(uniques) <= limit:
        return uniques
    return None


def histogram(values: list[str]) -> dict[str, int]:
    return dict(Counter(values))
