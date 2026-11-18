# genesis-curation

Find public plant sequencing datasets, curate their sample metadata with source evidence, and
turn them into sample sheets that
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing) can run.

The package provides two tools:

- **Sample sheets** (`genesis sheets`, CLI only). These build a run-ready sheet for one species
  and one genesis-preprocessing workflow (RNA-seq, bulk ATAC, DAP-seq, TSS or scATAC) from
  ENA/SRA. Every candidate run is kept or left out with a recorded reason, and the result is
  scored against curated gold sheets. The rules are deterministic, and no LLM or account is
  needed.
- **Metadata curation** (web app and `genesis curation`). This tool discovers datasets,
  proposes metadata with an LLM, and keeps the archive and paper evidence for each value.
  Uncertain fields go to study-by-study human review before export. The GUI and the CLI run
  the same application code, so you can use either.

## Quick start: build a sample sheet

You need Git, [uv](https://docs.astral.sh/uv/) and Python 3.14.

```sh
git clone https://github.com/kundajelab/genesis-curation.git
cd genesis-curation
uv sync --frozen --extra web

uv run genesis sheets gold-fetch --to gold
uv run genesis sheets build --workflow rna-seq --species Sorghum_bicolor --out sb-rna \
    --no-measure --strandedness-from gold/Sorghum_bicolor/rna-seq-curated.runs-qc.tsv
uv run genesis sheets score --workflow rna-seq --species Sorghum_bicolor --gold-dir gold \
    --candidate-sheet sb-rna/rna-seq.tsv --candidate-qc sb-rna/rna-seq.runs-qc.tsv
```

This builds the Sorghum RNA-seq sheet from archive metadata alone and reuses the curated
strandedness calls, so no reads are downloaded. It takes about three minutes, most of it
ENA queries. `sb-rna/rna-seq.runs-qc.tsv` gives the reason
each run was left out. [Sample sheets](docs/usage/sample-sheets.md) covers every workflow,
read measurement, outputs and scoring.

## Quick start: run the curation app

The supported deployment is Docker with Compose on Linux. Keep the runtime (database,
evidence, keys) outside the checkout:

```sh
export GENESIS_RUNTIME="$(dirname "$PWD")/runtime/development"
deploy/web/manage.sh build
deploy/web/manage.sh init --username curator     # asks for a password
```

Add your model provider ([deployment guide](docs/usage/deployment.md#your-api-provider)),
then start the app:

```sh
deploy/web/manage.sh start
deploy/web/manage.sh doctor
```

Open http://127.0.0.1:18765. If the app runs on a remote server, forward the port first:
`ssh -N -L 18765:127.0.0.1:18765 USER@SERVER`. The app listens only on loopback. Public
hosting needs your own HTTPS proxy.

For a native (non-Docker) setup, see the [development guide](docs/development-handoff.md).

## Curation workflow

1. **Discover**: search archives, literature and (optionally) the web. The app records the
   actual queries, source links, errors and any partial coverage.
2. **Curate**: group records by assay and propose metadata with your model provider.
   Original values and supporting passages are kept.
3. **Review**: resolve each study's checklist and sign it off. Only signed-off studies go
   into a clean export, and the source and change history is retained.

Each LLM step follows a *skill*, a short instruction set for one assay or step. There are
14 built-in skills. Supported metadata covers RNA-seq, bulk ATAC, scATAC, TSS, DAP-seq,
ChIP-seq, WGBS, MPRA and genomes.

## Documentation

| Guide | Contents |
| --- | --- |
| [Command-line interface](docs/usage/cli.md) | Every command group, runtimes, and the CLI equivalents of GUI actions |
| [Sample sheets](docs/usage/sample-sheets.md) | `genesis sheets`: build, outputs, curated decisions, scoring |
| [Curation workflow](docs/usage/workflow.md) | Discover, curate, review and export from the CLI or GUI |
| [Skills](docs/usage/skills.md) | What each skill does, how one is chosen, custom skills |
| [RNA curation and measured QC](docs/usage/rna-curation.md) | RNA evidence rules and importing QC measurements |
| [Deployment](docs/usage/deployment.md) | Docker, model providers, budget, web search, local models |
| [Development guide](docs/development-handoff.md) | Native setup, runtime layout, cleanup |
| [Evaluation](evals/README.md) | Gold sheets, traps and score history |
| [Validation reports](docs/validation/) | Recorded acceptance and pilot results |

## Scope

This repository curates metadata and writes sample sheets. Sequencing preprocessing runs in
genesis-preprocessing, and model training happens elsewhere. Measured QC in the curation app
is imported from pipelines, not computed here. `genesis sheets` measures small read samples
only to decide what goes into a sheet. Search coverage depends on what each source allows, and
uncertain biological claims need expert review.

## Development

Run `scripts/check.sh` before committing. It runs lint, formatting, type checks and every
`tests/verify_*.py` program offline. There is no CI, so run it locally. See
[AGENTS.md](AGENTS.md) for contribution rules.
