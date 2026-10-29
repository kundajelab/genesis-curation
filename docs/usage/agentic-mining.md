# Find public data and review the exceptions

Open **Find data**, describe the research goal, select the species and assays, and
press **Start mining**. The worker searches GEO and ENA, examines study evidence,
imports matching sample metadata, retrieves eligible linked evidence, and prepares
suggestions using the configured model. Open **Review next decision** to work through
uncertain inclusion decisions and the existing metadata to-do list. Study sign-off
remains a human action. Preparation is not scientific QC or independent validation.

The first release supports Arabidopsis thaliana and Sorghum bicolor, with bulk RNA-seq
and ATAC-seq. Species and assay selections are alternatives; a study need not contain
all selected species or assays. Tissue and condition constraints remain requirements.
Other assays and single-cell-only studies are outside this discovery pilot.

## Budgets and recovery

Defaults are 50 candidate studies, 10 imported studies, 200 selected sample records,
and 30 minutes. Advanced controls permit at most 500 candidates, 100 studies, 2,000
samples and 240 minutes. These are bounded discovery budgets, not archive totals.
The sample budget is divided among studies; each study reports its selected and
available records. Metadata import is checkpointed in chunks of ten records.

Pause stops the current job cooperatively. Resume continues persisted work and can
increase limits to examine further candidates. It does not automatically expand
already prepared studies. After a worker interruption, explicitly resume the run.
Existing imported studies are linked without overwriting their human review work.
Failed candidates remain visible and receive bounded retries when resumed.

## Evidence and decisions

Searches use typed GEO/ENA queries, with bounded pagination and expansion. Models
cannot choose arbitrary URLs, execute commands, or download sequencing files.
A clear match requires source-backed evidence. An uncertain first assessment gets
one additional investigation of sample/library protocols. Remaining ambiguity goes
to a reviewer. Invalid model quotes follow this same path; they never authorize
inclusion or exclusion. Raw model responses and citation errors remain auditable.

Primary/secondary archive identifiers can establish aliases. Shared BioProject links
and similar titles do not prove that GEO subseries contain identical biological
samples. Possible overlaps require review. Biological samples, experiments and runs
remain distinct in inventories. Advertised sizes/checksums are archive claims, not
verification that a file was downloaded or passed QC.

Eligible CSV, TSV, TXT and XLSX metadata attachments are fetched from fixed archive
hosts only, with bounded sizes and no redirects. A table row is attached to a sample
only when its biological identifiers match that sample without sibling identifiers.
Expression matrices, counts and sequencing files are not fetched. Unmatched rows
are not copied across samples. Source revisions are retained by content hash in
addition to the expiring URL cache.

Table preparation uses the configured model's context and output limits. If one
sample's full sources exceed the budget, it selects a bounded, sample-first evidence
window. Original sources remain intact. Selected references and omitted counts are
recorded in provenance and shown in the study summary. Missing evidence in a partial
window is not evidence that a biological property is absent.

Reviewer notes and the current guided task survive reload. Decisions use version
checks to reject stale submissions. Source/action history and a JSON file inventory
are available from each run. The inventory is a discovery artifact; signed metadata
exports still come from the study summary.

## Operations

The existing app and worker serve mining; no new service or dependency is required.
Configure a model with an explicit context window using the existing provider setup.
Local OpenAI-compatible models and permitted hosted providers use the same interface.
Schema 8 adds mining runs and append-only events. Back up the application data before
deployment; the registry migration also retains its normal database backup. Restore
both the previous image and the pre-migration data together when rolling back.

Authenticated endpoints under `/api/mining-runs` provide create/list, run detail,
`control`, `inventory`, and candidate `decision`/`draft` operations. They use the
existing session, origin and CSRF protections. Mining inputs are stored by hash,
linked to recorded invocations; failed invocations retain reported token usage.

This implements the proposal's discovery, metadata extraction and expert-review
portion. Processing, QC and success reporting remain in genesis-preprocessing's
Nextflow workflows. This interface does not execute those pipelines, establish the
proposal's QC gates, or claim independently measured curation accuracy.
