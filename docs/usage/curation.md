# Validate, catalog and review published datasets

Use these commands to check output files, find datasets in a local catalog and
record metadata or QC reviews. Start with a completed DAP run. If you
want to manage inputs and processing as well, follow the [study workflow](studies.md),
which collects results into the same catalog automatically. No AI service is needed.

Install genesis-curation and put `~/.local/bin` on PATH
(see [installation](../../README.md#install)). Use
`genesis` from any directory, or the command in the Python environment containing
an installed wheel.
These command families support `--help` and `--json`. Successful commands emit
JSON by default; search also supports `--format tsv`. In the examples, replace
`DATASET_ID` with an ID returned by `registry search` or `study init`. A sequencing
accession is not necessarily the catalog ID.

For registered studies, add `--scope inputs` to metadata/review commands before
processing. Use the default `--scope results` to review processing outputs.
Only metadata can be approved in the input scope; a matching collected result can
carry that exact input approval forward with its original evidence. QC and export
eligibility always require result-specific decisions. See the
[study guide](studies.md#2-review-input-metadata-when-needed) for the full recipe.

## Try the workflow with synthetic data

From the repository root, run the complete offline tutorial in a new directory:

```bash
uv run --frozen python examples/curation/offline_workflow.py /tmp/genesis-curation-demo
```

It validates synthetic files, imports them, records demonstration reviews and exports
a catalog. Expected output: `status: PASS`, two included datasets and six review
decisions. Inspect its files to learn the formats before working with real studies.
The tutorial uses a mock provider and requires no account.

## Understand the dataset status

| State | Owner | Meaning |
| --- | --- | --- |
| Execution | Campaign `state.sqlite` | What happened to a processing attempt |
| Structure | Versioned validation | Which declared files and evidence were checked |
| Scientific QC | Versioned assessment | Measurements interpreted under an explicit policy |
| Metadata review | Immutable decisions | A person's review of exact metadata/source versions |
| Export eligibility | Decision and export profile | Acceptance for a particular catalog purpose |

`SUCCEEDED` does not mean accepted. Zero peaks can be structurally valid.
A reviewer cannot waive structural errors. `resolve` authorizes an
execution retry; it does not approve scientific quality or curation.

## Validate existing inputs and published runs

Validate the sample sheet and reference before starting a run:

```bash
genesis validate inputs dap.tsv --assay DAP-seq \
  --references /data/references --json
```

For published runs, use a stable source namespace and the actual worker identity.
Reusing that namespace and library name identifies a new revision of the same
dataset. Different studies with overlapping sample names need different namespaces.
Internal dataset IDs are separate from accessions, library names, lanes and jobs.

```bash
# Execute on the machine that holds these artifacts.
genesis validate run /data/dap-run --assay DAP-seq \
  --source-id study-B --study study-B --worker local \
  --sheet /data/dap.tsv --references /data/references \
  --output dap-inspection-v1.json --json

# Explicit full inspection with a separately installed bigWig reader.
genesis validate run /data/dap-run --assay DAP-seq \
  --source-id study-B --study study-B --worker local --level full \
  --sheet /data/dap.tsv --references /data/references \
  --bigwig-python /path/to/track-environment/bin/python \
  --max-bytes 10737418240 --max-records 10000000 --max-seconds 1800 \
  --output dap-validation-v1.json --json
```

The DAP run directory is the directory containing `output/`, not the pipeline
workspace above it. The adapter preserves shared-control assignments, raw source
snapshots and available QC definitions. DAP controls have no required treatment peak
outputs.

DAP runs record their image pins and Git status in `output/dap-images.lock` and
`output/git-status.txt` (see [DAP run records](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/dapseq-reference.md#run-records)).
Import treats a run's provenance as complete only for a clean checkout run with a
container engine and digest-pinned images, and keeps the engine and image pins in
each manifest. Full inspection checks the declared artifact digests and reference
bytes.

Historical DAP MultiQC provenance lacks complete input/resource/container evidence.
These records are incomplete even when all surviving files are readable. The default
reviewed export excludes them. An explicitly authored export profile
can allow incomplete evidence for an appropriate catalog purpose; it still cannot
allow a known structural error. Such an export is not full model-readiness evidence.

Metadata inspection checks declarations and local availability; it is not a full
validation pass. Full inspection hashes bytes and scans supported formats. Defaults
are 1 GiB of scanned bytes, one million records and 300 seconds per bundle. Scanned
bytes include rereads and decompression; they are not network bytes. Zero disables
the byte or record limit, while the time limit must remain finite and positive.
An exhausted budget or unavailable reader yields `NOT_EVALUATED`, not a pass.
Documents/snapshots are limited to 32 MiB; split larger imports into bundles.

Checks cover reference contigs/lengths and declared BED0 TSS points; BAM headers
and declared coordinate sorting/index availability; BED/narrowPeak coordinates,
ordering and finite values; bigWig readability, contigs, finite signals and declared
count/normalization semantics; source-backed metrics and required published outputs.
No TSS annotation, cell barcode or plant threshold is synthesized. BAMs are checked
when declared, not required as new published outputs. Optional pruned intermediates
remain `intentionally_pruned`, distinct from unavailable required artifacts.

bigWig parsing uses `pyBigWig` in the explicitly selected track interpreter,
via a bounded local subprocess. Keep that binary dependency outside core Python
3.14; the existing track environment is pinned separately. This command does not
install it. Selecting another worker name does not connect to that worker: run
the validator there and transfer only the resulting JSON through approved means.
`/data/a.bw` on two workers is two locations even if both have identical digests.

For authored records or repeat scans:

```bash
genesis validate manifest bundle.json --json
genesis validate manifest bundle.json --scan --level full --worker worker-A \
  --bigwig-python /path/to/track-environment/bin/python --output recheck-v2.json --json
```

Without `--scan`, this validates only the JSON contract and content digests.
Record schemas ship in `src/genesis_curation/contracts/schemas/records-v1.json` and
`records-v2.json`; `records-v3.json` adds prepared inputs, studies, plans and collection receipts.
`contracts.records` provides typed manifest, finding, measurement, proposal,
assessment and decision records. Unknown versions, extra contract fields, malformed
types, duplicate JSON keys, NaN and stale nested hashes are rejected. Flexible
descriptive metadata and legacy lane dictionaries retain their source fields.
Use `record()`/typed `.export()` to seal authored records; never hand-edit a hash.

Run adapters declare `metadata.output_contract = genesis-published-v1`, which checks
the existing assay's required output roles. Authored generic manifests describe
their own artifact set; full validation of that set is not a claim that a complete
assay or every possible model input has been provided. Export profiles can require
specific roles in addition to review and full structural validation.

Exit codes: **0** for valid requests without found errors (including metadata-only
inspection), **1** for structural findings with errors, **2** for invalid requests,
and **3** for an incomplete requested full scan. Inspect findings and `complete`;
never interpret metadata-level exit 0 as scientific acceptance. Output bundles are
immutable: use a new output filename for a new observation.

## Import, search and inspect provenance

Keep the scientific registry on the controller's local disk. Do not place SQLite
on a shared worker/NFS filesystem. Workers export bundles, not database writes.
Every write uses a local writer lock and transaction; readers use SQLite snapshots.
Sources, findings, decisions and prior manifests are immutable. Current dataset
projections change only through explicit import, assessment or review operations.

```bash
genesis registry init /local/catalog --json
genesis registry import /local/catalog --bundle dap-validation-v1.json --json
genesis registry import /local/catalog --campaign /local/campaign --json
genesis registry search /local/catalog --filter assay=DAP-seq \
  --filter availability=available --limit 100 --json
genesis registry search /local/catalog --text 'leaf' --format tsv
genesis registry show /local/catalog DATASET_ID --json
genesis registry history /local/catalog DATASET_ID --limit 100 --after 0 --json
```

Structured filters are `species`, `assay`, `study`, `reference_id`, `worker`,
`availability` and `biological_context`. QC/review filters use `--qc-status` or
`--review-status` with `--category metadata|qc|eligibility`. Text search is literal
FTS5 when available, with escaped substring fallback. No SQL supplied by a user
or model is executed. Search and review queues return `next_after`; status filters
apply within each scanned page, so an empty result page can still have a cursor.
Registry history uses an offset; review history returns decision sequence cursors.

Repeat imports of an identical bundle are idempotent and preserve approvals.
A new manifest revision supersedes the current projection and invalidates affected
review. Importing known historical content cannot silently roll the catalog back.
Campaign imports preserve observed jobs/events and worker configuration without
polling or changing execution. They do not reconstruct unavailable past attempts.
The returned `campaign_id` is the full SHA256 of canonical campaign JSON, distinct
from the controller's shortened worker-attempt directory prefix. Supply
`--campaign-id ID --job-id JOB --attempt N` together to `validate run` to link records.

The registry models entities/relations, worker-qualified locations, exact reference
versions in manifests, immutable scientific records and attempt observations.
Invocation/deployment provenance contracts can be imported with `--record`.
The optional [provider](ai-providers.md) and [model-service](llm-deployment.md) commands
write richer version 2 provenance through the same registry. Sequencing files stay
outside SQLite. Catalog facts describe observations, not continuous filesystem
monitoring: revalidate before consuming artifacts that might have changed.

## Assess QC and review one dataset at a time

```bash
genesis qc evaluate /local/catalog DATASET_ID --json
genesis review queue /local/catalog --category metadata --json
genesis review show /local/catalog DATASET_ID --json
```

The packaged `plant-diagnostic-v1` policy leaves thresholds **UNSPECIFIED**. It
reports `NOT_EVALUATED` or `NOT_APPLICABLE`, rather than converting absent or
incompatible metrics into zero or a pass. A policy records applicability,
metric definition/version, units, denominator, depth, threshold, missing behavior,
severity, rationale, author and evidence. Custom policies use `--policy FILE`;
only fixed declarative comparison operators are supported. Reassessment stores a
new version when evidence or policy changes and never reruns sequencing.

Approve only after inspecting evidence. Put selected references in an evidence
file using their exact recorded location, worker and digest:

```json
{"evidence":[{"location":"/data/dap-run/output/multiqc/multiqc_data/genesis_flagstat.json",
  "worker":"local","sha256":"REPLACE_WITH_RECORDED_SHA256",
  "locator":"/TF1.qc.report.flagstat.txt/mapped_passed"}]}
```

Locators are JSON pointers into stored sources, `line:N` with one-based line
numbers, or `$` for a whole source/artifact. Fabricated locations, hashes and
unresolvable source locators are rejected. Copy the current `status.token` from
`review show`; obtain a fresh token after each decision or reassessment.

```bash
genesis review approve /local/catalog DATASET_ID --category metadata \
  --token CURRENT_TOKEN --reason 'Reviewed the named source evidence' --evidence evidence.json
genesis review approve /local/catalog DATASET_ID --category qc \
  --token NEW_TOKEN --reason 'Document the actual QC judgment or exception' --evidence evidence.json
genesis review approve /local/catalog DATASET_ID --category eligibility \
  --token NEWEST_TOKEN --reason 'Reviewed for this catalog purpose' --evidence evidence.json
genesis review history /local/catalog DATASET_ID --json
```

`reject` and `request-info` use the same category/token/reason interface. QC approval
requires a current assessment; a scientific concern can have a documented human
exception. Structural errors always exclude the dataset. Review records the OS
UID, login name, hostname, timestamp, reason, evidence and exact version bindings.
Shared OS accounts cannot distinguish individual humans; use distinct project
accounts when that accountability is required. Hashes detect stale content, not
malicious database administrators. Protect registry files using existing permissions.

Manual descriptive edits use a proposal, never an executed sample-sheet rewrite:

```python
from pathlib import Path
from genesis_curation.contracts.records import MetadataProposal, dump, load

proposal = MetadataProposal(
    dataset_id="DATASET_ID", manifest_version="CURRENT_MANIFEST_SHA256",
    changes={"tissue": "leaf"}, evidence=load(Path("evidence.json"))["evidence"],
    reason="State what the cited source establishes",
).export()
dump(Path("proposal.json"), proposal, immutable=True)
```

```bash
genesis review propose /local/catalog --file proposal.json
genesis review diff /local/catalog DATASET_ID --target PROPOSAL_VERSION
genesis review approve /local/catalog DATASET_ID --category metadata \
  --target PROPOSAL_VERSION --token CURRENT_TOKEN --reason 'Reviewed this diff' \
  --evidence evidence.json
```

Approval creates the canonical metadata revision and makes affected QC/eligibility
stale. Reference, control, MAPQ and other scientific-input edits are not accepted
as descriptive proposals. Use an explicit new scientific run for such changes.

## Export an explicit selection

```bash
genesis registry search /local/catalog --filter study=study-A \
  --limit 100 --selection-output selection.json --json
genesis registry export /local/catalog --selection selection.json \
  --reviewed --output reviewed-catalog-v1.json --json
```

Inspect the selection before exporting. It contains exact dataset/manifest
versions and registry identity; it is one search page, not an implicit all-record
selection. For larger selections, collect every intended page and use the
`selection` contract to seal the explicit combined candidates. There is no
approve-all command. Omitting `--reviewed` produces an ordinary, ungated bundle.

The default reviewed profile requires full validation, metadata/QC/eligibility
approval and at least one available artifact with a digest. A custom profile
can add required roles and is supplied with `--profile` both at eligibility
approval and export. A different profile invalidates that eligibility approval.

Reviewed exports are **manifest catalogs**. They include candidates, exclusions,
missing-evidence reasons, QC exceptions, exact candidate/included/excluded counts,
canonical metadata, relevant proposals/assessments/policies, decisions and the
original scientific manifest bundle. They do not copy sequencing files or produce
training arrays. A catalog with zero eligible candidates still succeeds and
reports those exclusions. Importing it imports its scientific bundle; it does
not replay another registry's approvals. Use backup/restore for complete history.

## Read-only status, backup and rollback

```bash
genesis serve /local/campaign --registry /local/catalog --port 8765
genesis registry backup /local/catalog --output catalog-backup.sqlite --json
genesis registry restore /local/restored-catalog --backup catalog-backup.sqlite --json
```

The status page binds to loopback and has no write endpoints. It escapes
untrusted names and shows campaign-linked curation summaries from a bounded
catalog page (first 1,000 records). Use CLI pagination for complete catalogs.
Approved report links download digest-verified HTML as attachments, not active
HTML on the status origin. Only registered `local` reports under the run's
`output/` root are served, with a 32 MiB limit. Remote reports are never fetched.

`registry init` migrates the local database transactionally and creates a
pre-migration backup for an existing older schema. Backups use SQLite's consistent
backup API, including committed WAL content. Restore requires a new destination
and verifies database/FK integrity and supported schema. To roll back, stop catalog
writes, restore the pre-migration backup into a new directory using the appropriate
software revision, verify it, then point commands at that directory. Do not copy a
live `.sqlite` file alone or delete campaign state. Disabling curation simply means
omitting these commands and `serve --registry`; scientific execution is unaffected.

See the [implementation brief](../curation/IMPLEMENTATION_BRIEF.md) and
[validation report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/curation-foundations-validation.md)
for scope, exact checks and unrun live scenarios.

## Evidence-backed metadata harmonization

Ingest local UTF-8 JSON objects/arrays, CSV, TSV or text excerpts against an existing
dataset. Input files are limited to 2 MiB each and new bundles to 16 MiB. Accession
retrieval is bounded and restricted to fixed ENA and NCBI GEO APIs; arbitrary URLs,
redirects, executable supplements and automatic paper downloads are not accepted.
Sources retain exact text, location, digest and retrieval time. Inaccessible papers
supply no evidence. Re-ingestion preserves earlier snapshots and the most restrictive
source classification; conflicting sources stay visible.

```bash
genesis metadata ingest /local/catalog DATASET_ID \
  --source source-metadata.json --source paper-excerpt.txt --classification local-only
# Optional public metadata retrieval, explicitly involving network access:
genesis metadata ingest /local/catalog DATASET_ID \
  --accession SRR123456 --classification public

# Deterministic extraction, no AI required:
genesis metadata propose /local/catalog DATASET_ID --output proposal-v2.json
# Or explicitly select a configured provider:
genesis metadata propose /local/catalog DATASET_ID \
  --config /local/providers.json --provider private --output model-proposal.json
genesis metadata diff /local/catalog DATASET_ID --target PROPOSAL_VERSION
```

Fields include organism/taxon, cultivar/genotype, tissue, developmental stage,
treatment, assay/protocol, layout/chemistry, biological replicate, technical lane,
accession and reference/control proposals. DAP TF identity/source organism is
separate from assayed DNA organism/genotype and native/amplified DNA context.
No tissue-specific TF activity is inferred. `ATAC-seq`, `GRO-seq`, `GRO-cap` and
vague 5-prime terms alone do not establish the necessary library/protocol distinctions.
Missing treatment is `unknown`, never automatically `untreated`.

Deterministic structured fields take precedence; models cannot overwrite a known
value or hide a source conflict. Each candidate includes its original value,
proposed value, `known|unknown|not_applicable|conflicting` status, exact source
version/JSON-pointer or line locator/quoted span, short rationale and vocabulary
version. The small packaged plant lookup preserves unrecognized author terms with
no ontology ID; invented IDs are rejected. Self-reported confidence is separate
from empirical accuracy (no calibration is claimed).

Every proposed value needs a quote found at its locator in the saved source.
This verifies evidence existence, not the biological interpretation. Contradictory
structured sources yield a null conflicting candidate. For curator resolution,
copy the proposal's `data.fields` to `{"fields":[...]}`, edit the value/status,
rationale and exact evidence, then use `metadata propose --fields curated-fields.json`.
Use supported vocabulary IDs or null, never fabricate an identifier.

Approve the exact proposal with a fresh review token and its recorded evidence.
For version 2 proposals, approval and apply are deliberately separate:

```bash
# Save {"evidence": proposal.data.evidence} as evidence.json after inspecting it.
genesis review show /local/catalog DATASET_ID
genesis review approve /local/catalog DATASET_ID --category metadata \
  --target PROPOSAL_VERSION --token CURRENT_TOKEN --reason 'Explain the evidence review' \
  --evidence evidence.json
genesis metadata apply /local/catalog DATASET_ID --target PROPOSAL_VERSION \
  --output canonical-metadata-v1.json
```

Approval is `PENDING_APPLY` until apply succeeds; reviewed exports exclude it.
Apply creates an immutable `metadata_revision`, updates the selected scope's canonical
metadata and emits a `compiled_inputs` description referencing the exact original
assay, lanes and reference. For registered input revisions, `genesis study plan`
compiles reviewed metadata and the recorded scientific choices into an executable
campaign. Apply itself does not launch work. Scientific inputs are copied
verbatim; reference/control proposals remain descriptive. Existing executed sheets,
campaigns and raw accession records are never rewritten. Reassess QC and review
eligibility after metadata changes. Source/manifest changes or concurrent reviews
invalidate stale proposals/approvals. Repeating an unchanged apply is idempotent.

For version 1 manual proposals, `review approve` applies their limited descriptive
changes directly. Version 1 hashes and history are readable. Rich proposals use
version 2 and require explicit apply.
The registry migrates through schema 4; stored source bundles/revisions/invocations
are included with reviewed catalog provenance. Shared-account identity limitations
and explicit individual review still apply.

## Failure diagnosis

Collect a small explicit selection of logs from an approved run/work root:

```bash
genesis ai diagnose /local/catalog --root /scratch/dap-run \
  --log .nextflow.log --log work/ab/task/.command.err \
  --facts diagnostic-facts.json --dataset DATASET_ID --output diagnosis.json
# Optional explanation, with the same deterministic classifier:
genesis ai diagnose /local/catalog --root /scratch/dap-run \
  --log .nextflow.log --config /local/providers.json --provider private
```

`--facts` is optional JSON containing observed `worker_state`, `exit_code`,
`oom_kill`, `resource_evidence`, `qc_concern`, `tool_exception`, and campaign/job/attempt
identifiers where available. Unknown facts should be omitted. For example,
`{"worker_state":"FAILED","exit_code":137}` remains unknown: 137 alone does not
prove OOM. Confirmed OOM requires a collected resource-evidence hash and an OOM
signature. State `RUNNING`, `STARTING` or `UNKNOWN` always prevents classification
as a safely retryable terminal failure.

Remote evidence uses `--worker-config worker.json --worker-id worker-A`, the existing
local/SSH worker fields and optional `diagnostic_roots`. The selected root must be
inside the node's approved roots. No recursive traversal/upload occurs. At most
16 explicitly named logs, each with at most its final 64 KiB, are retained; hashes
refer to retained redacted snapshots, not uncollected full files. Oversized files
are marked partial. Credentials and common secret patterns are redacted.

Categories cover temporary network/exit-75 errors, access, missing inputs, checksum
mismatch, malformed manifests, measured resource exhaustion, reference mismatch,
tool defects, biological QC concerns and unknown causes. Optional model explanations
and hypotheses are separate from deterministic category/facts. A model outage does
not prevent deterministic diagnosis, worker polling or ordinary processing.

Results propose a typed action (`inspect`, `renew-auth-manually`,
`reacquire-and-verify`, `retry-same-config`, `propose-resource-change`,
`propose-metadata-correction`, `escalate`), evidence, uncertainty, preconditions and
expected effect. **Diagnosis never executes that action.** It cannot change MAPQ,
duplicates, adapters, references, peak thresholds, controls, QC approval or credentials.
Recollect evidence and compare `target_version` before an action. Automatic retries
follow the controller's bounded exit-75 policy; the controller blocks retries while
an attempt is UNKNOWN.
Changed resources/inputs need an explicitly versioned replacement campaign linked
to the original, not an edited campaign JSON or an assumption that `resolve` applies it.

Run `uv run --frozen python tests/evaluate_metadata.py`
for the separate synthetic metadata evaluation. The fixture split and reported field,
evidence/conflict/abstention counts test software; they do not establish real curation
accuracy. Human correction rates and live-provider comparisons have not been measured.
