# Data Contract Enforcer

This repository is the Week 7 contract-enforcement layer for the upstream Week 1-6 systems. The committed `outputs/`, `generated_contracts/`, `validation_reports/`, `schema_snapshots/`, `violation_log/`, and `enforcer_report/` directories already contain real run artifacts, so evaluators can follow the steps below on a fresh clone without re-seeding data first.

## Upstream Repositories

- Week 1: [TenX_Day2_Research](https://github.com/Gersum/TenX_Day2_Research.git)
- Week 2: [TenX_W2_Intereme](https://github.com/Gersum/TenX_W2_Intereme.git)
- Week 3: [DocRefinery](https://github.com/Gersum/DocRefinery.git)
- Week 4: [The_Brownfield_Cartographer](https://github.com/Gersum/The_Brownfield_Cartographer.git)
- Week 5 and Week 6: [The_Ledger_2](https://github.com/Gersum/The_Ledger_2.git)

## Prerequisites

```bash
python3 --version
pip install -r requirements.txt
```

Expected result:
- Python 3.11+ is available
- the repository already contains `outputs/week3/extractions.jsonl`, `outputs/week4/lineage_snapshots.jsonl`, `outputs/week5/events.jsonl`, `outputs/week2/verdicts.jsonl`, and `outputs/traces/runs.jsonl`

## Step 1: Bootstrap The Registry

```bash
sed -n '1,220p' contract_registry/subscriptions.yaml
```

Expected result:
- `contract_registry/subscriptions.yaml` exists
- it contains at least 4 subscription entries
- each entry includes `contract_id`, `subscriber_id`, `breaking_fields`, `validation_mode`, and `contact`

## Step 2: Generate Contracts

```bash
python3 contracts/generator.py \
  --source outputs/week3/extractions.jsonl \
  --contract-id week3-document-refinery-extractions \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --output generated_contracts

python3 contracts/generator.py \
  --source outputs/week5/events.jsonl \
  --contract-id week5-event-records \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --output generated_contracts
```

Expected result:
- `generated_contracts/week3_extractions.yaml`
- `generated_contracts/week3_extractions_dbt.yml`
- `generated_contracts/week5_events.yaml`
- `generated_contracts/week5_events_dbt.yml`
- new timestamped snapshots under `schema_snapshots/week3_document_refinery_extractions/` and `schema_snapshots/week5_event_records/`

Verification:
- `generated_contracts/week3_extractions.yaml` has at least 8 clauses
- `generated_contracts/week3_extractions.yaml` includes a confidence clause with `minimum: 0.0` and `maximum: 1.0`
- `generated_contracts/week5_events.yaml` includes UUID, datetime, enum, and payload-key clauses

## Step 3: Validate Clean Data

```bash
python3 contracts/runner.py \
  --contract generated_contracts/week3_extractions.yaml \
  --data outputs/week3/extractions.jsonl \
  --mode AUDIT \
  --output validation_reports/clean.json

python3 contracts/runner.py \
  --contract generated_contracts/week5_events.yaml \
  --data outputs/week5/events.jsonl \
  --mode AUDIT \
  --output validation_reports/week5_events_report.json
```

Expected result:
- `validation_reports/clean.json` has `status: PASS`
- `validation_reports/week5_events_report.json` has `status: PASS`
- both reports include `mode`, `pipeline_action`, and structured `results`
- `schema_snapshots/baselines.json` is written or refreshed

## Step 4: Inject A Known Violation

```bash
python3 scripts/create_violation.py
head -n 2 outputs/week3/extractions_violated.jsonl
```

Expected result:
- `outputs/week3/extractions_violated.jsonl` is regenerated from the real Week 3 dataset
- the top comment documents the injection
- the first few records show confidence scaled to `0-100`

## Step 5: Validate Violated Data

```bash
python3 contracts/runner.py \
  --contract generated_contracts/week3_extractions.yaml \
  --data outputs/week3/extractions_violated.jsonl \
  --mode ENFORCE \
  --output validation_reports/violated_run.json

python3 contracts/runner.py \
  --contract generated_contracts/week5_events.yaml \
  --data outputs/week5/events_direct_import.jsonl \
  --mode ENFORCE \
  --output validation_reports/week5_direct_import_violation.json
```

Expected result:
- `validation_reports/violated_run.json` has `status: FAIL`
- `validation_reports/violated_run.json` has `pipeline_action: BLOCK`
- `validation_reports/week5_direct_import_violation.json` has `status: FAIL`
- the Week 3 violated report includes a failing confidence range check and a failing statistical drift check
- the Week 5 direct-import report includes failing datetime, schema version, and payload-key checks

## Step 6: Attribute Violations

```bash
python3 contracts/attributor.py \
  --violation validation_reports/violated_run.json \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --contract generated_contracts/week3_extractions.yaml \
  --output violation_log/injected_week3_violations.jsonl \
  --header-comment "Injected violation included via outputs/week3/extractions_violated.jsonl generated by scripts/create_violation.py."

python3 contracts/attributor.py \
  --violation validation_reports/week5_direct_import_violation.json \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --contract generated_contracts/week5_events.yaml \
  --output violation_log/week5_real_violations.jsonl \
  --header-comment "Real violation from raw Week 5 direct-import events migrated from The_Ledger_2."
```

Expected result:
- both outputs contain ranked candidates, a commit hash, and a blast radius
- blast radius is registry-first and includes `direct_subscribers`
- lineage appears only as enrichment via `direct_nodes`, `transitive_nodes`, and `contamination_depth`

Single-entry blame-chain verification:

```bash
sed -n '2p' violation_log/week5_real_violations.jsonl > violation_log/week5_single_violation.json
python3 contracts/attributor.py \
  --violation violation_log/week5_single_violation.json \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --contract generated_contracts/week5_events.yaml \
  --output violation_log/week5_blame_chain.json
```

Expected result:
- `violation_log/week5_blame_chain.json` contains one blame-chain JSON object
- it includes at least one ranked candidate, `blame_chain.commit_hash`, and `blast_radius`

## Step 7: Analyze Schema Evolution

```bash
python3 contracts/generator.py \
  --source outputs/week3/extractions_violated.jsonl \
  --contract-id week3-document-refinery-extractions \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --output generated_contracts

python3 contracts/schema_analyzer.py \
  --contract-id week3-document-refinery-extractions \
  --output validation_reports/schema_evolution.json

python3 contracts/generator.py \
  --source outputs/week3/extractions.jsonl \
  --contract-id week3-document-refinery-extractions \
  --lineage outputs/week4/lineage_snapshots.jsonl \
  --registry contract_registry/subscriptions.yaml \
  --output generated_contracts
```

Expected result:
- `validation_reports/schema_evolution.json` has `compatibility_verdict: BREAKING`
- the change list includes a `SEMANTIC_RANGE_SHIFT` for `extracted_facts.confidence`
- the final clean generator run restores `generated_contracts/week3_extractions.yaml` to the clean source data while keeping the snapshot history

## Step 8: Run AI Contract Extensions

```bash
python3 contracts/ai_extensions.py \
  --mode all \
  --extractions outputs/week3/extractions.jsonl \
  --verdicts outputs/week2/verdicts.jsonl \
  --output validation_reports/ai_extensions.json
```

Expected result:
- `validation_reports/ai_extensions.json` exists
- it contains `embedding_drift`, `prompt_input_schema`, and `output_violation_rate`
- the checks run on real Week 3 extracted text and real Week 2 verdict records

## Step 9: Generate The Enforcer Report

```bash
python3 contracts/report_generator.py \
  --reports-dir validation_reports \
  --violations-dir violation_log \
  --registry contract_registry/subscriptions.yaml \
  --output enforcer_report/report_data.json
```

Expected result:
- `enforcer_report/report_data.json` exists
- `data_health_score` is between `0` and `100`
- `top_violations` mention downstream subscribers from the registry
- `recommended_actions` reference real file paths from this repository such as `src/extractor.py`, `src/events.py`, `outputs/migrate/create_week5_direct_import.py`, `contracts/runner.py`, or `contracts/schema_analyzer.py`

## Final Verification

Open `enforcer_report/report_data.json` and confirm:
- `data_health_score` is between `0` and `100`
- `schema_changes_detected` is non-empty
- `violation_count` reflects the mixed real plus injected violation log
- `ai_system_risk_assessment` contains real numeric outputs from the AI extension checks
