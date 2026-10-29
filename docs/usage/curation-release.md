# Plant metadata curation release

## Reviewer workflow

Use **Discover** to start a bounded plant metadata search. The system retrieves archive
metadata, proposes evidence-linked fields, and puts unresolved questions in **Review**.
Review presents the next question and preserves position. **Results** lists draft records
by modality and exports a workbook with candidate, unresolved, excluded, and manifest tabs.
Draft results are not signed releases or proof that the underlying sequencing passed QC.

The external multi-tab reference workbook is independently curated by people and other
models. It is not output from this application. `scripts/snapshot_reference.py` only reads
it; reference labels are never supplied to the evaluated model as answers.

Discovery supports exact plant scientific names validated against NCBI taxonomy and
TSS, bulk ATAC, and scATAC searches. Archive search hits are candidates: controls and
conflicting assay strategies remain excluded or unresolved. Cell-type annotation is an
optional evidence adapter; see [celltype.md](celltype.md). Pipeline processing remains in
genesis-preprocessing.

## Local models

The catalog includes pinned Google Gemma 4 E4B and 12B QAT Q4_0 artifacts and Mistral
Ministral 3 14B Instruct Q8_0, alongside the existing Qwen baseline. Model cards link to
official sources and distinguish verified installation, inference smoke tests, and
biological accuracy. All three new presets passed live tokenization and structured-JSON extraction probes at
32,768 tokens on the test A100. A smoke test does not validate metadata accuracy.

Use `genesis ai local-model list` and `genesis ai local-model download PRESET --directory
MODEL_STORAGE` for checksum-verified installation. The downloader requires storage for
the artifact plus a reserve, rejects mismatched existing files, and never silently
replaces a model. Hosted OpenAI-compatible and Anthropic provider configurations remain
available through the existing provider interface.

An optional **host-owned** controller (`deploy/web/model-controller.py --web-root
DEPLOYMENT_ROOT`) enables the GUI's Activate buttons on the single-GPU test deployment.
It adopts the existing pinned Qwen container, accepts only catalog presets, drains active
work, checks model hashes, and verifies context, tokenization, and JSON extraction before
resuming queued work. Failed starts restore the previous owned container; failed recovery
leaves inference paused. The web container receives no Docker socket or arbitrary command
execution capability. This controller is specific to the existing Linux NVIDIA test
layout; inspect its device and volume assumptions before using another host.

Queued jobs retain their provider identity. Jobs for an inactive local model wait until
that model is active again. `tests/evaluate_table_live.py --lease-root SERVICE_DATA` reserves
the model for an isolated benchmark and prevents switching or concurrent queued inference.
A stale controller pauses managed local work rather than silently using another model.

## Evidence and scale

`plant-v4.json` is generated from a pinned Planteome release and contains 1,360 anatomy
terms and 296 development stages with unambiguous exact aliases. Historical vocabularies
remain available. Prompts receive a bounded relevant subset; full normalization stays on
the server. Compact XML excerpts cite exact source character ranges and retain the complete
source for reviewers.

Results and review queues use indexed SQLite projections. Tests exercise 10,000 synthetic
result rows, paginated tasks, and workbook round trips. These are software scaling tests,
not 10,000 scientifically curated experiments. Discovery jobs remain bounded; a complete
10,000-record scientific campaign has not been demonstrated.

The frozen pilot contains 100 accession-reconstructed experiment/library records stratified
30 TSS / 40 bulk ATAC / 30 scATAC, spanning 85 archive studies and 61 archive species.
Retrieval succeeded for all 100 using ENA with exact-accession NCBI fallback. GSA records
were excluded from this pilot and remain unverified. Reconstruction from known accessions
must not be reported as independent discovery.

The initial Qwen run (study-table-3) took 1,773.52 seconds and reported 543,422 tokens
(498,282 prompt, 45,140 completion) across 47 invocations. Eleven records failed citation
identity checks. The old progress counter overlapped two failed/completed records; exclusive
counts are 89 nonfailed and 11 failed. The counter has been corrected. No expert biological
accuracy or dollar cost has been established. The new workflow (study-table-4, plant-evidence/3) completed all 100 records with zero
terminal failures in 1,315.10 seconds (21.9 minutes), 25.85% faster in this paired run.
It used 327,596 tokens (292,118 prompt, 35,478 completion), 39.72% fewer, across 19
invocations. One invocation failed and recovered; 37 review decisions remain. Compact
XML excerpts reduced source text by 49.09%. These are workflow and runtime measurements,
not biological accuracy or a repeated statistical performance benchmark.

Before scaling to 10,000 real records, complete the blinded 100-record expert audit and
quality gate. Keep candidate predictions, expert labels, and external reference curation
separate. Next measurements must compare identical frozen evidence and report abstentions,
failures, token usage, elapsed time, and human correction effort. LLM curation of the 1,000-record stage,
independent discovery recall, and paper-attribution quality remain to be demonstrated.
Single-sample XML tissue/genotype conflicts are now checked; mixed-sample and study-context
XML is deliberately excluded from this check.

## Release verification

`scripts/check.sh` passed with `GENESIS_REPO` set, including launcher checks, local-model
queue isolation and recovery, exact evidence excerpts, and 10,000-row synthetic exports.
Binary bigWig reader checks were **NOT RUN** because no reader environment was supplied.
The pinned Node 24 container build passed. Authenticated HTTP checks passed for navigation,
390-pixel layout without horizontal overflow, paginated results, model status, and XLSX
download. The schema-10 deployment preserved all 36 pre-existing catalog tables after backup.
Hosted API comparisons, real sequencing-tool validations, and expert accuracy are not
covered by these checks. The test model controller currently runs as a host process; after
a host restart it must be started again, and managed inference waits while it is unavailable.

The frozen pilot represents 116 sequencing runs: TSS has 30 experiments / 24 studies /
28 species / 35 runs; bulk ATAC has 40 / 39 / 36 / 41; scATAC has 30 / 22 / 17 / 40.
These strata come from the external reference and are sampling categories, not validated
assay labels. Archive evidence, not those labels, enters the model.

A read-only vocabulary coverage check found exact aliases for 70 of the external bulk
ATAC tab's 401 distinct tissue strings (2,467 of 7,638 rows). The corresponding figures
were 23/119 strings and 82/616 rows for TSS, and 21/66 strings and 283/409 rows for scATAC.
This measures exact-string coverage, not correctness. Composite or stage-qualified tissue
labels still need evidence-based interpretation; the expanded vocabulary does not by itself
resolve all metadata inconsistencies.

## Proposal alignment and repository consolidation

This release supports the Objective 1 / Activity 1 pilot: metadata discovery, evidence
retrieval, harmonization, failure reporting, and expert review. It does not establish the
proposal's genome/haplotype corpus, assay QC thresholds, 6,000-experiment decision gate,
model-training performance, or MPRA outcomes. Processing, hashing, and success reporting
remain the responsibility of the Nextflow workflows in genesis-preprocessing.

The release integrates the 28 commits on the published agentic-mining branch onto main's
pipeline-separated base, then adds the cell-type adapter and release changes in distinct
commits. A range comparison accounts for the earlier review, study-workspace, table, and
trustworthy-review branches: corresponding changes are present, with adjustments for the
pipeline commands/tests removed from main. Old worktrees and branches remain intact.
Publishing or merging the consolidated release requires the repository owner's approval.

An independent live search probe (no reference accessions or labels supplied) completed
18 GEO/ENA queries across Arabidopsis, sorghum, and maize and the three target modalities
in 16.50 seconds, with no query failures. It returned 264 hits / 219 distinct candidate
accessions. The per-modality sets contain 141 TSS, 67 ATAC, and 48 scATAC candidate
accessions, with overlap. This was a bounded search-tool probe, not end-to-end LLM
curation, deduplicated biological-study coverage, precision, or recall. Broad archive
searches still require library-level eligibility checks.


The 1,000-record metadata retrieval stage completed 997 records representing 254 archive
studies, 161 species, and 1,166 runs. By reference sampling stratum: TSS 300 experiments /
58 studies / 28 species / 334 runs; bulk ATAC 399 / 161 / 145 / 405; scATAC 298 / 37 / 17 /
427. Strata are not validated modality labels. The three failures remain in the manifest:
`SRX11670424` has missing sample/species fields in the ENA run report; `DRX512984` and
`DRX512983` did not return the requested experiment identity. No replacement records or
reference labels were substituted. This cohort remains unsealed because retrieval is
incomplete. Its retrieval runtime was not instrumented. No 1,000-record LLM run or
10,000-record biological validation has been completed.

The GUI contains a new, isolated 100-record blinded audit comparing the baseline and new
Qwen workflow on identical sources. Seven fields are fixed before viewing predictions:
organism, assay, tissue, genotype, developmental stage, treatment, and library layout.
**Review → Continue quality review** opens the next unfinished record and saves progress.
The older 20-record audit remains separate. The new audit starts with zero human answers;
no expert judgments have been manufactured. Source-derived attributes are confirmation
shortcuts, not AI predictions, and conflicting attributes are not silently resolved.
