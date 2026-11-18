# Deployment

Each installation has its own runtime, credentials and optional models. No shared demo account
ships with the app. By default the app listens only on loopback (127.0.0.1).

## Docker on Linux

You need Docker with Compose. The image builds the Node 24 assets and the locked Python
application. Choose an absolute runtime directory outside the checkout:

```sh
export GENESIS_RUNTIME="$HOME/projects/genesis/runtime/development"
deploy/web/manage.sh build
deploy/web/manage.sh init --username curator
deploy/web/manage.sh start
deploy/web/manage.sh doctor
```

Open http://127.0.0.1:18765, or use an SSH tunnel from your computer.

| Command or variable | Effect |
| --- | --- |
| `manage.sh stop` | Stops this installation's server and worker |
| `manage.sh cli ARGS` | Runs `genesis ARGS` inside the image |
| `GENESIS_PORT` | Changes the loopback port |
| `GENESIS_PROJECT` | Separates several Compose installations on one host |
| `GENESIS_IMAGE` | Selects the built image |

The supplied configuration is for loopback use. For public access, put your own TLS reverse
proxy in front and configure the app's exact HTTPS origin. Keep provider endpoints and keys
private.

For a native setup, see the [development guide](../development-handoff.md).

## Your API provider

After `init`, configure a model your account can use. These commands ask for the model ID and
the prices per million tokens, then prompt privately for the key:

```sh
read -r -p "Model ID: " GENESIS_MODEL
read -r -p "Input USD per million tokens: " GENESIS_INPUT_PRICE
read -r -p "Output USD per million tokens: " GENESIS_OUTPUT_PRICE
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" provider api \
  --protocol anthropic-messages --model "$GENESIS_MODEL" --default \
  --input-price "$GENESIS_INPUT_PRICE" --output-price "$GENESIS_OUTPUT_PRICE"
deploy/web/manage.sh cli workspace --root "$GENESIS_RUNTIME" budget --limit-usd 5
```

This example uses the Anthropic API. For an OpenAI-compatible service, use
`--protocol openai-chat --endpoint YOUR_BASE_URL`. The endpoint is the API base (usually ending
in `/v1`), not `/chat/completions`. `workspace provider --help` lists every protocol.

Keys are stored under the runtime's `config/secrets/` with private permissions. Do not put
them in command arguments, tracked files or the browser. Setting up a provider does not send a
request to it.

The budget reserves a conservative estimate before each request, including retries. It is not
a billing report, and prices must match your account. Your provider's account limit is the
final spending control. Records imported as local-only are never sent to a hosted provider.

`manage.sh doctor` checks the installation, and `manage.sh cli curation skills` lists the
built-in skills. Neither makes a model request. After changing the provider configuration,
restart with `manage.sh stop` and then `manage.sh start`.

## Optional web search

Archive and literature search work without extra services. Web search needs a
[SearXNG](https://docs.searxng.org/) instance with JSON output on loopback port 18888:

1. Copy `deploy/web/search-settings.example.yml` to
   `$GENESIS_RUNTIME/config/search/settings.yml`.
2. Replace its secret placeholder with your own random value.
3. Start it with `deploy/web/search.yaml`, which uses the same `GENESIS_RUNTIME`.

Follow the SearXNG documentation for configuration and permissions; engine availability
varies. When web search is missing or fails, the app records partial coverage. Do not expose
the search endpoint publicly.

## Local models

Register an OpenAI-compatible local server you already run:

```sh
uv run genesis workspace --root "$GENESIS_RUNTIME" provider local \
  --protocol openai-chat --model YOUR_SERVED_ALIAS \
  --endpoint http://127.0.0.1:19091/v1 --local --default
```

The app can also manage a llama.cpp server for you:

1. Copy `deploy/web/model-runtime.example.json` to `config/model-runtime.json` in your
   runtime.
2. Fill in a pinned Docker image digest, the weight file name, its SHA-256 and the served
   alias. Set context, CPU, memory and GPU options for your hardware.
3. Put your licensed GGUF weights in `models/`.
4. Run the controller under your own service manager:

```sh
uv run genesis ai local-model serve --root "$GENESIS_RUNTIME"
```

The controller verifies weight hashes, names its container after the runtime and reports
readiness to the GUI. GPU mode needs a working NVIDIA container runtime on the host; the app
does not install drivers. `genesis ai local-model --help` lists the download options, and
downloads happen only on request. A configured model is not necessarily ready, so check
provider readiness before starting curation.

Stopping the controller does not delete the weights or its container. When you retire the
service, stop that specific container; never use a host-wide prune.

The local-model lifecycle has synthetic tests only. Local weights and GPU inference have not
been tested in an acceptance run.
