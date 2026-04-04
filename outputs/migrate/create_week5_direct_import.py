import json
import sys
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import read_jsonl, write_jsonl


WEEK5_REPO = ROOT.parent / "week5_6"
UUID_NAMESPACE = uuid.UUID("abcdefab-cdef-abcd-efab-cdefabcdefab")


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, "::".join(parts)))


def main() -> None:
    rows = read_jsonl(WEEK5_REPO / "data/seed_events.jsonl")
    counters: defaultdict[str, int] = defaultdict(int)
    previous_event_id: dict[str, str | None] = {}
    output_rows = []
    for row in rows:
        stream_id = str(row["stream_id"])
        counters[stream_id] += 1
        sequence_number = counters[stream_id]
        event_id = stable_uuid("week5-direct", stream_id, str(sequence_number), row["event_type"])
        aggregate_id = stable_uuid("week5-direct-aggregate", stream_id)
        raw_time = row["recorded_at"] + "Z"
        output_rows.append(
            {
                "event_id": event_id,
                "event_type": row["event_type"],
                "aggregate_id": aggregate_id,
                "aggregate_type": "LoanApplication",
                "sequence_number": sequence_number,
                "payload": row.get("payload", {}),
                "metadata": {
                    "causation_id": previous_event_id.get(stream_id),
                    "correlation_id": stable_uuid("week5-direct-correlation", stream_id),
                    "user_id": str(row.get("payload", {}).get("contact_email") or row.get("payload", {}).get("requested_by") or "unknown"),
                    "source_service": "week5-ledger-2",
                },
                "schema_version": str(row.get("event_version", 1)),
                "occurred_at": raw_time,
                "recorded_at": raw_time,
            }
        )
        previous_event_id[stream_id] = event_id
    write_jsonl(ROOT / "outputs/week5/events_direct_import.jsonl", output_rows)
    print(f"Wrote {len(output_rows)} direct-import Week 5 events.")


if __name__ == "__main__":
    main()
