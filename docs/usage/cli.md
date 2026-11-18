# Command-line interface

Everything in genesis-curation is available from one command, `genesis`. The web app (GUI) and
the CLI call the same application code, so they share accounts, evidence records, review
versions and export rules. Anything you can do in the GUI you can do from the CLI.

```text
genesis
├── workspace   create and maintain a runtime: account, providers, budget
├── web         run the web server and the job worker
├── curation    discover, curate, review and export (the GUI's operations)
├── metadata    import or export metadata tables directly
├── ai          list and check model providers; manage local models
└── sheets      build and score genesis-preprocessing sample sheets
```

`genesis COMMAND --help` lists the subcommands and options of each group.

## Which commands need a runtime

A *runtime* is a private directory that holds the database, job ledger, evidence, provider
configuration and exports. It lives outside the Git checkout. Create one with
`genesis workspace init` (see [the development guide](../development-handoff.md)).

| Group | `--root` | Needs a model provider |
| --- | --- | --- |
| `sheets` | not used | No |
| `workspace`, `ai local-model serve` | the runtime | No |
| `curation` | the runtime (default `$GENESIS_RUNTIME`) | Only for discovery planning and curation |
| `web`, `metadata` | the runtime's `state/` directory | `web` uses the runtime's provider configuration |

In a native checkout, run commands as `uv run genesis ...`. In the Docker deployment, run them
as `deploy/web/manage.sh cli ...`.

## Two ways to work

**Sample sheets** (`genesis sheets`): rule-based and reproducible. It goes from archive search
to a sheet that genesis-preprocessing can run, with a recorded reason for every run left out.
It uses no LLM. See [sample sheets](sample-sheets.md).

**Metadata curation** (`genesis curation`, or the GUI): LLM-assisted. It discovers datasets,
proposes metadata fields with source evidence, and keeps every uncertain field for human
review before export. See [the curation workflow](workflow.md).

## Set up a runtime

```sh
export GENESIS_RUNTIME="$PWD/../runtime/development"
genesis workspace --root "$GENESIS_RUNTIME" init --username curator
genesis workspace --root "$GENESIS_RUNTIME" provider claude \
    --protocol anthropic-messages --model YOUR_MODEL_ID --default \
    --input-price INPUT_USD_PER_MTOK --output-price OUTPUT_USD_PER_MTOK
genesis workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
genesis workspace --root "$GENESIS_RUNTIME" doctor
```

`provider` prompts for the API key and stores it with private permissions in the runtime.
`clean` previews removal of cache and temporary files, and `clean --apply` removes them.

## Curate from the CLI

`genesis curation` reads the runtime from `GENESIS_RUNTIME` (or `--root`). Global options such
as `--wait`, `--json` and `--output` go before the operation:

```sh
genesis curation summary                     # workspace overview
genesis curation skills                      # the skill catalog
genesis curation --wait discover --species 'Sorghum bicolor' --assay RNA-seq
genesis curation --wait intake PRJNA123456   # add one study by accession
genesis curation review list                 # studies waiting for review
genesis curation review show STUDY_ID        # its tasks and current version
genesis curation --wait curate --study STUDY_ID --version VERSION
genesis curation review approve STUDY_ID --version VERSION
genesis curation --wait --output curated.zip export --clean
```

`--wait` runs queued jobs in the CLI when no worker is running, and otherwise follows the
worker's progress. Progress goes to stderr and results go to stdout, so `--json` output can be
piped. Accessions and IDs above are placeholders.

Operations without a dedicated subcommand are reachable through `request`, which sends any GUI
request:

```sh
genesis curation operations                              # every supported path
genesis curation --json request GET /api/workspace
genesis curation --json request POST /api/OPERATION --file body.json
```

The full workflow, including review decisions and measured QC, is in
[the curation workflow](workflow.md).

## Serve the GUI

The Docker deployment starts the server and worker for you (`deploy/web/manage.sh start`). In a
native checkout, `scripts/dev.sh` builds the GUI and runs both. To run them yourself:

```sh
genesis web worker --root "$GENESIS_RUNTIME/state" \
    --providers "$GENESIS_RUNTIME/config/providers.json" &
genesis web serve --root "$GENESIS_RUNTIME/state" \
    --providers "$GENESIS_RUNTIME/config/providers.json" \
    --origin http://127.0.0.1:18765 --dev
```

Open http://127.0.0.1:18765. From another computer, forward the port with
`ssh -N -L 18765:127.0.0.1:18765 USER@SERVER`.
