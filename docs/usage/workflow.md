# Discover, curate, review

Initialize a private runtime using [the handoff](../development-handoff.md).
The GUI and CLI use the same application endpoints, permissions, evidence records,
review versions and export gates. The local CLI requires one curator account and
an owned private state directory; it does not impersonate arbitrary reviewers.

## Common CLI operations

Set `GENESIS_RUNTIME` to your runtime directory. Options such as `--wait`, `--json`
and `--output` go before the operation.

```sh
genesis curation summary
genesis curation providers
genesis curation skills
genesis curation --wait discover --species 'Arabidopsis thaliana' --assay RNA-seq
genesis curation --wait intake PRJNA123456
genesis curation import metadata.csv --namespace my-import --id-column run_accession --classification public
genesis curation review list
genesis curation review show STUDY_ID
genesis curation --wait curate --study STUDY_ID --version VERSION
genesis curation review history STUDY_ID
genesis curation jobs list
genesis curation jobs wait JOB_ID
```

Imported records remain externally supplied metadata. Public classification permits
sending selected evidence to a hosted provider; local-only is the import default.
Choose classification according to your data policy, not convenience.

Discover records actual search attempts and partial coverage. Curation retrieves
archive evidence first, then targeted literature where needed. Review groups
uncertain fields and conflicts by study. Model confidence is not human approval.
Genotype and cultivar remain separate columns. Re-curation retains omitted prior
proposals; explicitly marking a field unknown withdraws that proposal. Prior
proposals are shown to the model as context, never as independent evidence.

## Decisions and exports

Use `review show` to obtain the current task and study versions. Submit a decision
JSON matching the API contract, including its reason and expected version:

```sh
genesis curation review draft TASK_ID --file draft.json
genesis curation review decide TASK_ID --file decision.json
genesis curation review approve STUDY_ID --version VERSION
genesis curation --wait --output curated.zip export --clean
```

Stale decisions are rejected. Source snapshots and before/after changes remain
available. Clean releases require human study sign-off. Unknown values remain
unknown rather than being filled by unsupported guesses.

## Full GUI parity

`genesis curation operations` lists all supported application paths. The generic
request operation exposes every GUI operation, including campaigns, skills,
provider probes, model requests, review drafts, QC and downloads:

```sh
genesis curation --json request GET /api/workspace
genesis curation --json request POST /api/OPERATION --file body.json
```

Use the listed paths and the GUI's request bodies; placeholders above are not real
accessions or endpoints. `--wait` runs queued work when no worker owns the runtime,
or observes an existing worker. If that worker exits, the CLI takes ownership of
queued work. An interrupted running job requires explicit recovery and is never
automatically replayed. Progress goes to stderr; output goes to stdout.

Measured QC is imported, not computed from sequencing reads. `qc import`,
`qc evaluate`, `qc rows`, `qc decide` and `qc export` use the same review workflow
as the GUI. See [RNA curation](rna-curation.md) for evidence and QC rules.
