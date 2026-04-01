import argparse
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import PASCAL_CASE_RE, read_jsonl, write_json


def tokenize(text: str) -> Counter:
    tokens = [token.lower() for token in text.replace(".", " ").replace(",", " ").split() if token]
    return Counter(tokens)


def cosine(counter_a: Counter, counter_b: Counter) -> float:
    keys = set(counter_a) | set(counter_b)
    numerator = sum(counter_a[k] * counter_b[k] for k in keys)
    denom_a = math.sqrt(sum(value * value for value in counter_a.values()))
    denom_b = math.sqrt(sum(value * value for value in counter_b.values()))
    if denom_a == 0 or denom_b == 0:
        return 0.0
    return numerator / (denom_a * denom_b)


def embedding_drift(extractions_path: str) -> dict:
    records = read_jsonl(extractions_path)
    texts = [fact["text"] for record in records for fact in record.get("extracted_facts", [])]
    midpoint = max(1, len(texts) // 2)
    baseline = tokenize(" ".join(texts[:midpoint]))
    current = tokenize(" ".join(texts[midpoint:]))
    similarity = cosine(baseline, current)
    drift = round(1 - similarity, 4)
    status = "PASS"
    if drift > 0.35:
        status = "FAIL"
    elif drift > 0.2:
        status = "WARN"
    return {
        "name": "embedding_drift",
        "status": status,
        "drift_score": drift,
        "baseline_size": midpoint,
        "current_size": len(texts) - midpoint,
    }


def prompt_input_schema_check(extractions_path: str) -> dict:
    records = read_jsonl(extractions_path)
    bad = []
    for record in records:
        if not isinstance(record.get("source_path"), str) or not record.get("source_path"):
            bad.append(record.get("doc_id"))
        if not isinstance(record.get("token_count", {}).get("input"), int):
            bad.append(record.get("doc_id"))
    return {
        "name": "prompt_input_schema",
        "status": "PASS" if not bad else "FAIL",
        "records_failing": len(bad),
        "sample_values": bad[:5],
    }


def llm_output_schema_check(verdicts_path: str) -> dict:
    records = read_jsonl(verdicts_path)
    violations = []
    for record in records:
        if record.get("overall_verdict") not in {"PASS", "FAIL", "WARN"}:
            violations.append(record.get("verdict_id"))
        if not isinstance(record.get("overall_score"), (int, float)):
            violations.append(record.get("verdict_id"))
        if not isinstance(record.get("confidence"), (int, float)) or not 0.0 <= record["confidence"] <= 1.0:
            violations.append(record.get("verdict_id"))
    rate = round(len(violations) / max(1, len(records)), 4)
    status = "PASS"
    if rate > 0.1:
        status = "FAIL"
    elif rate > 0.03:
        status = "WARN"
    return {
        "name": "llm_output_schema",
        "status": status,
        "violation_rate": rate,
        "records_failing": len(violations),
        "sample_values": violations[:5],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run AI-specific contract extensions.")
    parser.add_argument("--mode", default="all")
    parser.add_argument("--extractions", required=True)
    parser.add_argument("--verdicts", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    results = [
        embedding_drift(args.extractions),
        prompt_input_schema_check(args.extractions),
        llm_output_schema_check(args.verdicts),
    ]
    overall = "PASS"
    if any(result["status"] == "FAIL" for result in results):
        overall = "FAIL"
    elif any(result["status"] == "WARN" for result in results):
        overall = "WARN"

    report = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "mode": args.mode,
        "status": overall,
        "results": results,
    }
    write_json(args.output, report)
    print(f"Wrote AI extension report to {args.output}")


if __name__ == "__main__":
    main()
