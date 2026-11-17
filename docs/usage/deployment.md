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
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" provider claude \
  --protocol anthropic-messages --model YOUR_MODEL_ID --default \
  --input-price YOUR_INPUT_PRICE --output-price YOUR_OUTPUT_PRICE
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
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
