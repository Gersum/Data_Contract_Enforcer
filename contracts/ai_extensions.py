import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

try:
    from jsonschema import ValidationError, validate
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal evaluator environments
    ValidationError = ValueError

    def validate(instance: dict, schema: dict) -> None:
        if schema.get("type") != "object":
            return
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        additional_allowed = schema.get("additionalProperties", True)

        for field in required:
            if field not in instance:
                raise ValidationError(f"'{field}' is a required property")

        if not additional_allowed:
            extras = set(instance) - set(properties)
            if extras:
                extra = sorted(extras)[0]
                raise ValidationError(f"Additional properties are not allowed ('{extra}' was unexpected)")

        for field, rules in properties.items():
            if field not in instance:
                continue
            value = instance[field]
            if rules.get("type") == "string":
                if not isinstance(value, str):
                    raise ValidationError(f"'{field}' must be a string")
                if "minLength" in rules and len(value) < rules["minLength"]:
                    raise ValidationError(f"'{field}' is too short")
                if "maxLength" in rules and len(value) > rules["maxLength"]:
                    raise ValidationError(f"'{field}' is too long")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import read_jsonl, utc_now, write_json, write_jsonl


EMBEDDING_BASELINE_PATH = ROOT / "schema_snapshots" / "embedding_baselines.npz"
OUTPUT_RATE_BASELINE_PATH = ROOT / "schema_snapshots" / "ai_output_baselines.json"
QUARANTINE_PATH = ROOT / "outputs" / "quarantine" / "quarantine.jsonl"
EMBEDDING_DIM = 256

WEEK3_PROMPT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["doc_id", "source_path", "content_preview"],
    "properties": {
        "doc_id": {"type": "string", "minLength": 36, "maxLength": 36},
        "source_path": {"type": "string", "minLength": 1},
        "content_preview": {"type": "string", "maxLength": 8000},
    },
    "additionalProperties": False,
}


def tokenize(text: str) -> list[str]:
    cleaned = text.replace(".", " ").replace(",", " ").replace("\n", " ")
    return [token.lower() for token in cleaned.split() if token]


def hashed_embedding(text: str, dim: int = EMBEDDING_DIM) -> np.ndarray:
    vector = np.zeros(dim, dtype=float)
    for token in tokenize(text):
        digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
        index = int(digest, 16) % dim
        vector[index] += 1.0
    norm = np.linalg.norm(vector)
    return vector if norm == 0 else vector / norm


def embed_sample(texts: list[str], n: int = 200) -> np.ndarray:
    sample = [text for text in texts[:n] if text]
    if not sample:
        return np.zeros((0, EMBEDDING_DIM), dtype=float)
    return np.vstack([hashed_embedding(text) for text in sample])


def check_embedding_drift(texts: list[str], baseline_path: Path = EMBEDDING_BASELINE_PATH, threshold: float = 0.15) -> dict:
    vectors = embed_sample(texts)
    sample_count = int(len(vectors))
    centroid = vectors.mean(axis=0) if len(vectors) else np.zeros(EMBEDDING_DIM, dtype=float)
    if not baseline_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(baseline_path, centroid=centroid, sample_count=sample_count)
        return {
            "name": "embedding_drift",
            "status": "PASS",
            "drift_score": 0.0,
            "threshold": threshold,
            "baseline_size": sample_count,
            "current_size": sample_count,
            "interpretation": "Baseline established from current extracted_facts text values.",
            "embedding_model": "local-hashed-embedding",
        }

    baseline_payload = np.load(baseline_path)
    baseline = baseline_payload["centroid"]
    baseline_size = int(baseline_payload["sample_count"]) if "sample_count" in baseline_payload.files else sample_count
    similarity = float(np.dot(centroid, baseline) / (np.linalg.norm(centroid) * np.linalg.norm(baseline) + 1e-9))
    drift = round(1 - similarity, 4)
    if drift > threshold:
        status = "FAIL"
    elif drift > threshold * 0.66:
        status = "WARN"
    else:
        status = "PASS"
    return {
        "name": "embedding_drift",
        "status": status,
        "drift_score": drift,
        "threshold": threshold,
        "baseline_size": baseline_size,
        "current_size": sample_count,
        "interpretation": "semantic content shifted" if drift > threshold else "stable",
        "embedding_model": "local-hashed-embedding",
    }


def build_prompt_input_records(extractions_path: str) -> list[dict]:
    records = read_jsonl(extractions_path)
    prompt_records = []
    for record in records:
        preview = " ".join(fact.get("text", "") for fact in record.get("extracted_facts", []))[:8000]
        prompt_records.append(
            {
                "doc_id": record.get("doc_id"),
                "source_path": record.get("source_path"),
                "content_preview": preview,
            }
        )
    return prompt_records


def validate_prompt_inputs(records: list[dict], schema: dict, quarantine_path: Path = QUARANTINE_PATH) -> dict:
    valid = []
    quarantined = []
    for record in records:
        try:
            validate(instance=record, schema=schema)
            valid.append(record)
        except ValidationError as error:
            quarantined.append({"record": record, "error": error.message, "path": list(error.path)})
    if quarantined:
        write_jsonl(quarantine_path, quarantined)
    return {
        "name": "prompt_input_schema",
        "status": "PASS" if not quarantined else "FAIL",
        "valid": len(valid),
        "quarantined": len(quarantined),
        "sample_values": [entry["record"].get("doc_id") for entry in quarantined[:5]],
        "quarantine_path": str(quarantine_path.relative_to(ROOT)),
    }


def load_output_rate_baseline(path: Path = OUTPUT_RATE_BASELINE_PATH) -> float | None:
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("output_violation_rate")


def write_output_rate_baseline(rate: float, path: Path = OUTPUT_RATE_BASELINE_PATH) -> None:
    write_json(path, {"written_at": utc_now(), "output_violation_rate": rate})


def check_output_violation_rate(
    outputs: list[dict],
    expected_enum_field: str,
    expected_values: set[str],
    baseline_rate: float | None = None,
    warn_threshold: float = 0.02,
) -> dict:
    total = len(outputs)
    violations = sum(1 for output in outputs if output.get(expected_enum_field) not in expected_values)
    rate = round(violations / max(total, 1), 4)
    if baseline_rate is None:
        trend = "baseline_set"
        write_output_rate_baseline(rate)
    elif rate > baseline_rate * 1.5:
        trend = "rising"
    elif rate < baseline_rate * 0.5:
        trend = "falling"
    else:
        trend = "stable"

    status = "WARN" if (trend == "rising" or rate > warn_threshold) else "PASS"
    if rate > warn_threshold * 5:
        status = "FAIL"
    return {
        "name": "output_violation_rate",
        "status": status,
        "total_outputs": total,
        "schema_violations": violations,
        "violation_rate": rate,
        "trend": trend,
        "baseline_rate": baseline_rate,
        "warn_threshold": warn_threshold,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI-specific contract extensions.")
    parser.add_argument("--mode", default="all")
    parser.add_argument("--extractions", required=True)
    parser.add_argument("--verdicts", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    extraction_records = read_jsonl(args.extractions)
    extracted_texts = [fact.get("text", "") for record in extraction_records for fact in record.get("extracted_facts", []) if fact.get("text")]
    prompt_records = build_prompt_input_records(args.extractions)
    verdict_records = read_jsonl(args.verdicts)

    embedding_result = check_embedding_drift(extracted_texts)
    prompt_result = validate_prompt_inputs(prompt_records, WEEK3_PROMPT_SCHEMA)
    output_rate_result = check_output_violation_rate(
        verdict_records,
        expected_enum_field="overall_verdict",
        expected_values={"PASS", "FAIL", "WARN"},
        baseline_rate=load_output_rate_baseline(),
    )

    results = [embedding_result, prompt_result, output_rate_result]
    overall = "PASS"
    if any(result["status"] == "FAIL" for result in results):
        overall = "FAIL"
    elif any(result["status"] == "WARN" for result in results):
        overall = "WARN"

    report = {
        "generated_at": utc_now(),
        "mode": args.mode,
        "status": overall,
        "embedding_drift": embedding_result,
        "prompt_input_schema": prompt_result,
        "output_violation_rate": output_rate_result,
        "llm_output_schema": output_rate_result,
        "results": results,
    }
    write_json(args.output, report)
    print(f"Wrote AI extension report to {args.output}")


if __name__ == "__main__":
    main()
