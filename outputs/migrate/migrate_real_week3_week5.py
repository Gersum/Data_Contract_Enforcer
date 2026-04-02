import hashlib
import json
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import parse_iso8601, read_jsonl, write_jsonl


WEEK3_REPO = ROOT.parent / "week3"
WEEK5_REPO = ROOT.parent / "week5_6"
UUID_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, "::".join(parts)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def iso_from_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_iso(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_entity_type(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ["report", "statement", "budget", "survey"]):
        return "ORG"
    if re.search(r"\b(20\d{2}|19\d{2})\b", name):
        return "DATE"
    return "OTHER"


def extract_pdf_snippet(path: Path) -> tuple[str, int | None]:
    try:
        with pdfplumber.open(path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    snippet = " ".join(text.split())
                    return snippet[:1200], page_num
    except Exception:
        pass
    return path.stem.replace("_", " "), None


def load_week3_ledger() -> dict[str, dict]:
    ledger_path = WEEK3_REPO / ".refinery/extraction_ledger.jsonl"
    ledger_rows = read_jsonl(ledger_path)
    index = {}
    for row in ledger_rows:
        index[normalize_name(str(row.get("document_id", "")))] = row
    return index


def build_week3_records() -> list[dict]:
    ledger_index = load_week3_ledger()
    corpus_files = sorted(path for path in (WEEK3_REPO / "corpus").iterdir() if path.is_file())
    records = []
    for path in corpus_files:
        stem = path.stem
        ledger = ledger_index.get(normalize_name(stem), {})
        snippet, page_ref = extract_pdf_snippet(path)
        strategy = ledger.get("strategy_used", "DOCREFINERY_CORPUS")
        confidence = float(ledger.get("confidence_score", 0.76))
        extracted_at = iso_from_timestamp(ledger["timestamp"]) if "timestamp" in ledger else datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        doc_id = stable_uuid("week3-doc", str(path))
        title_entity_id = stable_uuid("week3-entity", stem, "title")
        strategy_entity_id = stable_uuid("week3-entity", stem, "strategy")

        facts = [
            {
                "fact_id": stable_uuid("week3-fact", stem, "snippet"),
                "text": f"DocRefinery processed document '{stem}' from the real corpus.",
                "entity_refs": [title_entity_id, strategy_entity_id],
                "confidence": round(max(0.0, min(confidence, 1.0)), 4),
                "page_ref": page_ref,
                "source_excerpt": snippet[:500] if snippet else stem,
            }
        ]

        if ledger:
            review_text = ledger.get("review_reason") or f"Strategy used: {strategy}"
            facts.append(
                {
                    "fact_id": stable_uuid("week3-fact", stem, "review"),
                    "text": f"Extraction strategy for '{stem}' was {strategy}.",
                    "entity_refs": [strategy_entity_id],
                    "confidence": round(max(0.0, min(confidence, 1.0)), 4),
                    "page_ref": None,
                    "source_excerpt": review_text[:500],
                }
            )

        processing_time_ms = int(round(float(ledger.get("processing_time", 1.0)) * 1000))
        token_count_input = max(1, len(snippet.split()))
        token_count_output = max(1, sum(len(fact["text"].split()) for fact in facts))

        records.append(
            {
                "doc_id": doc_id,
                "source_path": str(path.resolve()),
                "source_hash": sha256_file(path),
                "extracted_facts": facts,
                "entities": [
                    {
                        "entity_id": title_entity_id,
                        "name": stem.replace("_", " "),
                        "type": classify_entity_type(stem),
                        "canonical_value": stem,
                    },
                    {
                        "entity_id": strategy_entity_id,
                        "name": strategy,
                        "type": "OTHER",
                        "canonical_value": strategy,
                    },
                ],
                "extraction_model": strategy,
                "processing_time_ms": max(1, processing_time_ms),
                "token_count": {"input": token_count_input, "output": token_count_output},
                "extracted_at": extracted_at,
            }
        )
    return records


def build_week5_records() -> list[dict]:
    source_path = WEEK5_REPO / "data/seed_events.jsonl"
    source_rows = read_jsonl(source_path)
    counters: defaultdict[str, int] = defaultdict(int)
    previous_event_id: dict[str, str | None] = {}
    records = []
    for row in source_rows:
        stream_id = str(row["stream_id"])
        counters[stream_id] += 1
        sequence_number = counters[stream_id]
        aggregate_id = stable_uuid("week5-aggregate", stream_id)
        event_id = stable_uuid("week5-event", stream_id, str(sequence_number), row["event_type"])
        occurred_at_raw = row.get("recorded_at")
        occurred_at = canonical_iso(occurred_at_raw)
        recorded_at = occurred_at
        source_payload = row.get("payload", {})
        document_id = (
            source_payload.get("application_id")
            or source_payload.get("application_reference")
            or source_payload.get("document_id")
            or stream_id
        )
        payload = dict(source_payload)
        payload["document_id"] = document_id
        payload["status"] = row["event_type"]
        payload["fact_count"] = len(source_payload)
        user_id = (
            source_payload.get("contact_email")
            or source_payload.get("requested_by")
            or source_payload.get("applicant_id")
            or "unknown"
        )
        records.append(
            {
                "event_id": event_id,
                "event_type": row["event_type"],
                "aggregate_id": aggregate_id,
                "aggregate_type": "LoanApplication",
                "sequence_number": sequence_number,
                "payload": payload,
                "metadata": {
                    "causation_id": previous_event_id.get(stream_id),
                    "correlation_id": stable_uuid("week5-correlation", stream_id),
                    "user_id": str(user_id),
                    "source_service": "week5-ledger-2",
                },
                "schema_version": f"{row.get('event_version', 1)}.0",
                "occurred_at": occurred_at,
                "recorded_at": recorded_at,
            }
        )
        previous_event_id[stream_id] = event_id
    return records


def main() -> None:
    week3_records = build_week3_records()
    week5_records = build_week5_records()
    write_jsonl(ROOT / "outputs/week3/extractions.jsonl", week3_records)
    write_jsonl(ROOT / "outputs/week5/events.jsonl", week5_records)
    print(f"Migrated {len(week3_records)} real Week 3 records and {len(week5_records)} real Week 5 records.")


if __name__ == "__main__":
    main()
