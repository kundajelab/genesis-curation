# genesis-curation

Python tooling for the Genesis project that sits around the processing pipelines rather than
inside them:

- **Metadata and LLM extraction** (`llm`, `curation`): harmonize study metadata, optionally with
  model assistance, and keep every proposed value tied to its source.
- **Vetting and human review** (`curation`, `qc_policy`): declarative QC policies, review queues
  and status pages; a person accepts or rejects each decision.
- **Records and sources** (`contracts`, `registry`, `publication`): versioned record schemas, a
  controller-local catalog, and sanitized exports of evidence.
- **Execution** (`execution`): a generic job controller for local or remote workers, and
  the `genesis benchmark` launcher.
- **scATAC** (`scatac`): discovery, inventory and model export. Pseudobulks come from
  the `scatac-seq` workflow in genesis-preprocessing.
- **Method benchmarks** (`benchmark`, `benchmarks/`): explicit, reproducible comparisons of
  processing choices. They never change pipeline defaults.

The Nextflow pipelines (DAP-seq, bulk ATAC-seq) live in
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing) and run there
directly (`pixi run pipeline`, `pixi run pipeline-atac`); this repository does not launch them.
It imports their finished outputs for curation. `genesis benchmark` and `genesis doctor` use
that checkout's Pixi environment: set `GENESIS_REPO` to it.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.14.

```bash
git clone https://github.com/kundajelab/genesis-curation.git
cd genesis-curation
uv python install 3.14.5   # .python-version pins the GIL build, which uv cannot download
uv sync --frozen
uv run python -m genesis_curation.execution.install --repo . --bin-dir ~/.local/bin
genesis --help
```

## Checks

```bash
GENESIS_REPO=/path/to/genesis-preprocessing scripts/check.sh
```

Without `GENESIS_REPO`, the launcher checks report NOT RUN and everything else still runs.
`tests/accept_*_live.py` (live model services) are opt-in and run separately.

## Documentation

- [Plant cell-type annotation](docs/usage/celltype.md) with reviewed RNA labels
- [Curation](docs/usage/curation.md), [AI providers](docs/usage/ai-providers.md),
  [LLM deployment](docs/usage/llm-deployment.md)
- [Execution](docs/usage/execution.md)
- [scATAC](docs/usage/scatac.md), [scATAC model handoff](docs/usage/scatac-model-handoff.md)
- [Benchmarks](benchmarks/README.md)

This code was moved from `genesis-preprocessing` (package `genesis_tools`) in October 2026;
its earlier history is in that repository.

For the optional browser review workspace, evidence-backed field decisions, local
Hugging Face models and hosted provider APIs, see
[human-reviewed metadata curation](docs/usage/metadata-web.md). Use [Find data](docs/usage/agentic-mining.md) to discover public studies and prepare metadata automatically, then review the exceptions.
