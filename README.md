# genesis-curation

Discover plant datasets, curate their metadata with source evidence, and review
uncertain fields study by study. The GUI and CLI use the same application.

1. **Discover:** search archives, literature and configured web search; retain actual
   queries, source links, errors and partial coverage.
2. **Curation:** organize records by assay and propose metadata using your API account
   or local model. Keep original values and supporting passages.
3. **Review:** resolve the study checklist and sign off clean exports. Retain the
   source and change history.

Supported metadata includes RNA-seq, bulk ATAC, scATAC, TSS, DAP-seq, ChIP-seq, WGBS,
MPRA and genomes. Fourteen built-in skills provide assay and workflow guidance.

## Start on a Linux server

Requires Git and Docker with Compose. API-backed curation does not require a GPU or
locally installed Python/Node. Choose a workspace location and keep runtime data
outside the checkout.

```sh
mkdir -p "$HOME/projects/genesis"
cd "$HOME/projects/genesis"
git clone https://github.com/kundajelab/genesis-curation.git
cd genesis-curation
export GENESIS_RUNTIME="$(dirname "$PWD")/runtime/development"

deploy/web/manage.sh build
deploy/web/manage.sh init --username curator
```

Initialization asks for your own login password. Next, configure your provider using
[the deployment guide](docs/usage/deployment.md#your-api-provider), then start:

```sh
deploy/web/manage.sh start
deploy/web/manage.sh doctor
```

Open **http://127.0.0.1:18765**. If the application is on a remote server, run this
on your computer, replacing `USER` and `SERVER`, then open the same address locally:

```sh
ssh -N -L 18765:127.0.0.1:18765 USER@SERVER
```

The default service listens on loopback. Public hosting needs your own HTTPS proxy
configuration. Native development requires **uv, Python 3.14 and Node 24**; see the
[fresh-server development guide](docs/development-handoff.md).

## Use and maintain

- [Deployment and provider setup](docs/usage/deployment.md): your API keys, local
  models, optional search, startup and shutdown.
- [GUI/CLI workflow](docs/usage/workflow.md): discovery, curation, study decisions,
  job progress and exports.
- [Skill folders](src/genesis_curation/skills/) and
  [skill guide](docs/usage/skills.md): built-in instructions and private custom skills.
- [RNA evidence and imported QC](docs/usage/rna-curation.md).
- [Development and cleanup](docs/development-handoff.md).
- [Validation results and limits](docs/validation/evidence-quality.md).

Fresh workspaces are empty. Credentials, databases, evidence and model weights stay
in your private runtime. Use your own provider account and a model available to it.
No shared API keys, reference spreadsheet or local-model downloads are included.

## Sample sheets for genesis-preprocessing

`genesis sheets` scores sample sheets for the
[genesis-preprocessing](https://github.com/kundajelab/genesis-preprocessing) workflows
against gold sheets and known traps; see [evals/README.md](evals/README.md).

## Scope and validation

This repository handles metadata curation, not sequencing preprocessing or model
training. Quantitative QC is currently RNA-focused. Search coverage depends on source
access, and uncertain biological claims need expert review. Skills and successful
inference do not establish biological accuracy.

Run `scripts/check.sh` in the native development environment; GitHub Actions runs it on every pull request. The suite covers
software contracts, evidence isolation, review, providers, exports and synthetic
scale; live API costs and biological accuracy are evaluated separately. The supported
container deployment is Linux; other operating systems and GPU configurations are
not claimed to work without platform-specific setup.
