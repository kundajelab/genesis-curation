# Guided review validation

## Software checks

The release adds regressions for confidence-independent evidence issues, combined
verification, increased output reservation after truncation, compact cross-study
queues, matching-evidence grouped correction, assay-scoped examples, isolated audit
labels, blind-before-save prediction delivery, reference/candidate comparisons,
stale judgments, and schema-6-to-7 backup/restore.

The synthetic grouped-correction fixture resolves two samples with one decision
submission (50% fewer submissions than individual decisions). This meets the
software action-count target for equivalent decisions; it is not a measured
reduction in real reviewer effort or a claim of improved biological accuracy.

Browser acceptance exercised grouped correction, automatic next task, direct study
summary navigation, sign-off/export, source-value confirmation, hidden model answers
before judgment, draft recovery across reload, and mobile layout at 390 pixels.

The reference set contains 20 frozen samples from ten cached plant studies.
No human answers were generated or signed by the evaluation. Expert accuracy and
real audit completion time remain pending the user's guided review.

## Live replay limits

The original 47-sample pilot used character-limited excerpts. The table workflow
retains full supplied evidence and can therefore fail when even a single sample's
sources exceed the effective 32K context budget. Such failures remain explicit
review tasks; no source text is silently discarded to obtain a passing result.

The initial development replay exposed long TF-binding responses exceeding a 2K
output reservation. It was stopped after diagnosis, with partial records retained.
The final workflow increases the reservation after truncation and estimates explicit
verification outputs from the requested field count, while retaining bounded retries.

These are engineering replays of previously used studies, not independently labelled
scientific benchmarks. Runtime and preparation outcomes must be reported separately
from source correctness and review workload.

## Required check results

`scripts/check.sh` completed with `GENESIS_REPO` set, exit 0.

| Check | Result |
| --- | --- |
| Ruff lint and format | PASS |
| ty source and test checking | PASS |
| verify_publication | PASS |
| verify_benchmark | PASS |
| verify_benchmark_spec | PASS |
| verify_benchmark_methods | PASS |
| verify_benchmark_references | PASS |
| verify_execution | PASS |
| verify_cli | PASS |
| verify_curation | PASS |
| verify_curation_artifacts | PASS |
| verify_curation_adapters | PASS |
| verify_web | PASS |
| verify_study_web | PASS |
| verify_table_web | PASS |
| verify_trustworthy_web | PASS |
| verify_ai | PASS |
| verify_llm_deployment | PASS |
| evaluate_metadata | PASS |
| verify_study | PASS |
| verify_scatac_inventory | PASS |
| verify_scatac_fragments | PASS |
| verify_scatac_pseudobulk | PASS |
| verify_scatac_exports | PASS |
| verify_scatac_inspection | PASS |
| check_reference_comparison | PASS |
| check_sra_sampling | PASS |
| check_queue_stop | PASS |

Optional binary bigWig reader checks: **NOT RUN** (no explicit reader interpreter).
Live hosted OpenAI/Anthropic APIs and real sequencing pipelines: **NOT RUN**.
Independent biological accuracy: **PENDING HUMAN AUDIT**.
Frontend Node 24 production build and synthetic browser acceptance: **PASS**.

## Final cached plant replay

| Study | Prepared | Failed | Seconds | Model requests | Pending decisions |
| --- | ---: | ---: | ---: | ---: | ---: |
| GSE255301 | 5 | 0 | 19.7 | 1 | 0 |
| GSE216351 | 0 | 5 | 2.8 | 0 | 5 |
| GSE101940 | 5 | 0 | 31.7 | 2 | 1 |
| GSE142613 | 5 | 0 | 59.3 | 2 | 0 |
| GSE60141 | 5 | 0 | 85.2 | 5 | 6 |
| GSE275897 | 5 | 0 | 45.7 | 2 | 5 |
| GSE197357 | 5 | 0 | 145.4 | 5 | 6 |
| GSE60554 | 2 | 0 | 44.1 | 2 | 1 |
| GSE245960 | 5 | 0 | 143.6 | 7 | 1 |
| GSE255302 | 5 | 0 | 20.6 | 1 | 0 |

Total: **42/47 prepared**, **5/47 failed**, 598.1 seconds across ten studies.
All five failures were GSE216351 samples whose full evidence exceeded the effective
32K token budget. This is a capacity failure, not a successful preparation.
The final replay includes failed/truncated attempts in its request and timing totals.
No independent accuracy or real reviewer-effort improvement is established.
The isolated reference-audit import passed with 20 samples and no human labels.

## 57-row timing regression

The same frozen 57-row table completed in **215.9 seconds** with **57/57
prepared, zero failures**, eight model requests, 99,268 input tokens and 4,008
output tokens. No previously populated required values were lost.
The preceding table implementation took 184.7 seconds with five requests; this
run is 16.9% slower with the additional verification, below the planned 50%
regression investigation threshold. It is a single-run comparison, not a latency
distribution or an accuracy result.

## Test deployment

The release was backed up and deployed to the existing HTTP test service.
All pre-existing catalog table contents matched before and after migration/import;
application-runtime SQLite integrity checks passed. Public health, authentication,
queue access, and the 20-sample audit passed smoke checks. Baseline and candidate
answers remained hidden before a judgment, with zero human labels generated.
The local provider retained its 32K context and 8K output ceiling.

Deployment preflight initially encountered a table without `rowid`; a subsequent
host-SQLite verification error triggered automatic rollback. The final preservation
check used the application SQLite runtime and sorted complete table rows, which
passed. Backups and the preserved intermediate database were retained.

See [sanitized machine-readable results](trustworthy-review-results.json).
