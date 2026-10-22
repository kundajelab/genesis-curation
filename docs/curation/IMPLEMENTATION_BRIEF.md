# Curation and optional AI: architecture

This document describes component ownership and compatibility requirements for
contributors. For commands and examples, use the [curation](../usage/curation.md),
[study](../usage/studies.md) and [AI provider](../usage/ai-providers.md) guides.

## Scientific computation and dataset state

Nextflow owns scientific computation. Curation uses the existing DAP and bulk
ATAC input formats, outputs, reference policies, caches and resume behavior.
Ordinary preprocessing does not require a catalog, model or review approval.

Execution state belongs to the campaign's `state.sqlite`. A separate
controller-local registry owns source snapshots, entities, artifact locations,
validation findings, measurements, assessments and review history. Campaign,
job and attempt identities connect the stores. Workers send checksummed records;
they do not write to a shared SQLite database. Artifact paths include the worker
identity, and no shared filesystem or implicit data transfer is assumed.

Execution status, structural validation, scientific QC, metadata review and
eligibility for a particular export are separate. A reviewer cannot make a
corrupted artifact structurally valid. Missing evidence is unknown, not zero.
QC policies assess recorded measurements without rerunning sequencing; plant
biological thresholds remain unspecified unless an explicit policy supplies them.

## Contracts, validation and review

- `contracts` supplies typed records and packaged JSON Schemas. Unsupported
  versions, malformed types, nonfinite numbers and stale content hashes fail
  validation. Older record versions retain their original bytes and identities.
- `curation` adapts existing scientific validators. Scientific task helpers do
  not depend on the registry or optional AI modules. Metadata inspection and full
  artifact scans have distinct completeness levels and explicit budgets.
- Artifact requirements depend on assay, role and retention state. Zero peaks
  can be valid, and intentionally pruned BAMs are distinct from missing required
  outputs. Binary bigWig inspection uses a separate, explicitly selected Python
  environment rather than adding its dependency to core Python 3.14.
- `registry` uses transactional migrations, bounded local writer locks, atomic
  imports and immutable history. Repeated imports are idempotent. Search uses
  literal text and structured filters; it does not execute user- or model-written SQL.
- Review decisions bind to exact source, manifest, evidence, policy and export
  versions. They record local OS identity and require a current concurrency token.
  Proposal approval and application are separate operations. Metadata edits create
  revisions; they do not rewrite executed sample sheets.
- Reviewed exports are manifest catalogs, not copied sequencing data or training
  arrays. They report included candidates, exclusions, exceptions and denominators.
  Ordinary registry exports remain available without review gating. The browser
  status page is read-only and binds to loopback.

## Prepared studies

`genesis study` connects prepared inputs to the existing execution and curation
components. It does not introduce a second scheduler or accession-first acquisition.
Version 3 records describe input manifests, study revisions, plans and collection
receipts. Registry schema 4 separates input and result review and stores collection
lineage; registration alone does not establish a scientific result or QC observation.

ATAC jobs combine technical lanes within a library. DAP jobs group treatments
sharing a control while retaining separate library identities. Worker assignments,
paths, profiles and Git revisions are explicit configuration. SSH workers require
path mappings for local inputs.

The existing controller and detached supervisors own execution. Stale plans block
new submissions while existing attempts continue to be observed. Collection reads
worker-local outputs with bounded validation and transfers checksummed receipts.
Import, diagnostic QC and receipt lineage commit together. Retrying collection
never resubmits scientific analysis.

Matching input metadata approval can carry forward with its original evidence.
QC and export eligibility still require current result decisions. Late receipts
remain historical; they cannot replace current reviewed records. Source
classifications cannot be weakened through re-ingestion or a change of review scope.
New DAP run provenance supports full validation; historical runs lacking the
required evidence remain incomplete.

## Optional AI and model services

Core validation, catalog queries and manual review work offline. Providers have
disabled, mock and explicitly configured live modes. Requests have capability
checks, output validation, budgets, egress restrictions and secret redaction.
Invocations retain model and prompt identity, evidence, responses and available
usage measurements. Unknown model revisions or costs remain unknown.

Models propose metadata or explanations. Deterministic extraction and validation
check field evidence against saved sources; JSON validity does not establish
biological correctness. Diagnosis returns typed proposed actions and cannot run
shell commands, SQL, retries, repairs, policy changes or approvals. Existing
worker retry and UNKNOWN reconciliation rules remain authoritative. Logs and
source text are untrusted data; concise evidence-backed explanations are retained,
not hidden reasoning traces.

Model services use a separate serving environment and the existing local/SSH
transport. Exact reviewed plans, node-local reservations and owned supervisors
control their lifecycle. Apply rechecks resources, revisions, license and runtime
requirements. Unknown service state retains reservations. Endpoints use loopback
and owned SSH tunnels; no model, GPU, service or external fallback is chosen
implicitly. See the [service guide](../usage/llm-deployment.md).

## Validation evidence

[Foundations](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/curation-foundations-validation.md) covers
contracts, catalog scale, review and installed-package tests.
[AI validation](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/hybrid-llm-validation.md) covers fake HTTP,
synthetic metadata and service lifecycle simulations.
[Study validation](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/study-workflow-validation.md) covers
local supervision, collection and review lineage.
[Real-tool study tests](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/real-study-validation.md) exercise
DAP and ATAC through the coordinator using synthetic scientific fixtures.

Each report identifies the tested revision and exact scope. Local mocks and
synthetic fixtures do not establish live SSH, GPU/API compatibility, biological
metadata accuracy or full-dataset scientific performance.
