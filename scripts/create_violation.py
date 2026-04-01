import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import read_jsonl, write_jsonl


def main() -> None:
    records = read_jsonl(ROOT / "outputs/week3/extractions.jsonl")
    for index, record in enumerate(records[:5]):
        for fact in record["extracted_facts"]:
            fact["confidence"] = round(fact["confidence"] * 100, 2)
        if index == 0:
            record["entities"][0]["type"] = "TEAM"
        if index == 1:
            record["extracted_facts"][0]["entity_refs"].append("missing-entity-id")
    write_jsonl(
        ROOT / "outputs/week3/extractions_violated.jsonl",
        records,
        header_comment="Injected violations: first 5 records use 0-100 confidence; record 0 has invalid entity type; record 1 has dangling entity ref.",
    )


if __name__ == "__main__":
    main()
