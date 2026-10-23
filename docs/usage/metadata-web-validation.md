# Metadata web validation — 2026-10-23

The implementation was exercised in a task worktree and deployed to a separate
Linux test node. The public GUI used the explicitly selected HTTP test mode.
Application state and credentials stayed on local disk; model weights used a
separate Genesis directory on mounted shared storage.

## Repository checks

`scripts/check.sh` was run with `GENESIS_REPO` pointing at the adjacent preprocessing
checkout. Each command below completed successfully; optional subchecks are listed
separately and are not counted as passing.

| Check | Result |
| --- | --- |
| Ruff lint | PASS |
| Ruff formatting | PASS |
| ty, source | PASS |
| ty, tests | PASS |
| verify_publication | PASS |
| verify_benchmark | PASS |
| verify_benchmark_spec | PASS |
| verify_benchmark_methods | PASS |
| verify_benchmark_references | PASS |
| verify_execution | PASS |
| verify_cli, including installed Pixi launcher and pipeline forwarding | PASS |
| verify_curation | PASS |
| verify_curation_artifacts | PASS; optional binary bigWig reader NOT RUN |
| verify_curation_adapters | PASS |
| verify_web | PASS |
| verify_ai | PASS; hosted live API checks NOT RUN |
| verify_llm_deployment | PASS offline; its full vLLM/SSH lifecycle acceptance NOT RUN |
| evaluate_metadata | PASS synthetic deterministic/mock evaluation |
| verify_study | PASS synthetic campaign and input checks; real scientific pipelines NOT RUN |
| verify_scatac_inventory | PASS |
| verify_scatac_fragments | PASS |
| verify_scatac_pseudobulk | PASS |
| verify_scatac_exports | PASS |
| verify_scatac_inspection | PASS; optional binary bigWig reader NOT RUN |
| verify_macs3_launcher | PASS |
| check_reference_comparison | PASS |
| check_sra_sampling | PASS |
| check_queue_stop | PASS |
| React/TypeScript production build | PASS |
| Application Docker image build | PASS |
| Deployment shell syntax and Compose configuration | PASS |

The web tests cover strict/idempotent imports, exact evidence, stale-review rejection,
approved-only export, example scoping/invalidation, sparse proposals, native OpenAI
and Claude HTTP protocols, truncation/refusal, durable jobs, cancellation while
running, restart recovery, secure cookies, CSRF/origin checks, upload bounds and
metadata/audit backup restoration. These use synthetic fixtures and local servers.

## Live model and browser checks

- Model: `unsloth/Qwen3.8-27B-GGUF`, `Qwen3.8-27B-Q8_0.gguf`.
- Hugging Face revision: `4ca720788d1e01f1bff70c033e0d0028fd02e502`.
- Verified SHA256: `a680f44a06920e5d689774823782006aa3acc8db95750323373b24139b67e348`.
- Runtime: official CUDA-12 llama.cpp image,
  `sha256:e28ed2eea6752a16e22fabb1d449c7557e0ab2494e5d80552e1b6961f1671086`;
  reported build `0.7.0-dev`, build 11804, commit `c7fc50941`.
- Hardware: one A100-SXM4-40GB, driver 550.120. Observed model GPU usage was
  27,281 MiB, not a measured peak guarantee.
- Configuration: Q8_0 weights, 8192 context, concurrency one, eight CPUs, non-thinking
  output. No sequencing data was downloaded.
- Real structured JSON probe: **PASS**. This tested an actual model response, not
  merely a listening port.
- Browser login, public metadata preview/import, approval, approved-only export,
  live contextual conversation with source citations, live proposal, and an explicit
  request for missing tissue information: **PASS**.

The node's existing NVIDIA container hook failed with insufficient permissions.
The optional device override used the curator's existing GPU group and explicit
GPU devices. The bundled CUDA compatibility libraries resolved the subsequent PTX
compiler mismatch. Docker configuration, host drivers, group membership and device
permissions were not changed. An initialized GPU alone did not establish inference
readiness; the final structured-response probe did.

The preliminary development run rejected an invalid locator and swapped ontology
label/ID fields. The implementation now supplies indexed evidence, constrains source
locators and vocabulary IDs, and describes their meaning explicitly. Invalid model
outputs remain rejected. Sparse proposals preserve the complete deterministic field
list while avoiding regeneration of unchanged/unsupported fields.

## Final synthetic comparison

Six cases were run with each skill setting. Four development cases were used during
implementation: explicit fields, prose extraction, missing evidence and conflicting
tissue. Two additional held-out synthetic cases tested rice prose and an ATAC title
that did not specify bulk versus single-cell preparation. Each case had two specified
gold field expectations. No model proposal approved any field.

| Setting | Successful runs | Matched gold fields | Median latency | Range |
| --- | --- | --- | --- | --- |
| No skill | 6/6 | 12/12 | 2.61 s | 0.764–14.088 s |
| Plant metadata skill | 6/6 | 12/12 | 3.18 s | 0.757–13.192 s |

Both settings matched all four held-out field expectations. These small synthetic
fixtures establish workflow behavior; they do not establish biological accuracy,
human correction rate, or an accuracy advantage from the skill. The larger initial
proposal format took about 62 seconds on the explicit development case; the final
format returned that case in about 2.6 seconds under both settings.

Hosted OpenAI/Claude acceptance, Alex's cell-type annotation workflow, real sequencing
pipeline checks, public sequencing downloads and biological validation were **NOT
RUN** in this task. They are not implied by the local model or offline test results.

## Guided review usability follow-up — 2026-10-23

The simplified GUI was tested against an isolated local registry with synthetic
metadata and the mock worker. Browser checks passed for table upload and preview,
identifier selection, species-first review, approval and automatic advancement,
correction through a selected source excerpt, information requests, skipping
without saving, and export containing only the two explicitly approved fields.
An unknown field remained unapprovable without a correction and evidence.
The missing-evidence action opened the source form. Mock proposal completion
exposed a View suggestions action; opening it showed the new revision and zero
retained approvals. This test does not measure real-provider accuracy.

The production TypeScript/Vite build passed. The required `scripts/check.sh` with
`GENESIS_REPO` set passed all commands listed above; binary bigWig subchecks, live
hosted API checks, full model lifecycle acceptance and real scientific pipelines
remained NOT RUN. The first check attempt could not resolve its build dependency
under network restrictions; the subsequent permitted run completed successfully.
The first frontend build used the wrong container UID; rebuilding as the workspace
owner succeeded. No failed attempt is counted as a pass.

Desktop (1440 px) and mobile (390 px) layout checks passed, including expanded
source evidence. An initially overflowing source identifier now wraps. These are
functional browser checks, not a measured curator usability study.

## AI-prepared reviewer inbox — 2026-10-23

The inbox replaces the field-by-field default with grouped tasks and persistent
To do / Waiting / Done lists. New offline checks verify atomic multi-field
approval and rollback, stale-version rejection, waiting and completed decisions,
backup/restore of inbox state, approved-only exports, per-record batch failures,
protection of newer human reviews, partial progress after interruption, cancellation,
and authenticated preparation/resolve endpoints. The full `scripts/check.sh` with
`GENESIS_REPO` set passed; the optional scientific, binary-reader and hosted-live
checks listed above remain NOT RUN.

Browser checks on an isolated synthetic registry and mock provider verified:

- Importing two records automatically prepared four grouped review tasks.
- AI preparation accepted zero fields without a human decision.
- Unchecking one suggestion and approving the other two accepted only those two.
- Moving nineteen missing fields to Waiting survived a page reload.
- Keeping those fields unknown moved their review to Done without exporting values.
- The mobile inbox at 390 px had no horizontal overflow.

The TypeScript/Vite production build passed. Initial development checks found an
unsupported audit operation and an inferred Python type error; both were fixed
before the passing full run. Grouped review now uses the existing per-field audit
operation inside one transaction. These tests establish workflow behavior, not
biological accuracy or measured curator time savings.
