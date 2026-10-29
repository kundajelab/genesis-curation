# Metadata workflow

Discover accepts a research goal, species names and assay selections. The model plans
bounded searches using the source catalog and repository recipes. The worker records
queries, source URLs, returned records and failures. Only actual searches appear in the
activity log. No sequencing files are downloaded.

Curation displays paginated modality tables. Select rows or a modality to retrieve
archive and publication evidence and prepare suggestions. Work runs in durable jobs;
progress appears while the GUI remains available. Requests respect configured context,
input and output budgets. Pause and resume preserve completed work. A local model must
be active before its queued requests run.

Review groups unresolved decisions into a to-do list. Evidence and original/proposed
values remain visible. Decisions record the actor and reason. Study sign-off is separate
from field suggestions. Export the working copy for inspection, or the clean export for
signed-off metadata; exports include originals and change history.

## Import

```sh
genesis metadata import --root /path/to/state --file metadata.xlsx
genesis metadata import --root /path/to/state --file metadata.csv --id-column sample_id
genesis metadata export --root /path/to/state --name reviewed --clean
```

XLSX tables use a header row. CSV, TSV and JSON imports require an explicit identifier.
Unknown XLSX table names appear under Other. Original cells and formulas are retained.

The operator can load a neutral `genesis-demo/1` JSONL snapshot with
`genesis metadata load-demo --root /path/to/state --file demo.jsonl`. These rows are
labelled externally curated and need independent retrieval and review. Loading is
idempotent by content hash. This is an offline import, not a connected data source.

## Skills and models

Models & skills shows configured providers, pinned local models and reviewed skill
content. Provider availability, installed weights and inference probes are distinct
states. Hosted providers require operator-supplied credentials and permitted data
classifications. Skills guide extraction but do not override source evidence or approval.

Alex Borowsky's plant cell-type annotation skill remains available as a reviewed,
provenance-pinned metadata guidance resource. Gene-expression analysis and preprocessing
are outside this application's scope.
