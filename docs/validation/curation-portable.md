# Portable curation acceptance

This change carries forward the archive-first RNA annotation and measured-QC
workflow described in slides 30–35. It adds a shared GUI/CLI application boundary,
private runtime initialization, explicit provider defaults, optional request-budget
reservations and portable deployment. No sequencing preprocessing is included.

## Recorded checks

`scripts/check.sh`: PASS (lint, formatting, source/test type checks and all 18
verification programs). Biological accuracy and 15,000 live LLM annotations are
explicitly NOT RUN in the offline suite.

- Synthetic browser: PASS; 63 ms from quality-evaluation click to visible queued
  feedback, 25 retained and 42 excluded fixture records, zero browser errors.
- Native launcher startup, loopback health and child-process shutdown: PASS. The
  host's older npm emitted a Node deprecation warning; application browser errors
  were zero. Use the npm version bundled with Node 24 on fresh installations.
- Fresh Docker build, empty workspace initialization, server/worker startup, CLI
  summary and runtime doctor: PASS. No provider is silently installed or selected.
- Scale fixture: 15,000 records / 1,500 studies; recorded p95 page time 1.8 ms,
  preview 65.7 ms, queue operation 247.0 ms on the acceptance host. These are local
  synthetic timings, not guaranteed remote-network performance.
- Local model lifecycle: synthetic checks only. No new weights downloaded and no
  GPU or local inference test performed in this API-only acceptance run.

## Public-metadata pilot

Nine records across three species/studies were processed with a configured hosted
Opus 5.5 endpoint: PRJDB3559 (Arabidopsis RNA-seq), PRJDB3281 (sorghum RNA-seq),
and PRJNA697943 (maize ATAC-seq). Eighteen archive retrieval receipts were recorded.
No sequencing reads were downloaded. All nine records completed the corrected
curation pass; four pending review items remained across two studies. None were
signed off by the test.

The first attempt exposed an Anthropic structured-output schema incompatibility:
array constraints such as maxItems were rejected by the endpoint. The wire grammar
now omits unsupported constraints while the full original schema still validates
returned output locally. Configuration errors stop instead of causing row-splitting
retries. The successful retry took 84.9 seconds across the three studies.

The persistent request ledger reserved $3.623268 across 30 attempts, including the
failed first attempt, below the authorized $5 cap. This is a conservative reservation,
not measured billing. The pilot establishes functional retrieval/annotation/review
behavior; biological accuracy is NOT EVALUATED. It is not a hand-curated benchmark,
and increased evidence coverage must not be reported as accuracy.

## Remaining limitations

Hosted model availability depends on the supplied endpoint and account. The local
CLI requires one curator account per runtime. The built-in plant vocabulary remains
limited; unresolved tissue/stage terms need expert review. Paper access and sample
alignment constrain enrichment, and source coverage can be partial. Production
public hosting requires an operator-managed TLS proxy and appropriate account policy.

Private acceptance logs, archived evidence and model weights remain outside Git.
Shared-volume preservation was verified file by file with SHA-256 before removing
its Genesis directory. No original reference spreadsheet or remote demo was changed.
