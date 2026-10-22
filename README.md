# genesis-curation

Python tooling for the Genesis project that sits around the processing pipelines rather than
inside them:

- **Metadata and LLM extraction** (`llm`, `curation`): harmonize study metadata, optionally with
  model assistance, and keep every proposed value tied to its source.
- **Vetting and human review** (`curation`, `qc_policy`): declarative QC policies, review queues
  and status pages; a person accepts or rejects each decision.
- **Records and sources** (`contracts`, `registry`, `publication`): versioned record schemas, a
  controller-local catalog, and sanitized exports of evidence.
- **Studies** (`study`, `execution`): plan, launch and collect multi-library studies on local or
  remote workers.
- **scATAC** (`scatac`): discovery, inventory and fragment-first products.
- **Method benchmarks** (`benchmark`, `benchmarks/`): explicit, reproducible comparisons of
  processing choices. They never change pipeline defaults.

The Nextflow pipelines (DAP-seq, bulk ATAC-seq) live in
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing). Commands that run a
pipeline need a checkout of it: set `GENESIS_REPO` to that checkout.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
git clone https://github.com/kundajelab/genesis-curation.git
cd genesis-curation
uv sync --frozen
uv run python -m genesis_curation.execution.install --repo . --bin-dir ~/.local/bin
genesis --help
```

## Run a pipeline through the study tools

```bash
export GENESIS_REPO=/path/to/genesis-preprocessing   # installed with `pixi run install-all`
genesis pipeline --help
genesis pipeline-atac --help
```

## Checks

```bash
GENESIS_REPO=/path/to/genesis-preprocessing scripts/check.sh
```

Without `GENESIS_REPO`, the launcher checks report NOT RUN and everything else still runs.
`tests/validate_study.py` (real DAP/ATAC studies) and `tests/accept_*_live.py` (live model
services) are opt-in and run separately.

## Documentation

- [Curation](docs/usage/curation.md), [AI providers](docs/usage/ai-providers.md),
  [LLM deployment](docs/usage/llm-deployment.md)
- [Studies](docs/usage/studies.md), [Execution](docs/usage/execution.md)
- [scATAC](docs/usage/scatac.md), [scATAC model handoff](docs/usage/scatac-model-handoff.md)
- [Benchmarks](benchmarks/README.md)

This code was moved from `genesis-preprocessing` (package `genesis_tools`) in October 2026;
its earlier history is in that repository.
