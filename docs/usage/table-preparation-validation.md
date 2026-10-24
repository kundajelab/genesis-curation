# Table preparation validation

The `study-table-1` workflow uses token budgets, deduplicated evidence, compact
changed-field output and durable batch checkpoints. It preserves sample-specific
citations and human decisions. Final sign-off is still manual.

## Checks

`scripts/check.sh` completed with `GENESIS_REPO` set (exit 0).

| Check | Result |
| --- | --- |
| Ruff lint and format | PASS |
| ty: source and tests | PASS |
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
| verify_ai | PASS |
| verify_llm_deployment | PASS (offline contracts) |
| evaluate_metadata | PASS (synthetic contracts) |
| verify_study | PASS (synthetic pipeline contracts) |
| verify_scatac_inventory | PASS |
| verify_scatac_fragments | PASS |
| verify_scatac_pseudobulk | PASS |
| verify_scatac_exports | PASS |
| verify_scatac_inspection | PASS; optional binary reader NOT RUN |
| verify_macs3_launcher | PASS |
| check_reference_comparison | PASS |
| check_sra_sampling | PASS |
| check_queue_stop | PASS |
| Node 24 frontend production build | PASS |
| Browser: import, preparation, review, sign-off and provenance export | PASS |
| Browser: mobile layout and lightweight progress polling | PASS |
| Live hosted OpenAI/Anthropic APIs | NOT RUN |
| Real scientific pipelines / sequencing downloads | NOT RUN |
| Independent biological accuracy | NOT EVALUATED |

The table tests cover whole-table admission, automatic input/output splitting,
shared evidence and wrong-sample rejection, omitted/duplicate rows, bounded recovery,
combined follow-up, reviewer priority, cache invalidation, cancellation and explicit
resume after worker restart. The existing study tests cover review preservation,
conflicting evidence, low confidence, distinct DNA/TF taxa and stale edits.

## Evaluation interpretation

The timing replay uses a frozen copy of 57 public metadata rows from GSE274557,
a human spatial-transcriptomics study. It does not download sequencing data or
modify the live study. This is an engineering workload, not a plant annotation
accuracy benchmark. Old model values are a comparison baseline, not ground truth.

Development experiments at 8K and 16K included failed startup and incomplete
extraction runs; these are not accepted quality results. More aggressive prompt
compression also caused overlooked required fields and extra recovery requests.
The release retains explicit requested fields and review questions on every row,
while sharing exact source text and existing common values. A regression assertion
protects this prompt layout.

A 32K context was measured on the existing 40 GiB A100 with the same Q8 weights.
Observed allocation was approximately 28,857 MiB, leaving 11,470 MiB free. The
existing GPU device mappings and compatibility libraries were retained; no Docker,
host permission or driver settings were changed. Context capacity is checked against
the running model, rather than inferred solely from the configured model name.

## Accepted replay

| Metric | Original per-row run | Table preparation |
| --- | --- | --- |
| Samples | 57 | 57 |
| Time | 1,582.9 s inference | 184.7 s total preparation |
| Model requests | 57 | 5 |
| Input tokens | 198,258 | 80,366 |
| Output tokens | 48,593 | 3,733 |
| Preparation failures | 0 | 0 |
| Previously supported required fields lost | — | 0 |
| Remaining exception tasks | — | 0 |

All five invocations succeeded; this count includes recovery and follow-up work.
The entire 57-row table exceeded the admitted budget, so it was split automatically.
The initial batch contained 34 rows. Smaller tables use one request when they fit.
These timings compare different workflow/context configurations and are not a
controlled hardware throughput benchmark. A prior replay of the same final prompt
layout took 183.3 seconds. See the [sanitized results](table-preparation-results.json).

Required organism, tissue and assay values were retained. Optional values differ:
protocol text is shorter; new BioSample/SRX identifiers come from exact GEO relation
lines; some old chemistry, DNA-context and notes values are omitted. Several old
values confused processing software with chemistry. These differences require
expert assessment, not automatic agreement with the old model. No biological
accuracy claim follows from zero software failures or an empty exception list.

The deployed test application retains the existing HTTP setup and Q8 model. A
stopped-service data/configuration backup and previous image were saved before
replacement. The full catalog dump hash was checked before and after startup to
verify that existing study records and reviewer work were unchanged.

Post-deployment health, authenticated provider settings, lightweight study progress
and logout checks passed. The live worker was resumed, and existing completed
studies were not automatically reprocessed. The full catalog hash matched exactly
across the deployment. No branch was pushed and no pull request was opened.
