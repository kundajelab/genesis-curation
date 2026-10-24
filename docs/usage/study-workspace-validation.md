# Study workspace validation — 2026-10-24

## Delivered workflow

A study is imported and prepared by the worker, exceptions become an ordered review
list, and a reviewer signs the complete study once before exporting a versioned
release. Notes and corrections survive navigation. Unresolved fields stay explicitly
unresolved. The GUI preserves the existing provider settings and accounts.

This implements the metadata-curation and expert-review portion of the Genesis
proposal's Activity 1 and month-one pilot: sample retrieval, harmonization, evidence,
provenance, uncertainty handling and expert sign-off. It does not claim completion
of genome/haplotype collection, the 8,000-experiment target, assay reprocessing,
ENCODE-style QC, or Gate 1. A signed metadata record is not a QC-passed experiment.
GEO samples and ENA runs are preserved as their source units; experiment-level
aggregation and deduplication must precede any proposal-scale counting.

## Repository checks

`scripts/check.sh` completed with exit code 0 and `GENESIS_REPO` set to the adjacent
preprocessing checkout. No sequencing files or scientific pipelines were run.

| Check | Result |
| --- | --- |
| Ruff lint | PASS |
| Ruff formatting | PASS |
| ty source | PASS |
| ty tests | PASS |
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
| verify_ai | PASS |
| verify_llm_deployment | PASS |
| evaluate_metadata | PASS |
| verify_study | PASS |
| verify_scatac_inventory | PASS |
| verify_scatac_fragments | PASS |
| verify_scatac_pseudobulk | PASS |
| verify_scatac_exports | PASS |
| verify_scatac_inspection | PASS |
| verify_macs3_launcher | PASS |
| check_reference_comparison | PASS |
| check_sra_sampling | PASS |
| check_queue_stop | PASS |

Optional subchecks remain separate from the passing script results:

- Binary bigWig readers: **NOT RUN** (no `--bigwig-python`).
- Live OpenAI/Claude API calls: **NOT RUN** (no configured live model credentials).
  Native provider wire formats and HTTP handling passed against local test servers.
- Full vLLM deployment lifecycle acceptance: **NOT RUN**. The existing llama.cpp
  Qwen deployment was used for the distinct live tests below.
- Real scientific pipeline checks (`validate-docker`, `validate-atac`,
  `tests/validate_study.py`): **NOT RUN**; separate approval is required.
- Independent biological accuracy and expert gold-label comparison: **NOT RUN**.

The MACS3 launcher printed expected rejection messages for deliberately invalid
fixtures; its assertions and overall command passed.

## Browser acceptance

The built GUI was exercised with the existing browser automation tool against an
isolated local application and mock worker:

- Table upload and automatic study preparation: PASS.
- Note persistence after reload: PASS.
- Deferral followed by explicit unknown resolution: PASS.
- Inline correction with a selected supporting passage: PASS.
- Navigation blocked while saving; correction and note restored after Settings: PASS.
- One study sign-off and signed JSON download: PASS.
- Unknown field excluded; corrected field included in export: PASS.
- Mobile viewport (390 × 844), no horizontal overflow: PASS.
- Default study screen hides legacy record-export links: PASS.
- Frontend TypeScript check and Vite production build: PASS.

Backend regression checks additionally cover atomic grouped corrections, stale
reviews/sign-off, changed-source invalidation, immutable historical releases,
wrong-sample citations, paper-context attribution, low-confidence review routing, failed sample retention,
interruption recovery (including failed intake and orphaned queued jobs),
schema-v5 backup/migration and study cursor pagination. Single-cell RNA spelling
variants select the cell-type guidance profile.
The public pilot exposed an overly broad PubMed reference traversal; retrieval now
follows only each linked article's own PMC identifier, with a regression fixture.
The sorghum RNA study also exposed non-breaking-space transcription differences.
A unique whitespace-only span restoration now records the model quote and exact
source quote while retaining the raw invocation. Read-only replay changed five
rejected responses to five valid responses without relaxing the exact validator;
regression checks retain rejection of altered words.
DAP evaluation exposed missing taxon registrations for DNA-organism and
TF-source-organism fields. Vocabulary v3 adds those field-specific registrations
using the already verified taxa; v1 and v2 remain immutable. The regression fixture
distinguishes Arabidopsis DNA from a maize TF and still rejects a mismatched taxon ID.
The maize pilot also exposed unreliable model copying of prior values and ontology
IDs. Workflow revision `study-2` makes both server-owned: version-checked record
snapshots supply prior values, and exact registered label/alias matches supply IDs.
Raw model responses remain audited; legacy provider validation is unchanged.

## Local model evaluation

`tests/evaluate_study_live.py` ran against the existing Qwen3.8-27B-Q8_0 endpoint,
using an isolated registry. The final two-record synthetic study passed in 5.49 seconds:
no automatic human approvals, missing tissue stayed unknown, scripted sign-off
produced a verifiable release, and unresolved tissue stayed out of the export.
This is software acceptance, not a biological accuracy estimate.

The public pilot manifest is `tests/fixtures/study/public-pilot.json`: ten studies,
up to five samples each, selected in repository order. Mixed-assay studies are
explicitly labeled. Development/reserved labels are organizational partitions,
not an independently curated held-out benchmark. No public pilot study is signed
by the evaluation script. Results are recorded below.

## Public metadata pilot results

Ten studies supplied 47 samples (GSE60554 contains only two samples). Latest
outcomes: **44 prepared; three model proposals FAILED validation**. Eight studies
prepared without invocation/validation failures. The isolated registries contain
17 review tasks, including field uncertainties and the three failed proposals;
none of the public studies was signed off.

| Study | Manifest assay category | Samples | Preparation outcome | Review tasks |
| --- | --- | ---: | --- | ---: |
| [GSE255301](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE255301) | RNA-seq | 5 | 5 prepared | 0 |
| [GSE216351](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE216351) | RNA-seq | 5 | 5 prepared | 0 |
| [GSE101940](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE101940) | ATAC-seq | 5 | 5 prepared | 0 |
| [GSE142613](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE142613) | ATAC/RNA mixed | 5 | 5 prepared | 0 |
| [GSE60141](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE60141) | DAP-seq | 5 | 4 prepared; 1 FAILED | 7 |
| [GSE275897](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE275897) | DAP-seq | 5 | 5 prepared | 4 |
| [GSE197357](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE197357) | ChIP-seq | 5 | 5 prepared | 3 |
| [GSE60554](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE60554) | ChIP-seq | 2 | 2 prepared | 1 |
| [GSE245960](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE245960) | WGBS/BS-PCR mixed | 5 | 5 prepared | 0 |
| [GSE255302](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE255302) | WGBS | 5 | 3 prepared; 2 FAILED | 2 |

The remaining rejected responses are:

- **GSM1924998** (GSE60141): a cultivar quote was paired with a locator containing
  the DAP library-strategy line, rather than the quoted passage. The proposal was
  rejected and remains a failure task.
- **GSM8068755 and GSM8068756** (GSE255302): duplicate metadata fields in model
  responses were rejected; neither is counted as prepared.

These failures were not acknowledged or silently promoted by the evaluation.
Preparing a sample means its proposal passed software validation, not that an
expert verified the annotation or that scientific QC passed. A prepared study
can still have decisions to resolve before sign-off.

This was an iterative development pilot. Initial failures prompted the bounded
publication, whitespace, vocabulary and server-owned normalization fixes described
above. Failed studies were retried with previous results retained. Successful
earlier proposals remain marked with their original workflow revision. The final
four studies and maize retry exercised `study-2`; this is not a frozen held-out
accuracy comparison. The first five samples in repository order are a convenience
sample, not a representative assay benchmark. See the [machine-readable results](study-workspace-pilot.json)
for sample failures, evidence coverage, timings, workflow revisions and retained
initial outcomes. Full source snapshots and invocation audits remain in the
isolated evaluation registries on the test node.

## Deployment and recovery

The test node was updated in the user-selected HTTP mode. The application and
worker were replaced; the existing model, nginx and unrelated services were retained.
A stopped-service backup of state/configuration and the previous application image
were preserved before switching. A separate copy was migrated from schema v5 to v6;
the canonical legacy export fingerprint matched before/after, and the automatic
pre-migration backup was present. Authentication, logout, study listing, provider
preservation and frontend delivery passed against the public deployment.

Rollback requires both the preserved image and pre-migration state; do not run the
old application against a schema-v6 database. Stop app/worker, archive current state,
restore the backup into the configured data location, restore the previous image,
and start app/worker with their original configuration. Keep post-upgrade state so
new reviewer work can be recovered. Never delete or overwrite the sole backup.

## Limits and next validation

The software does not measure semantic citation correctness or biological accuracy.
Independent plant experts should review stratified sample annotations and score
field accuracy, correct abstention, wrong-sample leakage and correction workload.
Only after that review should automated acceptance thresholds or broader deployment
be considered. The current workflow always requires final human sign-off.

A synthetic backend check imported and retrieved all 1,001 records without truncation:
1.22 seconds to import and 0.12 seconds to retrieve on this host. The detail payload
was 7,508,572 bytes. Study detail currently returns all member evidence, despite
paginated study/task lists. This is not a browser performance benchmark; large-study
rendering and multi-reviewer load remain unbenchmarked.
Ontology v3 covers five core species and five tissues; other labels remain explicit
unmapped values rather than invented ontology identifiers. The cell-type profile
uses Alex's guidance but does not run expression-based cell annotation.
