# Development guide

Use a private workspace on your own server, and keep the checkout and the runtime separate.
Never commit credentials, databases, downloaded evidence or model weights.

## Native setup

You need Git, uv, Python 3.14 and Node 24 (with the npm version bundled with it). Install
missing system tools through your server's normal process.

```sh
mkdir -p "$HOME/projects/genesis/tmp"
cd "$HOME/projects/genesis"
export TMPDIR="$PWD/tmp"
git clone https://github.com/kundajelab/genesis-curation.git
cd genesis-curation
export GENESIS_RUNTIME="$(dirname "$PWD")/runtime/development"
uv python install 3.14.5
uv sync --frozen --extra web
uv run genesis workspace --root "$GENESIS_RUNTIME" init --username curator
uv run genesis workspace --root "$GENESIS_RUNTIME" doctor
```

`init` asks for a password and creates an empty runtime. Add a model provider (below), then
run `scripts/dev.sh`. It builds the GUI and starts the server and the worker; stopping the
script stops both. Open http://127.0.0.1:18765, or forward that port with SSH from another
computer.

`genesis sheets` needs only `uv sync`; it does not use the runtime or a provider. See
[sample sheets](usage/sample-sheets.md).

## Model provider and budget

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" provider claude \
  --protocol anthropic-messages --model YOUR_MODEL_ID --default \
  --input-price YOUR_INPUT_PRICE --output-price YOUR_OUTPUT_PRICE
uv run genesis workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
```

`provider` prompts for your API key and stores it with mode 0600 under `config/secrets/` in the
runtime. Never put a key in a tracked file. Prices are USD per million tokens for your account.
OpenAI-compatible chat and responses endpoints are also supported
(`genesis workspace --root DIR provider --help`).

The budget is optional. Before each request, including retries, the app reserves a
conservative cost estimate against the limit. It is not a billing report and does not refund
failed calls. Without a budget, the app sets no spending cap, and your provider account's
limits are the only control.

## Runtime layout

| Directory | Contents |
| --- | --- |
| `config/` | Provider configuration and secrets |
| `state/` | Database and job ledger |
| `evidence/`, `exports/`, `skills/` | Durable artifacts, exports and custom skills |
| `models/` | Optional local model weights |
| `cache/`, `tmp/`, `logs/` | Local working files |

Back up the whole runtime with the services stopped. Never publish it as a Git repository.

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" clean          # preview
uv run genesis workspace --root "$GENESIS_RUNTIME" clean --apply  # remove
```

Cleanup removes only cache and temporary files; it keeps evidence, state, keys and weights.
It refuses to run while a worker holds the runtime lock.

## Checks

Run `scripts/check.sh` before committing. It runs ruff (lint and format), ty type checks and
every `tests/verify_*.py` program offline. These are software checks; they do not establish
biological accuracy.

## Prompt for a coding agent

When handing development to a coding agent, start from this prompt:

> Work on the main branch in this checkout. Fetch origin first and read AGENTS.md.
> Keep a single main branch; do not create additional branches or worktrees.
> Keep runtime files outside Git and inside my chosen workspace. Preserve original
> evidence and human decisions. Implement the same behavior for GUI and CLI. Run
> scripts/check.sh and relevant browser checks. Report exact failures and skipped
> checks. Ask before pushing, merging, installing system tools or paid live tests.

See also [the CLI guide](usage/cli.md), [deployment](usage/deployment.md) and
[RNA curation](usage/rna-curation.md).
