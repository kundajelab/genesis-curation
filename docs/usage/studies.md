# Process a prepared study from inputs to reviewed results

Use `genesis study` when you want to track a study from its prepared sample sheet
through processing, review and export. The commands save the input version, run
jobs on your chosen workers and collect results into a catalog. AI is optional.
For a single analysis without a catalog, use `pixi run pipeline` (DAP-seq) or
`genesis pipeline-atac` instead.

Before starting, prepare the sample sheet and choose the reference, controls and
analysis settings. See the [DAP](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/dapseq-reference.md) and
[bulk ATAC](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/bulk-atac.md) guides for input formats and output definitions.

| Command | Purpose |
| --- | --- |
| `study init` | Validate and register a versioned sheet/reference snapshot, with one input record per library |
| `study plan` | Compile exact, checksummed worker documents and a campaign without launching jobs |
| `study run` | Recheck the plan, launch its campaign and collect successful results |
| `study resume` | Reconnect, poll existing attempts, launch eligible queued jobs and continue collection |
| `study status` | Show execution, collection, structure, QC, input/result review and next commands independently |
| `study collect` | Collect or revalidate successful attempts without launching scientific analysis |
| `study export` | Export an explicitly selected reviewed manifest catalog, including exclusions and decision evidence |

## 1. Register prepared inputs

Complete the [command installation](../../README.md#install-and-check-your-environment)
with `pixi run install-all --install-cli` and put `~/.local/bin` on PATH. Keep study state and its SQLite registry on
controller-local storage outside the checkout. Workers need a clean checkout of
the selected commit and their installed environment. Use absolute input paths.

```bash
# Bulk ATAC: local files or supported public HTTPS FASTQs; explicit reference registry.
genesis study init /local/studies/atac-A --study-id atac-A --assay bulk-ATAC \
  --sheet /data/atac-A.tsv --references /data/references.json

# DAP: existing public FASTQ URL sheet; directory containing exact named FASTA files.
genesis study init /local/studies/dap-B --study-id dap-B --assay DAP-seq \
  --sheet /data/dap-B.tsv --references /data/dap-references
genesis study status /local/studies/atac-A
```

The default registry is `STUDY_DIRECTORY/registry`; `init --registry /local/catalog`
can select an existing/shared **controller-local** catalog. `init` prints dataset IDs
and input versions. Record those IDs; an accession, a library and a job are different
identities. A study's source namespace is `STUDY_ID:ASSAY`, so use a different study ID
for unrelated sheets with overlapping library names. Execution initially reports `NOT_RUN`; processing and review provide the result
and approval records later.

Registration verifies current sample/reference requirements and snapshots source
evidence and digests. At most 10,000 rows are accepted per study; stored documents
also have size limits. Repeating unchanged input registration is idempotent. Changed
bytes produce new input versions, preserve prior history and invalidate affected
approvals. Existing studies cannot silently switch ID, assay or registry.

The default source classification is `local-only`. Set `--classification public`
only for public sources, or `internal` as appropriate. Retained restrictions apply
across input and result review and cannot be downgraded by re-ingestion. A private
provider can be configured separately; no provider is selected by study commands.

## 2. Review input metadata when needed

The original prepared sheet can run without a metadata approval. Descriptive edits
must have a current input approval before they enter a plan. Input review uses
`--scope inputs`; the default scope, `results`, applies to processing outputs.
Only metadata review is applicable before processing. QC and export eligibility
require actual result evidence.

```bash
genesis review queue /local/studies/atac-A/registry --scope inputs
genesis review show /local/studies/atac-A/registry DATASET_ID --scope inputs
genesis metadata ingest /local/studies/atac-A/registry DATASET_ID \
  --scope inputs --source /data/curator-source.json --classification local-only
genesis metadata propose /local/studies/atac-A/registry DATASET_ID \
  --scope inputs --output /local/proposal-v1.json
genesis metadata diff /local/studies/atac-A/registry DATASET_ID \
  --scope inputs --target PROPOSAL_VERSION
```

The default proposal uses deterministic extraction from library-specific evidence.
Put actual source-backed values, such as tissue/treatment, in the source file; missing
treatment is unknown. To use a configured model, add `--config /local/providers.json
--provider private` to `metadata propose`. The same evidence validation and review
apply. See [metadata harmonization](curation.md#evidence-backed-metadata-harmonization)
for conflicts, manual fields, DAP TF/DNA distinctions and supported vocabularies.

Inspect the saved proposal's `data.fields`, quotes and `data.evidence`. Save the
selected evidence as `{"evidence": [...]}` in `/local/input-evidence.json`. Copy
the proposal's top-level `version` and a **fresh** `status.token` from `review show`:

```bash
genesis review approve /local/studies/atac-A/registry DATASET_ID \
  --scope inputs --category metadata --target PROPOSAL_VERSION --token CURRENT_TOKEN \
  --reason 'State the actual source review and judgment' --evidence /local/input-evidence.json
genesis metadata apply /local/studies/atac-A/registry DATASET_ID \
  --scope inputs --target PROPOSAL_VERSION --output /local/canonical-input-v1.json
genesis review history /local/studies/atac-A/registry DATASET_ID --scope inputs
```

Approval of a rich proposal is `PENDING_APPLY` until apply succeeds. Applying creates
an immutable canonical revision; it never rewrites an executed sample sheet. Existing
reference, control, MAPQ, duplicate and adapter choices are not descriptive metadata
edits. To change scientific choices, explicitly prepare a new sheet/reference revision.

## 3. Configure workers and inspect a plan

Copy [the local execution template](../../examples/curation/study-execution.local.json)
outside the checkout and edit it. Obtain the full pinned revision with `git rev-parse
HEAD`. Replace **every example path and the SHA placeholder** before planning.

```bash
cp examples/curation/study-execution.local.json /local/study-execution.json
git rev-parse HEAD
command -v pixi
```

| Configuration | Meaning |
| --- | --- |
| `git_sha` | Full 40-character commit required on every worker; dirty/mismatched checkouts fail before analysis |
| `transport`, `host` | `local`, or `ssh` with an existing authenticated host alias |
| `repo`, `python`, `pixi` | Absolute worker paths to checkout, installed Genesis interpreter and Pixi executable |
| `root` | Worker-local owned attempt/supervisor and generated-document directory |
| `run_root` | Worker-local scientific outputs/work directories, outside the checkout |
| `slots` | Concurrent analysis-job count; this is not a RAM/GPU reservation |
| `profile` | Explicit existing Nextflow profile, such as `local,docker` or `slurm,apptainer` |
| `config` | Optional controller-side site configuration, copied as a bounded checksummed document; paths it references must exist on the worker |
| `path_map` | Controller input-path prefixes mapped to already staged worker paths; longest matching prefix wins |
| `bigwig_python` | Optional existing worker interpreter with `pyBigWig`; separate from core Python 3.14 |
| `assignments` | Library name to worker ID; required for every library/control when multiple workers exist |

For SSH, extend `workers` with the same fields and, for example:

```json
{
  "transport": "ssh",
  "host": "existing-worker-alias",
  "path_map": {"/data": "/worker/project/data"}
}
```

This is a fragment, not a complete execution configuration. Provide explicit mappings
for all local FASTQ/reference/TSS paths on SSH workers, even if the physical path is
identical. Large files must already be staged using approved transfer tools; Genesis
does not assume a shared worker filesystem or copy credentials. Public input URLs
retain the existing pipeline's download behavior. Planning does not contact workers.

Each ATAC library compiles to one job containing all its technical lanes. DAP
treatments sharing a control compile to one job with that control, while each library
retains a distinct catalog identity. Assign the whole DAP group to one worker.
No reference selection, control pairing, threshold or lane merging policy is inferred.

```bash
genesis study plan /local/studies/atac-A \
  --execution /local/study-execution.json --output /local/atac-plan-v1.json
genesis study plan /local/studies/dap-B \
  --execution /local/study-execution.json --output /local/dap-plan-v1.json
```

Inspect `data.status`, `data.blockers`, `data.campaign.jobs` and `data.jobs` before
running. The plan includes exact argv, mapped paths, input hashes, input/review versions,
generated sample/reference/metadata documents, output ownership and scan budget.
`BLOCKED` never launches. Identical inputs/configuration/reviews produce the same plan;
use a new output filename after a change. Planning hashes local assets; worker startup
checks their bytes again. Controller polling checks small documents and review versions,
so it does not reread large FASTQs every two seconds.

## 4. Execute and reconnect

```bash
genesis study run /local/studies/atac-A --plan /local/atac-plan-v1.json
genesis study status /local/studies/atac-A
genesis study resume /local/studies/atac-A

# DAP uses exactly the same orchestration interface.
genesis study run /local/studies/dap-B --plan /local/dap-plan-v1.json
```

`run` and `resume` poll until no confirmed active work remains, emitting JSON progress
lines every two seconds and a final JSON result. Use `--once` for one bounded controller
tick and a single result. Durable worker supervisors survive a controller disconnect;
resume reconnects to the same attempt. Keep the controller running to launch subsequent
queued work and collect completed jobs. Healthy groups continue when another fails.

`status` includes dataset ID/name, job, input/result versions, input metadata review,
execution, collection, structural validation, scientific QC, result reviews, and
`next_command`. `campaign_directory` locates the existing execution state. Use existing
`genesis issues`, `resolve` and `reconcile` there according to the
[execution recovery guide](execution.md). An `UNKNOWN` attempt is not evidence that it
is safe to retry. `resolve` authorizes a scientific retry; it never approves quality.

Source or review changes block new queued submissions from a stale plan. Existing
attempts are still polled and their results retained. A late receipt tied to older
inputs/evidence is historical only: inspect `collection_detail.result.historical`.
It cannot replace the current reviewed dataset. Re-run `init` and `plan` for deliberate
input changes; an earlier active/uncertain campaign must be resolved before switching
to another plan. Do not edit `campaign.json` or generated worker documents in place.

## 5. Collect, validate and assess results

Successful attempts are collected automatically by `run`/`resume`. A separate owned
worker supervisor reads the actual output contract, performs a bounded scan and returns
a checksummed receipt through the existing transport. The controller imports manifests,
findings, diagnostic QC and the receipt in one local transaction. Sequencing files stay
on their workers. Interrupted transfer/import is recoverable and repeated collection
is idempotent. A collection failure never resubmits scientific analysis.

Metadata-level inspection is the default. **It does not establish full structural
validation or default export eligibility.** For full inspection, set `--level full`
and appropriate budgets during planning, or revalidate after processing:

```bash
genesis study collect /local/studies/atac-A --level full \
  --reader local=/path/to/track-environment/bin/python \
  --max-bytes 10737418240 --max-records 10000000 --max-seconds 1800
genesis study status /local/studies/atac-A

# Poll/reconnect an interrupted collection without creating a new scientific attempt.
genesis study collect /local/studies/atac-A --once
# After investigating a confirmed collection failure, start a new collection generation.
genesis study collect /local/studies/atac-A --retry
```

`--reader WORKER=/absolute/python` can be repeated for multiple workers. It chooses
an already installed interpreter on that worker; it does not install packages or run
a model. Reader/budget changes create a new collection generation without changing
the scientific plan. Unspecified workers retain their configured reader. All study
scans require positive byte/record limits and at most 3,600 seconds. Defaults are
1 GiB, 1,000,000 records and 300 seconds; budget exhaustion stays `NOT_EVALUATED`.
Collection retries are explicitly bounded to 100 generations per plan.

The packaged plant policy deliberately leaves biological thresholds `UNSPECIFIED`.
Automatic assessment records evidence and missing values; it cannot approve a dataset.
Measurements, policy assessment and human exceptions remain separate. Reassess with
`genesis qc evaluate --policy FILE` when an approved policy is available.

DAP runs with [complete run records](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/dapseq-reference.md#run-records), from a clean
checkout in digest-pinned containers, can pass full structural validation and the
default reviewed export. Legacy or incomplete
records remain excluded. DAP shared-control groups retain the exact six-column public
sheet order when compiled for a worker. Each collection generation records its request
and observations; restoring artifact bytes and revalidating creates new evidence without
replacing history or launching another scientific attempt.

## 6. Review and export a selected catalog

```bash
genesis registry search /local/studies/atac-A/registry --filter study=atac-A
genesis review show /local/studies/atac-A/registry DATASET_ID
genesis review history /local/studies/atac-A/registry DATASET_ID --scope inputs
```

An approved input metadata revision can satisfy result metadata review only when the
collected result uses that exact input version and matches the reviewed canonical
fields. The export includes the real input decision, source bundle and canonical
revision in `input_curation`; no result approval is fabricated. If no input review
exists, review result metadata explicitly. In every case QC and eligibility still
need their own current decisions.

Follow the [individual review recipe](curation.md#assess-qc-and-review-one-dataset-at-a-time):
inspect recorded metrics/findings/sources, create `{"evidence": [...]}` with their exact
worker/location/hash/locator, and fetch a fresh `status.token` before **each** decision.

```bash
# Skip this decision only if metadata is already APPROVED through matching input review.
genesis review approve /local/studies/atac-A/registry DATASET_ID --category metadata \
  --token CURRENT_TOKEN --reason 'State the actual metadata review' --evidence /local/evidence.json
genesis review show /local/studies/atac-A/registry DATASET_ID
genesis review approve /local/studies/atac-A/registry DATASET_ID --category qc \
  --token FRESH_TOKEN --reason 'State the actual QC judgment or exception' --evidence /local/evidence.json
genesis review show /local/studies/atac-A/registry DATASET_ID
genesis review approve /local/studies/atac-A/registry DATASET_ID --category eligibility \
  --token NEWEST_TOKEN --reason 'State the reviewed catalog purpose' --evidence /local/evidence.json
genesis study export /local/studies/atac-A --dataset DATASET_ID \
  --output /local/reviewed-atac-v1.json
```

Select multiple explicit IDs with repeated `--dataset`, or save a registry search's
`--selection-output` and use `--selection FILE`. At most 1,000 distinct candidates are
accepted; all must belong to this study revision. Inspect selections before export.
Custom profiles must be identical at eligibility approval and export (`--profile`).
Exports are immutable manifest catalogs with included/excluded counts, exact reasons,
QC exceptions and decision evidence. They do not copy artifacts or create training
arrays. An all-excluded catalog is still a valid export: inspect its denominators.

## State, monitoring and recovery

The study directory holds `study.json`, immutable revisions/plans, active-plan and
collection progress, receipts and campaign directories. Each campaign owns its
existing execution `state.sqlite`; the scientific registry owns input/result versions,
review, QC and provenance. Workers never write controller SQLite files over NFS.

```bash
# Use campaign_directory reported by study status, not the outer study directory.
genesis serve /local/studies/atac-A/campaigns/CAMPAIGN_HASH \
  --registry /local/studies/atac-A/registry --port 8765
genesis registry backup /local/studies/atac-A/registry --output /local/catalog-backup.sqlite
genesis registry restore /local/restored-catalog --backup /local/catalog-backup.sqlite
```

The loopback status page shows read-only study summaries. Make changes through the
CLI. Report downloads serve local, digest-checked files as attachments.
Back up the catalog consistently and, with orchestration stopped, preserve study state
and worker attempt/output directories separately. A Git clone transfers none of those
live files. Relocating a study or running jobs across servers is not an automatic path
rewrite; coordinate actual storage and authentication through the existing site tools.

Registry schema 4 stores input heads, a separate input decision ledger and collection
lineage through transactional migration with a pre-migration backup. `registry init`
migrates existing catalogs; `study init` does this for its selected registry. Old software
cannot write schema 4. To roll back, stop writers and restore the saved older-schema
backup using the corresponding software revision into a new directory. Preserve the
current database and immutable worker outputs for recovery.

Command exit codes are **0** for a completed request (including pending `--once` work,
incomplete validation, or an all-excluded export), **2** for malformed requests, and
**3** for blocked plans, unresolved execution/collection failures or structural errors
observed by run/resume/collect. `status` itself is read-only and returns 0 when readable.
Inspect the separate states; exit 0 is not biological acceptance.

[Worker validation](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/study-workflow-validation.md) covers real
local worker supervision with explicitly synthetic output writers, real binary artifact
readers and reviewed exports. Use the acceptance recipe below to test scientific tools.
These local tests do not establish SSH/scheduler access or biological accuracy.

## Real-tool acceptance on synthetic inputs

Use this opt-in check on a development server with a **clean committed checkout**,
installed Pixi/Nextflow, the existing digest-pinned DAP/ATAC Docker images, and an existing
separate Python environment containing `pyBigWig`. It does not build or pull images,
install a reader, download public sequencing datasets, or use AI. All input reads and
references are generated from the repository's deterministic fixtures.

```bash
pixi run validate-study --keep --work-root /var/tmp \
  --bigwig-python /path/to/bigwig-environment/bin/python
```

The work root must be outside the checkout and visible to Docker. On installations
where Docker cannot bind host `/tmp` or home directories, place both the checkout and
work root in an approved Docker-visible location such as `/var/tmp`. The test serves
synthetic DAP reads over a temporary HTTP endpoint reachable from its containers.

The runner creates fresh fixture-only registries and records explicit synthetic review
decisions. It has no option to approve an existing study. It runs SE/PE DAP (five libraries,
including a shared control) and bulk ATAC (two libraries, each with two lanes), assigned
to two local worker identities with separate staged data, attempts and output roots.
It exercises controller reconnect, metadata-only versus full scans, standard reviewed
exports, idempotent collection, budget exhaustion, stale decisions, corrupt artifacts,
failed collection and recovery without sequencing resubmission.

`--keep` retains inputs, registries, traces, logs, per-command timing and `result.json`.
Successful runs otherwise remove their newly created test directory; failures retain
it for diagnosis. Inspect retained worker state before cleanup. Test approvals concern
synthetic integration only and establish no biological acceptance thresholds. The runner
does not test SSH, schedulers, live API/GPU services or public biological datasets.

The [real-study validation report](https://github.com/kundajelab/genesis-preprocessing/blob/main/validation/reports/real-study-validation.md)
records the tested revision, results and limitations. The ordinary `pixi run checks`
suite uses Docker-free fixtures.
