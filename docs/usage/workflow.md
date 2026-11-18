# Curation workflow

This guide covers metadata curation from the CLI. Every step is also available in the GUI,
and both work on the same runtime, so you can start a job in one and continue in the other.
Set up a runtime and a model provider first ([CLI guide](cli.md#set-up-a-runtime)).

`genesis curation` reads the runtime from `GENESIS_RUNTIME` (or `--root`). It acts as the
runtime's single curator account. Global options (`--wait`, `--json`, `--output`) go before
the operation. IDs and accessions below are placeholders.

## 1. Discover

```sh
genesis curation --wait discover --species 'Arabidopsis thaliana' --assay RNA-seq
genesis curation --wait intake PRJNA123456
genesis curation import metadata.csv --namespace my-import --id-column run_accession \
    --classification public
```

- `discover` searches archives, literature and (if configured) the web for the given species
  and assays. It records every query, error and gap in coverage. `--species` and `--assay` can
  be repeated, and `--constraints FILE` adds tissue, genotype or treatment requirements.
- `intake` adds one study by accession.
- `import` loads your own table. Imported values stay labelled as externally supplied.
  `--classification` controls whether the records may be sent to a hosted model: `public`
  allows it, while `local-only` (the default) keeps them to local models. Choose it by your
  data policy.

## 2. Curate

```sh
genesis curation review list
genesis curation review show STUDY_ID       # shows the study's current VERSION
genesis curation --wait curate --study STUDY_ID --version VERSION
```

Curation retrieves archive evidence first and searches papers only for fields that are still
missing. The model proposes values together with the exact passages that support them. Some
rules always hold:

- Genotype and cultivar stay in separate columns.
- When a study is curated again, earlier proposals the model omits are kept. An explicit
  "unknown" withdraws a proposal.
- Earlier proposals are shown to the model as context, never as evidence.

The skill used for each step is described in [skills](skills.md).

## 3. Review and decide

Uncertain fields and conflicts are grouped into tasks by study. `review show` lists the tasks
and the current task and study versions. Write a decision as JSON with the reason and the
version you reviewed, then submit it:

```sh
genesis curation review draft TASK_ID --file draft.json      # save a draft
genesis curation review decide TASK_ID --file decision.json  # record the decision
genesis curation review history STUDY_ID
genesis curation review approve STUDY_ID --version VERSION   # sign off the study
```

A decision made against an old version is rejected, so two reviewers cannot overwrite each
other. Source snapshots and before/after changes stay available. Model confidence never counts
as approval, and values without evidence stay unknown.

## 4. Export

```sh
genesis curation --wait --output curated.zip export --clean
```

`--clean` exports only studies a human has signed off and that have not changed since. Without
it, the export is a draft of every current record.

## Jobs

Discovery, curation and export run as jobs:

```sh
genesis curation jobs list
genesis curation jobs wait JOB_ID
genesis curation jobs cancel JOB_ID
```

With `--wait`, the CLI runs queued jobs itself when no worker is running, or follows the
worker's progress when one is. If the worker exits, the CLI takes over its queued jobs. A job
interrupted while running is never replayed automatically; it needs explicit recovery.
Progress goes to stderr and results to stdout.

## Measured QC

QC measurements come from an external pipeline. The application imports them; it does not
compute them from reads.

```sh
genesis curation qc import measurements.tsv
genesis curation qc evaluate profile.json          # returns an ANALYSIS_ID
genesis curation qc rows ANALYSIS_ID
genesis curation qc decide ANALYSIS_ID --file decision.json
genesis curation qc export ANALYSIS_ID
```

The columns, thresholds and review rules are in [RNA curation](rna-curation.md).

## Everything else

Any GUI operation is available through `request`, including campaigns, skills, provider
probes, review drafts, QC and downloads:

```sh
genesis curation operations                           # list every path
genesis curation --json request GET /api/workspace
genesis curation --json request POST /api/OPERATION --file body.json
```

Use the paths that `operations` lists and the same request bodies the GUI sends.
