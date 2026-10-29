# Agentic mining validation

Measured on 2026-10-29 with the local Qwen3.8-27B-Q8_0 provider and a 32,768-token
context. [Sanitized measurements](agentic-mining-results.json) contain the exact
counts and timings. These are software and preparation outcomes, not expert-labelled
biological accuracy measurements. No sequencing data were downloaded or processed.

## Public metadata pilot

The final bounded search examined eight candidates across Arabidopsis/sorghum and
bulk RNA-seq/ATAC-seq. It prepared three studies and thirteen selected sample records,
excluded three studies, and queued two uncertain inclusion decisions. There were no
execution failures. Active run time was 160.98 seconds; final status was WAITING for
review. Default budgets are larger than this evaluation's 8 candidates, 4 studies,
20 samples and 20 minutes.

Prepared studies: GSE296367, PRJDB2180 and GSE274752. PRJDB686 and GSE329773 require
review. GSE320172, PRJDB41695 and PRJNA705005 were excluded by the model's assessment.
These relevance decisions still need independent evaluation; the run is not a recall
or precision benchmark and does not demonstrate ATAC preparation coverage.

Earlier iterations were not successful end-to-end pilots: two prepared no studies
because the model misinterpreted alternative selections or lacked protocol evidence.
A third prepared two studies but failed two assessments on nonmatching citations.
The final workflow uses candidate-specific scope, investigates sample/library
protocols once, and sends unresolved or invalid citations to human review. Exact
citation validation was not relaxed.

## Frozen replay and timing

All 47 samples across ten frozen plant studies prepared successfully, with zero
preparation failures. Five previously oversized samples in GSE216351 now fit bounded
evidence windows. Full sources remain retained; omissions are explicit in provenance.
This does not establish that every relevant passage was examined or every output
is correct. The frozen reference audit still needs human labels.

The separate 57-row timing fixture prepared 57/57 records with zero failed invocations
in 215.47 seconds, using eight model invocations and eight HTTP attempts. One review
decision remained. The prior table release took 215.92 seconds on that fixture, so
this run shows essentially unchanged timing, not a new speedup. The fixture reports
no required values lost relative to its stored baseline; that baseline is not expert
truth and this timing fixture is not a plant discovery benchmark.

## Automated and browser checks

`scripts/check.sh` completed with `GENESIS_REPO` set, exit 0.

| Check | Result |
| --- | --- |
| Ruff lint / format | PASS |
| ty source / tests | PASS |
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
| verify_mining | PASS |
| verify_ai | PASS |
| verify_llm_deployment (offline lifecycle) | PASS |
| evaluate_metadata (synthetic contracts) | PASS |
| verify_scatac_inventory | PASS |
| verify_scatac_exports | PASS |
| verify_scatac_workflow_import | PASS |
| verify_scatac_inspection | PASS |
| verify_macs3_launcher | PASS |
| check_reference_comparison | PASS |
| check_sra_sampling | PASS |
| check_queue_stop | PASS |
| Frontend production build | PASS |

Binary bigWig reader checks, hosted-provider comparisons and full sequencing-tool
validations were NOT RUN. The offline deployment test does not itself exercise a
real GPU; the separate live metadata evaluations above used the test node's model.

Mining regressions cover discovery, automatic preparation, inclusion review,
idempotent existing-study handling, drafts, stale submissions, pause/recovery,
context-window coverage, metadata attachment restrictions, exact row identity,
advertised file inventories, schema migration, immutable events/source revisions,
invalid citations and failed-inference usage accounting.

Browser acceptance covered creating and pausing a run, state across reload, inclusion
notes saved/restored, exclude-and-next, an empty completed inclusion queue, and a
390-pixel viewport without horizontal overflow. This used synthetic fixtures.

## Release protection

The test deployment backs up data/configuration while the app and worker are stopped,
checks SQLite integrity using the application runtime, migrates to schema 8, and
compares every pre-existing catalog table with its pre-migration fingerprint.
The model service is not restarted. Failed deployment restores the previous app image
and pre-migration data. Evaluation runs are isolated and are not imported into the
live reviewer database.
