# DOMAIN_NOTES

## Phase 0 Question 1: What are the real interfaces in this platform, and which ones matter most?

The platform in this repository models the six data-producing surfaces described in the Week 7 brief: Week 1 intent records, Week 2 verdict records, Week 3 extraction records, Week 4 lineage snapshots, Week 5 event records, and LangSmith trace exports. These are tied back to my actual upstream repositories in `upstream_repos.json`: Week 1 `TenX_Day2_Research`, Week 2 `TenX_W2_Intereme`, Week 3 `DocRefinery`, Week 4 `The_Brownfield_Cartographer`, and Week 5/6 `The_Ledger_2`. In this implementation the highest-risk interface is the Week 3 `outputs/week3/extractions.jsonl` file because it feeds both the Week 4 lineage context and the Week 5 event platform. The specific fields that create the most downstream coupling are `doc_id`, `extracted_facts[].confidence`, `extracted_facts[].entity_refs`, `entities[].type`, and `extracted_at`.

The second most important interface is Week 5 `outputs/week5/events.jsonl`. Event consumers depend on `event_type`, `aggregate_id`, `sequence_number`, `payload`, `occurred_at`, and `recorded_at` staying stable. The `sequence_number` contract is especially important because silent gaps or duplicates would make event replay untrustworthy. I wrote the week5 contract to enforce positivity, monotonic ordering, timestamp ordering, payload required keys, PascalCase naming, and registry membership for `event_type`.

The Week 2 interface matters to the AI extension path. The repository stores verdicts in `outputs/week2/verdicts.jsonl`, and the AI extension reads `overall_verdict`, `overall_score`, and `confidence` to measure LLM output schema conformance. That means a verdict formatting drift would not only break reporting; it would also corrupt the quality signals used to judge AI reliability.

## Phase 0 Question 2: What happens if the Week 3 confidence scale changes from 0.0-1.0 to 0-100?

I measured the current clean distribution directly from this repository's Week 3 data with the required script pattern. The actual result was:

`min=0.550 max=0.940 mean=0.713`

That measurement matters because it confirms the clean dataset is genuinely operating on the intended `0.0-1.0` scale. The contract generator then turns that fact into an explicit clause:

- `week3.extracted_facts.confidence.range`
- field: `extracted_facts.confidence`
- minimum: `0.0`
- maximum: `1.0`
- severity: `CRITICAL`

I then injected a scale-change violation into `outputs/week3/extractions_violated.jsonl` with `scripts/create_violation.py`. For the first five extraction records, each fact confidence was multiplied by 100. When the validation runner executed against that violated dataset, it produced a real failure:

- check id: `week3.extracted_facts.confidence.range`
- expected range: `0.0..1.0`
- actual observed range: `0.55..60.0`
- failing records: `10`

This is a textbook silent-failure scenario. The data would still parse as numeric, the pipeline would still run, and a consumer that thresholds on `confidence >= 0.8` would suddenly treat nearly every affected fact as maximally reliable. In this repository that would distort the lineage metadata that depends on Week 3 quality signals and would contaminate any event payloads that assume confidence is normalized. The schema evolution analyzer also classified the change as `BREAKING` with the impact text: "Confidence max moved from 0.94 to 60.0, indicating a 0.0-1.0 to 0-100 scale break."

## Phase 0 Question 3: Which invariants are structural, which are semantic, and which are statistical?

Structural invariants in this project are the ones that can be checked without understanding business meaning. Examples:

- `doc_id`, `event_id`, and `aggregate_id` must be UUIDs.
- `extracted_at`, `occurred_at`, and `recorded_at` must parse as ISO 8601 UTC timestamps.
- `processing_time_ms`, `token_count.input`, and `token_count.output` must be positive integers.
- `event_type` and `aggregate_type` must match PascalCase naming.

Semantic invariants encode meaning inside the platform rather than just shape. Examples:

- `extracted_facts.entity_refs` must point to entity IDs that exist in the same extraction record.
- `entities.type` must be one of `PERSON`, `ORG`, `LOCATION`, `DATE`, `AMOUNT`, or `OTHER`.
- `recorded_at >= occurred_at` because event recording cannot precede event occurrence.
- `event_type` must appear in the schema registry at `schemas/event_registry.json`.

Statistical invariants catch meaningful drift even when shape has not technically changed. The runner writes numeric baselines into `schema_snapshots/baselines.json` and compares future means using a z-score threshold. This is how the system distinguishes "still numeric" from "numerically unsafe." The confidence scale injection shows why this distinction matters. Even if a producer kept the field numeric, the meaning would still have drifted out of contract.

## Phase 0 Question 4: How should LangSmith traces be contracted?

The LangSmith export in this repo lives at `outputs/traces/runs.jsonl`. The useful contract is not just "the file exists." It should encode time ordering, token arithmetic, run type conformance, and non-negative cost. A valid Bitol-style snippet for that interface is:

```yaml
contract_id: langsmith-trace-export
dataset: outputs/traces/runs.jsonl
clauses:
  - field: run_type
    type: string
    enum: [llm, chain, tool, retriever, embedding]
    required: true
  - field: end_time
    type: string
    format: date-time
    rule: must_be_after:start_time
  - field: total_tokens
    type: integer
    rule: equals:prompt_tokens+completion_tokens
  - field: total_cost
    type: number
    minimum: 0.0
    required: true
```

This matters because the AI Contract Extension depends on trustworthy telemetry. If `total_tokens` is wrong or `run_type` drifts to an unregistered value, any cost or performance analysis built on top of that export becomes unreliable.

## Phase 0 Question 5: What did the first real validation and attribution runs reveal?

The clean Thursday baseline was healthy. Both core contracts generated successfully, each with 12 clauses, and the baseline Week 3 validation run returned `PASS`. The Sunday violated run revealed three concrete failures:

1. `extracted_facts.confidence` broke its range clause.
2. `entities.type` contained the invalid enum value `TEAM`.
3. `extracted_facts.entity_refs_valid` failed because one fact referenced `missing-entity-id`.

The violation attributor converted those failures into `violation_log/violations.jsonl` and attached a ranked blame chain, a git commit hash, and a blast radius. The machine-generated report in `enforcer_report/report_data.json` computed a `data_health_score` of `50`, counted `3` attributed violations, and flagged `2` of them as `CRITICAL`. That score feels directionally correct for this state of the platform: the system is runnable, but a high-risk upstream contract break has already been demonstrated.

The AI extension results were reassuring on the clean data: embedding drift `0.0129`, prompt-input schema `PASS`, and LLM output schema violation rate `0.0`. That contrast is useful. It shows the platform can separate a real upstream data contract failure from AI telemetry that remains healthy. In other words, the enforcer is not just screaming "everything is broken"; it is isolating the failing interface and preserving signal quality elsewhere.

The main lesson is that my own assumptions were too optimistic before writing the contracts. I assumed "numeric confidence" was specific enough, but it was not. I assumed entity references were safe because they were generated in the same record, but that only holds until a producer bug creates a dangling ID. I assumed event payloads were safe because their keys looked stable, but replay correctness also depends on sequence integrity and temporal ordering. Writing the contracts turned those vague assumptions into executable checks, and the injected run proved the system can catch the exact class of silent breakage the assignment is about.

## Trust Boundary Note: Where lineage attribution stops and registry-based notification begins

The original project framing assumes the ViolationAttributor can traverse the Week 4 lineage graph across all five systems. That is valid for Tier 1, where one team owns the full stack and blast radius can be computed directly from lineage. It stops being realistic once the dependency crosses a team or company boundary.

For that reason, this repository now supports two attribution modes:

- `lineage_graph`: used when the cartographer output is available and all affected systems are inside the same trust boundary
- `registry_subscriptions`: used when the producer cannot see inside downstream systems and must rely on registered subscribers

This maps to the three tiers in the additional guidance:

- Tier 1: same team, same repo, full lineage traversal is possible
- Tier 2: different teams in the same company, shared registry plus partial lineage is more realistic
- Tier 3: different companies, no shared lineage graph; the producer only knows which subscribers are on which contract version

In the Tier 2 and Tier 3 model, cross-boundary blast radius is intentionally smaller in scope. The producer can report subscriber count, active contract versions, and notification targets, but each consumer must compute its own internal blast radius. That matches how Confluent Schema Registry, dbt Mesh, and Pact invert the problem in production systems: they make dependencies explicit at the governance boundary and block or notify on breaking changes before a producer ships them.
