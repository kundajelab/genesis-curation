# Guided review and plant reference audit

The default screen offers **Start review / Continue review** across studies. AI
prepares metadata; the queue prioritizes preparation failures and important identity
or assay decisions. One decision shows a suggested value, its evidence and the
number of affected samples. Accept, correct, keep unknown or defer; the next task
opens automatically. The study browser remains available for imports and sign-off.

Repeated evidence is collapsed. **Inspect all affected samples** expands the full
set. **Correct inline** retrieves original sample evidence on demand. The optional
**Fill matching samples from the first correction** copies a value only to samples
with an identical supporting passage. Review the affected set before saving.
Every sample still undergoes its own evidence and version checks.

## Evidence assessments

`plant-evidence/1` assessments accompany newly prepared records. They identify
processing software presented as chemistry, reference identifiers presented as
biological source, unsupported biological-replicate claims from technical lanes,
ambiguous TF-versus-DNA organism evidence, and invalid library-layout labels.
Conflicting organism values sharing a BioSample, or replicate values sharing an
experiment accession, are flagged without filling values from other samples.

The existing follow-up stage combines unresolved fields and interpretations needing
verification into token-budgeted requests. It remains a single follow-up round;
invalid replies have bounded recovery. Truncation increases the next output
reservation within the provider ceiling. A high confidence score does not waive
an evidence issue. Model verification is recorded as model output, not expert truth.
These rules do not comprehensively establish biological correctness.

A correction remains authoritative. Reusable examples require explicit promotion
or the existing remember checkbox and sign-off, with matching organism, tissue and
assay. The example records the corrected field; it is guidance rather than evidence
for another sample. Previously promoted examples without assay scope are not reused.

Existing studies are not automatically reprocessed. New assessments appear on an
explicit re-analysis, and existing task identities/drafts remain compatible until
then. Original field values, evidence and signed exports are retained.

## Twenty-sample reference audit

Open **Plant reference audit** and select the prepared reference set. It contains
20 samples selected from the ten cached plant pilot studies, spanning RNA-seq,
ATAC-seq, DAP-seq, ChIP-seq and methylation study categories. Mixed-assay studies
remain mixed. Each study contributes the first sample by accession and another
sample with the most unresolved evidence or a prior preparation failure.

For each card:

1. Read the source entry or open its original passages.
2. Confirm an explicit source value, enter another supported value with a citation,
   mark it undocumented/not applicable, or choose **Cannot determine**.
3. Save the judgment to reveal baseline and new-workflow model answers.
4. Continue to the next sample. Drafts are saved automatically; you can pause.

The 20–30 minute duration is a target, not a measured completion-time guarantee.
The audit covers assay-dependent core fields and one documented context field.
It does not establish accuracy across the full project metadata schema.

Reference answers live in separate audit tables and are never retrieved as model
examples or included in replay input. Source and candidate predictions are frozen
before review. Study-level development/evaluation partitions are retained; these
studies have been used previously, so this is not an unseen-data generalization
benchmark. An edited answer retains an immutable event history.

The report compares statuses and case-insensitive exact labels, with counts for
unsupported assertions, missing supported values and disagreements that had no
review task. **Cannot determine** judgments are excluded from judged-field counts.
Synonyms can appear as differences; inspect them before interpreting correctness.
Candidate metrics are also separated by development/evaluation partition. No
scientific accuracy improvement is claimed before expert judgments are available.

## APIs, storage and recovery

- `GET /api/review-queue` returns compact, paginated cross-study task summaries;
  `status=open|waiting` selects the queue.
- `GET /api/review-queue/{study}/{task}` retrieves a task with citation summaries;
  existing subject endpoints load full source text when correction is requested.
- `GET /api/audits`, `/api/audits/{id}`, and `/api/audits/{id}/report` expose audit
  progress and results. Sample endpoints provide evidence and reveal predictions
  only after a judgment is saved.
- Audit draft and answer POST endpoints use the existing authentication/CSRF
  boundary. Answers bind to frozen source hashes and optimistic version tokens.
- Registry schema 7 adds audit, answer and immutable audit-event tables. Automatic
  migration backups preserve the prior registry; no old study is re-signed.

`tests/prepare_reference_audit.py` freezes cached pilot metadata into a new file.
`tests/evaluate_reference_live.py` replays those frozen study sources in new
isolated registries, without reading audit answers. `tests/install_reference_audit.py`
loads baseline/candidate snapshots into audit tables after verifying identical
source hashes. None of these commands downloads sequencing files.

Rollback requires the preserved application image **and** the schema-6 data backup.
Stop app/worker, preserve the current data (including new audit answers), restore the
matching backup, then start the older image. Never run the old application against
a schema-7 registry or overwrite the only copy of post-deployment reviewer work.
