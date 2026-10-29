# genesis-curation

Discover plant datasets, curate their metadata with evidence, and review uncertain fields.

The GUI has three steps:

1. **Discover** — combine archive queries, literature searches, configured web search,
   and versioned repository recipes. Search attempts, results and failures are recorded.
2. **Curation** — browse metadata by modality, retrieve source evidence and request
   model suggestions. Original values remain available alongside proposed changes.
3. **Review** — work through unresolved decisions and sign off studies. Clean exports
   require review; model confidence alone is not approval.

Supported discovery recipes cover RNA-seq, bulk ATAC, scATAC, TSS assays, DAP-seq,
ChIP-seq, WGBS, MPRA and genomes. Species are entered by name. Repository coverage
varies; an empty or failed search is not evidence that no datasets exist.

## Development

```sh
uv python install 3.14.5
uv sync --frozen --extra web
uv run genesis --help
scripts/check.sh
```

Use Python 3.14 and Node 24 for the frontend (`cd frontend && npm ci && npm run build`).
The Docker build compiles the frontend and installs the locked Python environment.
The repository contains metadata tooling and model serving, not sequencing preprocessing.

## Run

See [deployment](docs/usage/deployment.md) and [workflow](docs/usage/workflow.md).
Provider examples are under `examples/curation/` and `deploy/web/`. Local model
installation, checksum verification and deployment commands are under `genesis ai`.
OpenAI-compatible and Anthropic APIs are supported through explicit provider configuration.
Credentials stay outside the repository.

## Evidence and limits

Imported demo rows are externally curated examples, not results produced by this tool.
Their snapshot identities are not archive verification. Real retrieval adds source
versions and actual query receipts. The application does not connect to or modify the
original reference spreadsheet.

Tests exercise evidence alignment, review rules, concurrency, exports, providers and
recovery with synthetic fixtures. They do not establish biological accuracy or complete
archive coverage. Model installation and successful JSON inference are separate from
metadata accuracy evaluation.
