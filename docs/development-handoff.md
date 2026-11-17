# Development handoff

Use a private workspace on your own server. Keep the checkout and runtime separate;
never commit credentials, databases, downloaded evidence or model weights.

## Native setup

Requires Git, uv, Python 3.14 and Node 24. Install missing system tools using your
server's normal administration process.

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

Initialization prompts for a password and creates an empty workspace. Configure your
own provider, then run `scripts/dev.sh`. Open http://127.0.0.1:18765. From another
computer, forward that loopback port with SSH. The script builds the GUI and runs
both the server and worker; stopping it stops its children.

## Your API account

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" provider claude \
  --protocol anthropic-messages --model YOUR_MODEL_ID --default \
  --input-price YOUR_INPUT_PRICE --output-price YOUR_OUTPUT_PRICE
uv run genesis workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
```

The provider command prompts privately for your key. Prices are USD per million
tokens; use the prices applicable to your account. The key is stored with mode 0600
under `config/secrets`, outside Git. Do not paste it into a tracked configuration.
OpenAI-compatible chat and responses endpoints are also supported; see command help.
API access depends on your account and endpoint, not merely the model's display name.

The optional budget reserves conservative request estimates before each attempt,
including retries. It does not report billing or refund failed calls. Without a
budget file, no application spending cap is imposed. Your provider's account limits
remain the final billing control.

## Runtime layout

`config/` holds provider references and secrets; `state/` holds the database and job
ledger; `evidence/`, `exports/` and `skills/` hold durable artifacts. `models/` holds
optional weights. `cache/`, `tmp/` and `logs/` are local runtime artifacts. Back up
the whole runtime with services stopped. Never publish it as a Git repository.

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" clean
# After inspecting the preview:
uv run genesis workspace --root "$GENESIS_RUNTIME" clean --apply
```

Cleanup removes only cache and temporary contents, preserving evidence, state, keys
and weights. It refuses cleanup while the worker owns its lock.

## Development task prompt

> Work on the main branch in this checkout. Fetch origin first and read AGENTS.md.
> Keep a single main branch; do not create additional branches or worktrees.
> Keep runtime files outside Git and inside my chosen workspace. Preserve original
> evidence and human decisions. Implement the same behavior for GUI and CLI. Run
> scripts/check.sh and relevant browser checks. Report exact failures and skipped
> checks. Ask before pushing, merging, installing system tools or paid live tests.

See [deployment](usage/deployment.md), [CLI workflow](usage/workflow.md) and
[RNA curation](usage/rna-curation.md). Synthetic checks do not establish biological
accuracy. Fresh installs contain no reference spreadsheet or fabricated discovery history.
