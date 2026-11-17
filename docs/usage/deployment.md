# Deployment

The default deployment is private and listens on loopback. Each installation owns
its runtime, credentials and optional models. No shared demo account is shipped.

## Docker on Linux

Requires Docker with Compose. The image builds Node 24 assets and the locked Python
application. Choose an absolute runtime directory outside the checkout.

```sh
export GENESIS_RUNTIME="$HOME/projects/genesis/runtime/development"
deploy/web/manage.sh build
deploy/web/manage.sh init --username curator
deploy/web/manage.sh start
deploy/web/manage.sh doctor
```

Open http://127.0.0.1:18765, or use an SSH tunnel from your computer. `GENESIS_PORT`
changes the loopback port. `GENESIS_PROJECT` distinguishes Compose installations;
`GENESIS_IMAGE` selects the built image. `manage.sh stop` stops only this project's
server and worker. For public access, configure your own TLS reverse proxy and the
application's exact HTTPS origin; the supplied loopback development configuration
is not a public HTTP deployment. Keep provider endpoints and keys private.

For native development, follow [the handoff](../development-handoff.md).

## Your API provider

After initialization, configure a model available to your own account. These commands
prompt for model ID and per-million-token prices, then privately prompt for the key:

```sh
read -r -p "Model ID: " GENESIS_MODEL
read -r -p "Input USD per million tokens: " GENESIS_INPUT_PRICE
read -r -p "Output USD per million tokens: " GENESIS_OUTPUT_PRICE
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" provider api \
  --protocol anthropic-messages --model "$GENESIS_MODEL" --default \
  --input-price "$GENESIS_INPUT_PRICE" --output-price "$GENESIS_OUTPUT_PRICE"
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
```

This example uses the Anthropic endpoint. For your OpenAI-compatible service, use
`--protocol openai-chat --endpoint YOUR_BASE_URL` instead. The endpoint is the API
base (typically ending in `/v1`), not `/chat/completions`. Other supported protocols
are listed by `workspace provider --help`. Keys are stored under the runtime's
`config/secrets/` with private permissions; do not put them in command arguments,
tracked files or the browser. Provider setup does not make an inference request.

The optional budget reserves conservative estimates before requests, including
retries; it is not a billing report. Prices must match your provider account. The
provider's account limit remains your final spending control. Local-only records
cannot be sent to a hosted provider.

Check the installation with `manage.sh doctor` and list built-in skills with
`manage.sh cli curation skills`. These checks do not incur inference charges.
After changing provider configuration, restart the app and worker with
`manage.sh stop` followed by `manage.sh start`.

## Optional web search

Archive and literature access does not require a metasearch service. Web-search
coverage requires a SearXNG instance exposing JSON search on loopback port 18888.
The optional `deploy/web/search.yaml` uses the same `GENESIS_RUNTIME` as the app.
Copy `search-settings.example.yml` to `$GENESIS_RUNTIME/config/search/settings.yml`
and replace its secret placeholder with your own random value before starting it.
Use the upstream SearXNG deployment instructions for configuration and permissions;
engine availability varies. The application records missing or failed web search
as partial coverage. Do not expose this optional search endpoint publicly.


## Local models

An existing OpenAI-compatible local server can be registered without downloading
anything:

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" provider local \
  --protocol openai-chat --model YOUR_SERVED_ALIAS \
  --endpoint http://127.0.0.1:19091/v1 --local --default
```

For the optional managed llama.cpp controller, copy
`deploy/web/model-runtime.example.json` to `config/model-runtime.json` in your
runtime. Supply a real pinned Docker image digest, weight filename, SHA-256 and
served alias. Put your licensed GGUF weights in `models/`. Choose context, CPU,
memory and GPU settings suitable for your hardware. GPU mode requires a working
host NVIDIA container runtime; the application does not install drivers.

```sh
uv run genesis ai local-model serve --root "$GENESIS_RUNTIME"
```

Run this foreground controller under your own service manager. It verifies model
hashes, uses a runtime-specific container name, and publishes readiness for the GUI.
The GUI can request supported installed alternatives; list/download options are
under `genesis ai local-model --help`. Downloads are explicit. A configured model
is not necessarily ready: check provider readiness before launching curation.
Stopping the controller does not delete weights or the owned Docker container;
stop that specific container when retiring the service. Never use host-wide prune.

The managed local-model lifecycle has synthetic regression coverage. The current
API-only acceptance run did not launch local weights or verify GPU inference.
