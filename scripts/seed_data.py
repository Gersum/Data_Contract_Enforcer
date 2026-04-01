import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import sha256_text, write_jsonl


random.seed(7)


def iso_at(offset_minutes: int) -> str:
    start = datetime(2025, 1, 15, 8, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_minutes)
    return start.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_week1() -> list[dict]:
    records = []
    repo_files = ["src/extractor.py", "src/events.py", "src/contracts.py", "src/lineage.py"]
    for index in range(12):
        records.append(
            {
                "intent_id": str(uuid.uuid4()),
                "description": f"Track contract-sensitive transformation #{index + 1}",
                "code_refs": [
                    {
                        "file": repo_files[index % len(repo_files)],
                        "line_start": 10 + index,
                        "line_end": 14 + index,
                        "symbol": f"handler_{index + 1}",
                        "confidence": round(0.72 + (index % 5) * 0.05, 2),
                    }
                ],
                "governance_tags": ["pii", "quality"] if index % 2 == 0 else ["billing"],
                "created_at": iso_at(index * 5),
            }
        )
    return records


def build_week2() -> list[dict]:
    rubric_text = (ROOT / "rubrics/week2_rubric.yaml").read_text(encoding="utf-8")
    rubric_hash = sha256_text(rubric_text)
    records = []
    for index in range(24):
        score_a = 3 + (index % 3)
        score_b = 2 + (index % 4)
        overall = round((score_a + score_b) / 2, 2)
        records.append(
            {
                "verdict_id": str(uuid.uuid4()),
                "target_ref": "src/extractor.py" if index % 2 == 0 else f"doc-{index:03d}",
                "rubric_id": rubric_hash,
                "rubric_version": "1.2.0",
                "scores": {
                    "accuracy": {
                        "score": score_a,
                        "evidence": [f"accuracy evidence {index}"],
                        "notes": "Consistent output",
                    },
                    "traceability": {
                        "score": score_b,
                        "evidence": [f"traceability evidence {index}"],
                        "notes": "Sufficient evidence",
                    },
                },
                "overall_verdict": "PASS" if overall >= 3.5 else "WARN",
                "overall_score": overall,
                "confidence": round(0.82 + (index % 5) * 0.03, 2),
                "evaluated_at": iso_at(index * 6),
            }
        )
    return records


def build_week3() -> list[dict]:
    records = []
    entity_types = ["PERSON", "ORG", "LOCATION", "DATE", "AMOUNT", "OTHER"]
    for index in range(60):
        doc_id = str(uuid.uuid4())
        entities = []
        for entity_index in range(3):
            entity_id = str(uuid.uuid4())
            entities.append(
                {
                    "entity_id": entity_id,
                    "name": f"Entity {index}-{entity_index}",
                    "type": entity_types[(index + entity_index) % len(entity_types)],
                    "canonical_value": f"entity-{index}-{entity_index}",
                }
            )
        facts = []
        for fact_index in range(2):
            excerpt = f"Document {index} excerpt {fact_index} describing an obligation."
            facts.append(
                {
                    "fact_id": str(uuid.uuid4()),
                    "text": f"Document {index} fact {fact_index}",
                    "entity_refs": [entities[fact_index]["entity_id"], entities[(fact_index + 1) % 3]["entity_id"]],
                    "confidence": round(0.55 + ((index + fact_index) % 40) / 100, 2),
                    "page_ref": fact_index + 1,
                    "source_excerpt": excerpt,
                }
            )
        record = {
            "doc_id": doc_id,
            "source_path": f"https://example.com/documents/{index}",
            "source_hash": sha256_text(f"source-{index}"),
            "extracted_facts": facts,
            "entities": entities,
            "extraction_model": "claude-3-5-sonnet-20241022",
            "processing_time_ms": 1100 + (index * 13),
            "token_count": {"input": 3800 + index * 11, "output": 760 + index * 3},
            "extracted_at": iso_at(index * 7),
        }
        records.append(record)
    return records


def build_week4(week3_records: list[dict]) -> list[dict]:
    doc_nodes = []
    for record in week3_records[:10]:
        doc_nodes.append(
            {
                "node_id": f"doc::{record['doc_id']}",
                "type": "MODEL",
                "label": record["doc_id"][:8],
                "metadata": {
                    "path": "outputs/week3/extractions.jsonl",
                    "language": "json",
                    "purpose": "Refined extraction document",
                    "last_modified": record["extracted_at"],
                },
            }
        )
    nodes = [
        {
            "node_id": "file::src/extractor.py",
            "type": "FILE",
            "label": "extractor.py",
            "metadata": {
                "path": "src/extractor.py",
                "language": "python",
                "purpose": "Extract facts from documents",
                "last_modified": iso_at(2),
            },
        },
        {
            "node_id": "service::week3-document-refinery",
            "type": "SERVICE",
            "label": "week3-document-refinery",
            "metadata": {
                "path": "services/week3",
                "language": "python",
                "purpose": "Runs extraction jobs",
                "last_modified": iso_at(3),
            },
        },
        {
            "node_id": "pipeline::week7-contract-enforcer",
            "type": "PIPELINE",
            "label": "week7-contract-enforcer",
            "metadata": {
                "path": "contracts/",
                "language": "python",
                "purpose": "Validates and attributes contract breaks",
                "last_modified": iso_at(4),
            },
        },
        {
            "node_id": "service::week5-event-platform",
            "type": "SERVICE",
            "label": "week5-event-platform",
            "metadata": {
                "path": "services/week5",
                "language": "python",
                "purpose": "Persists event records",
                "last_modified": iso_at(5),
            },
        },
    ] + doc_nodes
    edges = [
        {
            "source": "file::src/extractor.py",
            "target": "service::week3-document-refinery",
            "relationship": "CALLS",
            "confidence": 0.92,
        },
        {
            "source": "service::week3-document-refinery",
            "target": "pipeline::week7-contract-enforcer",
            "relationship": "PRODUCES",
            "confidence": 0.97,
        },
        {
            "source": "service::week3-document-refinery",
            "target": "service::week5-event-platform",
            "relationship": "PRODUCES",
            "confidence": 0.89,
        },
    ]
    for record in week3_records[:10]:
        edges.append(
            {
                "source": "service::week3-document-refinery",
                "target": f"doc::{record['doc_id']}",
                "relationship": "PRODUCES",
                "confidence": 0.94,
            }
        )
    return [
        {
            "snapshot_id": str(uuid.uuid4()),
            "codebase_root": str(ROOT),
            "git_commit": "a" * 40,
            "nodes": nodes,
            "edges": edges,
            "captured_at": iso_at(100),
        }
    ]


def build_week5() -> list[dict]:
    records = []
    event_types = ["DocumentProcessed", "ExtractionStored", "ContractValidated"]
    for aggregate_num in range(10):
        aggregate_id = str(uuid.uuid4())
        for seq in range(1, 7):
            event_type = event_types[(aggregate_num + seq) % len(event_types)]
            occurred = iso_at(aggregate_num * 20 + seq)
            recorded = iso_at(aggregate_num * 20 + seq + 1)
            records.append(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": event_type,
                    "aggregate_id": aggregate_id,
                    "aggregate_type": "Document",
                    "sequence_number": seq,
                    "payload": {
                        "document_id": str(uuid.uuid4()),
                        "status": "completed",
                        "fact_count": 2 + (seq % 3),
                    },
                    "metadata": {
                        "causation_id": str(uuid.uuid4()) if seq > 1 else None,
                        "correlation_id": str(uuid.uuid4()),
                        "user_id": f"user-{aggregate_num}",
                        "source_service": "week3-document-refinery",
                    },
                    "schema_version": "1.0",
                    "occurred_at": occurred,
                    "recorded_at": recorded,
                }
            )
    return records


def build_traces() -> list[dict]:
    records = []
    run_types = ["llm", "chain", "tool", "retriever", "embedding"]
    for index in range(60):
        prompt_tokens = 3200 + index * 5
        completion_tokens = 500 + index * 2
        records.append(
            {
                "id": str(uuid.uuid4()),
                "name": f"run-{index}",
                "run_type": run_types[index % len(run_types)],
                "inputs": {"doc": index},
                "outputs": {"status": "ok"},
                "error": None,
                "start_time": iso_at(index * 4),
                "end_time": iso_at(index * 4 + 1),
                "total_tokens": prompt_tokens + completion_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_cost": round(0.01 + index * 0.0001, 4),
                "tags": ["week3", "extraction"],
                "parent_run_id": None,
                "session_id": str(uuid.uuid4()),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed example data for the Data Contract Enforcer project.")
    parser.parse_args()

    week1 = build_week1()
    week2 = build_week2()
    week3 = build_week3()
    week4 = build_week4(week3)
    week5 = build_week5()
    traces = build_traces()

    write_jsonl(ROOT / "outputs/week1/intent_records.jsonl", week1)
    write_jsonl(ROOT / "outputs/week2/verdicts.jsonl", week2)
    write_jsonl(ROOT / "outputs/week3/extractions.jsonl", week3)
    write_jsonl(ROOT / "outputs/week4/lineage_snapshots.jsonl", week4)
    write_jsonl(ROOT / "outputs/week5/events.jsonl", week5)
    write_jsonl(ROOT / "outputs/traces/runs.jsonl", traces)


if __name__ == "__main__":
    main()
