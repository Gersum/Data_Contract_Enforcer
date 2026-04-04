import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.common import write_jsonl


WEEK2_REPO = Path("/tmp/TenX_W2_Intereme")
UUID_NAMESPACE = uuid.UUID("87654321-4321-8765-4321-876543218765")


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(UUID_NAMESPACE, "::".join(parts)))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_iso(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_scores(audit_report: dict, evidences: dict) -> tuple[dict, float]:
    scores = {}
    confidences = []
    for criterion in audit_report.get("criterion_breakdown", []):
        criterion_id = criterion["criterion_id"]
        cited = []
        for opinion in criterion.get("judge_opinions", []):
            cited.extend(opinion.get("cited_evidence", []))
        cited = list(dict.fromkeys(cited))
        evidence_snippets = []
        for evidence_id in cited[:3]:
            evidence = evidences.get(evidence_id, {})
            if evidence.get("content"):
                evidence_snippets.append(str(evidence["content"])[:200])
            if isinstance(evidence.get("confidence"), (int, float)):
                confidences.append(float(evidence["confidence"]))
        scores[criterion_id] = {
            "score": int(criterion.get("final_score", 3)),
            "evidence": evidence_snippets or [criterion.get("criterion_name", criterion_id)],
            "notes": criterion.get("final_rationale", "")[:500],
        }
    overall_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.8
    return scores, overall_confidence


def build_record(path: Path, rubric_hash: str, rubric_version: str) -> dict:
    payload = json.load(open(path, "r", encoding="utf-8"))
    audit_report = payload["audit_report"]
    scores, confidence = build_scores(audit_report, payload.get("evidences", {}))
    overall_score = round(float(audit_report.get("aggregate_score", 0.0)), 2)
    if overall_score >= 4.0:
        verdict = "PASS"
    elif overall_score >= 3.0:
        verdict = "WARN"
    else:
        verdict = "FAIL"
    target_ref = payload.get("repo_url") or payload.get("pdf_path") or path.name
    return {
        "verdict_id": stable_uuid("week2-verdict", str(path)),
        "target_ref": target_ref,
        "rubric_id": rubric_hash,
        "rubric_version": rubric_version,
        "scores": scores,
        "overall_verdict": verdict,
        "overall_score": overall_score,
        "confidence": max(0.0, min(confidence, 1.0)),
        "evaluated_at": canonical_iso(audit_report["generated_at"]),
    }


def main() -> None:
    rubric_path = WEEK2_REPO / "rubric/week2_rubric.json"
    rubric_text = rubric_path.read_text(encoding="utf-8")
    rubric = json.loads(rubric_text)
    rubric_hash = sha256_text(rubric_text)
    rubric_version = rubric["rubric_metadata"]["version"]

    report_paths = sorted((WEEK2_REPO / "audit/report_onself_generated").glob("*.json"))
    records = [build_record(path, rubric_hash, rubric_version) for path in report_paths]
    write_jsonl(ROOT / "outputs/week2/verdicts.jsonl", records)
    print(f"Migrated {len(records)} real Week 2 verdict records.")


if __name__ == "__main__":
    main()
