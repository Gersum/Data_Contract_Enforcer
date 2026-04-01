# Data Contract Enforcer

This repository implements the Week 7 Data Contract Enforcer assignment with runnable CLIs for contract generation, validation, attribution, schema evolution analysis, AI contract checks, and report generation.

## Upstream Repositories

This Week 7 repository builds on the user's actual prior-week repositories, recorded in [upstream_repos.json](/Users/gersumasfaw/Downloads/week7/upstream_repos.json):

- Week 1: [TenX_Day2_Research](https://github.com/Gersum/TenX_Day2_Research.git)
- Week 2: [TenX_W2_Intereme](https://github.com/Gersum/TenX_W2_Intereme.git)
- Week 3: [DocRefinery](https://github.com/Gersum/DocRefinery.git)
- Week 4: [The_Brownfield_Cartographer](https://github.com/Gersum/The_Brownfield_Cartographer.git)
- Week 5 and Week 6: [The_Ledger_2](https://github.com/Gersum/The_Ledger_2.git)

The JSONL files in `outputs/` are the Week 7 contract-enforcement interfaces derived from those upstream systems.

## Prerequisites

```bash
python3 --version
pip install -r requirements.txt
```

## How To Run

1. Seed the example platform outputs.

```bash
python3 scripts/seed_data.py
```

Expected output: JSONL files under `outputs/week1`, `outputs/week2`, `outputs/week3`, `outputs/week4`, `outputs/week5`, and `outputs/traces`.

2. Generate the Week 3 extraction contract.

```bash
python3 contracts/generator.py \
  --source outputs/week3/extractions.jsonl \
  --contract-id week3-document-refinery-extractions \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --output generated_contracts
```

Expected output: `generated_contracts/week3_document_refinery_extractions.yaml` and `generated_contracts/week3_document_refinery_extractions_dbt.yml`

3. Generate the Week 5 event contract.

```bash
python3 contracts/generator.py \
  --source outputs/week5/events.jsonl \
  --contract-id week5-event-sourcing-events \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --output generated_contracts
```

Expected output: `generated_contracts/week5_event_sourcing_events.yaml` and `generated_contracts/week5_event_sourcing_events_dbt.yml`

4. Validate clean Week 3 data.

```bash
python3 contracts/runner.py \
  --contract generated_contracts/week3_document_refinery_extractions.yaml \
  --data outputs/week3/extractions.jsonl \
  --output validation_reports/thursday_week3.json
```

Expected output: `validation_reports/thursday_week3.json` with overall status `PASS`

5. Validate clean Week 5 data.

```bash
python3 contracts/runner.py \
  --contract generated_contracts/week5_event_sourcing_events.yaml \
  --data outputs/week5/events.jsonl \
  --output validation_reports/thursday_week5.json
```

Expected output: `validation_reports/thursday_week5.json` with overall status `PASS`

6. Inject a known Week 3 violation.

```bash
python3 scripts/create_violation.py
```

Expected output: `outputs/week3/extractions_violated.jsonl` with documented injected violations at the top of the file

7. Re-run generation on the violated data to create a second schema snapshot.

```bash
python3 contracts/generator.py \
  --source outputs/week3/extractions_violated.jsonl \
  --contract-id week3-document-refinery-extractions \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --output generated_contracts
```

Expected output: a new file in `schema_snapshots/` for the same contract

8. Validate the violated dataset.

```bash
python3 contracts/runner.py \
  --contract generated_contracts/week3_document_refinery_extractions.yaml \
  --data outputs/week3/extractions_violated.jsonl \
  --output validation_reports/violated_run.json
```

Expected output: `validation_reports/violated_run.json` with overall status `FAIL` and failing checks for confidence range, entity type enum, and entity reference validity

9. Attribute the violated checks.

```bash
python3 contracts/attributor.py \
  --violation validation_reports/violated_run.json \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --contract generated_contracts/week3_document_refinery_extractions.yaml \
  --output violation_log/violations.jsonl
```

Expected output: `violation_log/violations.jsonl` with at least 3 attributed violations, each including ranked candidates, git blame details, and blast radius

10. Optional: run attribution in cross-boundary registry mode.

```bash
python3 contracts/attributor.py \
  --violation validation_reports/violated_run.json \
  --contract generated_contracts/week3_document_refinery_extractions.yaml \
  --registry contract_registry/subscriptions.json \
  --output violation_log/violations_registry_mode.jsonl
```

Expected output: `violation_log/violations_registry_mode.jsonl` with subscriber-based blast radius metadata instead of full lineage traversal

11. Analyze schema evolution.

```bash
python3 contracts/schema_analyzer.py \
  --contract-id week3-document-refinery-extractions \
  --output validation_reports/schema_evolution.json
```

Expected output: `validation_reports/schema_evolution.json` with compatibility verdict `BREAKING`

12. Run AI contract extensions on real Week 3 and Week 2 data.

```bash
python3 contracts/ai_extensions.py \
  --mode all \
  --extractions outputs/week3/extractions.jsonl \
  --verdicts outputs/week2/verdicts.jsonl \
  --output validation_reports/ai_extensions.json
```

Expected output: `validation_reports/ai_extensions.json` with embedding drift, prompt input schema, and LLM output schema results

13. Generate the machine-readable enforcer report.

```bash
python3 contracts/report_generator.py \
  --reports-dir validation_reports \
  --violations-dir violation_log \
  --output enforcer_report/report_data.json
```

Expected output: `enforcer_report/report_data.json`

## Expected Final State

- `generated_contracts/` contains Week 3 and Week 5 contracts plus dbt counterparts
- `validation_reports/` contains clean, violated, schema evolution, and AI extension reports
- `violation_log/violations.jsonl` contains 3 attributed violations
- `schema_snapshots/` contains multiple snapshots for Week 3
- `enforcer_report/report_data.json` contains a `data_health_score` between 0 and 100

After running all steps, open `enforcer_report/report_data.json` and verify that `data_health_score` is between `0` and `100`.
