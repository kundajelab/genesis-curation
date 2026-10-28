# Curation and optional AI: architecture

This document describes component ownership and compatibility requirements for
contributors. For commands and examples, use the [curation](../usage/curation.md)
and [AI provider](../usage/ai-providers.md) guides.

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

## Input and result review

Pipelines run in genesis-preprocessing; this repository imports their finished
outputs and does not launch them. Version 3 records and registry schema 4 keep the
input-revision, plan and collection-receipt formats written by the removed
`genesis study` commands, so existing catalogs stay readable. Registration alone
does not establish a scientific result or QC observation.

Matching input metadata approval can carry forward with its original evidence.
QC and export eligibility still require current result decisions. Source
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

Each report identifies the tested revision and exact scope. Local mocks and
synthetic fixtures do not establish live SSH, GPU/API compatibility, biological
metadata accuracy or full-dataset scientific performance.
