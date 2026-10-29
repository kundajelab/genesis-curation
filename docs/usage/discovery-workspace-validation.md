# Discovery and curation demo validation

Validated 2026-10-29. These are software, deployment and small live metadata checks,
not a biological accuracy benchmark or a claim of completed corpus curation.

## Source inventory

The external reference workbook was imported read-only. Its SHA256 is
`c7567d0d94aed06cba53652a2524fbba63a74542ee02ce3d103b8d81f33a7a45`.
All 16,927 source rows were retained, including headerless first rows and repeated
column labels. The Google Sheet was not edited.

| Source tab | Rows | Curation modality |
| --- | ---: | --- |
| DAP-seq | 6,035 | DAP-seq |
| DAP-seq genomes | 16 | Genomes |
| TSS | 716 | TSS |
| ATAC | 1,487 | Bulk ATAC |
| MPRA Papers | 10 | MPRA |
| TSS Fable Curated | 616 | TSS |
| Bulk ATAC Fable Curated | 7,638 | Bulk ATAC |
| scATAC Fable Curated | 409 | scATAC |

Original names above are provenance labels, not claims of pipeline authorship.
The inventory has 257 distinct normalized species labels, including unresolved
names and path-derived hints; that is not a count of verified taxa.

## Checks

- `scripts/check.sh`, with `GENESIS_REPO` set: PASS, exit 0. Ruff, formatting,
  source/test type checks, repository verification scripts and launcher checks ran.
- New workspace checks: PASS for lossless import, idempotence, immutable history,
  queued preparation, missing-evidence review, real-query receipts, pause/recovery,
  raw archive retention, deterministic archive fields and clean-export exclusions.
- Pinned Docker frontend/Python build: PASS. Browser navigation, modality tables,
  filtering, history, export and expandable skills worked. No console errors after
  final reload. The mobile viewport had no horizontal document overflow.
- Migration to registry schema 11 preserved every pre-existing immutable record,
  curation event and study event, checked against the deployment backup.
- Binary-reader checks: NOT RUN (no `--bigwig-python`). Real sequencing-tool tests,
  public sequencing downloads and a held-out biological evaluation: NOT RUN.

## Live observations

One Arabidopsis/Sorghum ATAC discovery campaign completed 22 searches and retained
381 candidate rows. These can overlap by study and are not 381 validated datasets.
Google rejected automated requests on the node; Brave supplied relevant results.
The direct Brave probe returned 19 hits in 0.855 seconds. Native archive searches
and engine failures remain separately visible in history.

The initial live curation test exposed an unsupported `not_applicable` response.
The validator rejected it. A subsequent test exposed missing deterministic
extraction from retained raw archive responses. The fixes add explicit applicability
instructions, validation feedback on bounded retries, and exact archive key/value
projections alongside the immutable raw responses. Failed and earlier proposals
remain in history; they were not rewritten to appear successful.

After these fixes, the SRX4740475 test using local Gemma 4 12B completed with no
preparation failures. Species `Zea mays`, assay `ATAC-seq`, layout `PAIRED`, tissue
`Seedlings` and genotype `wild type` were recovered from sample evidence. The
imported genotype `B73` remains visible for comparison. The record stays **Needs
review**; completing model inference does not approve unresolved fields.

This sample used 3 inference batches, 10,651 prompt tokens and 1,009 output tokens.
Recorded batch processing took 18.47 seconds; this excludes archive retrieval and
queue time. No hosted API cost was incurred by these local-model tests; node/GPU
cost was not measured. A single sample does not establish throughput or accuracy.

The full background re-check was queued with 16,728 distinct work items across
17,308 imported and discovered source rows. Exact experiment identities share work;
study-lead expansion can add more items. This campaign is ongoing, not completed.

## Responsiveness

- Full live XLSX import: 16.13 seconds, performed outside HTTP request handling.
- Browser-observed filtered pages: 68–75 ms for 50 rows, six requests.
- Selected curation acknowledgement: 137 ms; complete campaign acknowledgement:
  374 ms. These measure queue/UI response, not inference completion.
- Full live workbook/history ZIP download: HTTP 200, 11,406,914 bytes at test time.
  Export runs as a background job. Later exports include later history.
- Local full-workbook export: 12.55 seconds. Local unfiltered pages: 1–5 ms.

## Operational and scientific limits

The demo node has less than 1 GiB free on its root disk. Campaigns pause below
512 MiB and retain checkpoints. A full corpus re-check needs more local capacity;
the database must not be moved casually onto network storage.

Qwen, Gemma 4 12B, Gemma 4 E4B and Ministral 3 14B are configured locally, with
one model active on the GPU at a time. Jobs retain their chosen provider and wait
when another model is active. Hosted OpenAI/Claude remain disabled until configured.
The model container's inherited Docker health probe targets port 8080 rather than
its configured serving port; this produces an unhealthy label despite successful
live inference. The model controller checks the actual serving endpoint.

Paper attribution, GSA retrieval, ambiguous taxonomy, modality/controls and missing
sample fields still require review where sources cannot establish them. Known
repository links do not imply complete repository coverage. Search pagination is
bounded; source failures and retained cursors are recorded. Imported Sheet labels
are not a gold-standard accuracy set. Confidence categories are evidence/review
states, not calibrated probabilities. A stratified, independently reviewed
evaluation and a completed multi-study run remain necessary to demonstrate
biological accuracy and sustained scaling.
