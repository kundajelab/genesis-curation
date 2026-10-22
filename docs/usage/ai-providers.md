# Optional AI providers and read-only assistance

Connect an optional LLM service to propose metadata, explain a failure or answer
questions about selected catalog records. Start with the mock configuration to
learn the commands without an account or network request, then configure an API
or private endpoint. Processing and manual review also work without a provider.

The client needs no GPU libraries. Hosting a model uses a
[separate serving environment](llm-deployment.md).

## Configure and check

Start with [the complete mock configuration](../../examples/curation/providers.mock.json):

```bash
genesis registry init /local/catalog
genesis ai providers list --config examples/curation/providers.mock.json
genesis ai providers check /local/catalog \
  --config examples/curation/providers.mock.json --provider mock
```

Mock probes report `MOCK_PASS`, not live capability acceptance. The mock produces
explicit synthetic/default responses and cannot establish biological accuracy.
`mode: disabled` records that inference is disabled and leaves the manual path
available. No provider is chosen automatically.

Copy [the live configuration template](../../examples/curation/providers.live.example.json)
outside the checkout. Replace the endpoint and model with your approved service.
Use the service's `/v1` base URL; Genesis appends `/chat/completions`. The adapter
uses Chat Completions, not Responses or a vendor's unrelated native API. It expects
the configured model identifier in responses. Resolve alias differences by setting
a supported exact identifier, not by accepting an unexpected model silently.

An external provider requires HTTPS and an explicit allowlist of tasks and source
classifications. The template allows only `public` data. Configure an existing
credential environment variable (the name only), or an owned regular secret file
with mode `0600`; secrets never appear in process arguments. Do not commit secrets.
A private endpoint uses `external: false`; HTTP is allowed only on loopback, including
an SSH tunnel. Classifying a server as private is a trusted administrator decision,
not automatic proof that its network or retention policy is private.

```bash
# After editing /local/providers.json and provisioning the referenced credential:
genesis ai providers check /local/catalog \
  --config /local/providers.json --provider api
genesis ai providers check /local/catalog \
  --config /local/providers.json --provider private
```

Every configuration field is explicit. `allowed_tasks` selects `probe`, `metadata`,
`diagnose`, and/or `ask`. `allowed_classifications` selects `public`, `internal`,
`local-only`; an external provider can never allow `local-only`. Classification
restrictions are checked before requests and before replaying a cached response.
There is no external fallback when a private service fails. Metadata ingestion
retains the most restrictive classification of its sources; an assistant question
cannot downgrade it. Redaction masks configured credentials and common secret
patterns before transmission and audit storage. It does not identify every possible
sensitive biological fact: source classification and explicit selection still matter.

`context_chars` bounds input plus schema/prompt characters. It is not an exact
model-token estimate. `max_tokens` bounds output per request; total attempts are
bounded by `max_retries` (0–3) plus at most one format repair. `timeout_seconds`
bounds each response, and a local cross-process slot lock enforces `concurrency`.
Use one authoritative configuration per provider ID. Cancellation between attempts
and Ctrl-C are recorded. A socket operation may take up to its configured timeout.
No task holds a registry write transaction while contacting a model.

Capabilities are endpoint/model/runtime-specific. With `json_schema: true`, the
probe sends a JSON Schema request and checks the answer. With `tools` or `streaming`
true, it also probes those protocols separately; otherwise they remain `NOT_TESTED`.
A tool probe validates arguments without executing a function. A successful probe
shows the observed response met the contract; it does not prove biological truth
or that every possible answer will be constrained. An unsupported schema response
is an error, never an undocumented fallback. You can explicitly configure
`json_schema: false` for a server that only supports schema-in-prompt responses;
local validation still applies.

Refusals, truncation, wrong-model replies, malformed JSON, invalid field types,
429/5xx, timeouts and context overflow are distinguished in retained attempts.
One format repair can request the same evidence in valid JSON. It cannot relax the
schema, invent source evidence, bypass refusal or change scientific settings.
Retries and temperature zero do not imply bitwise reproducibility.

## Read-only questions and provenance

```bash
genesis registry search /local/catalog --text 'leaf' --limit 10
genesis ai ask /local/catalog 'What metadata is recorded for these libraries?' \
  --dataset DATASET_ID --config /local/providers.json --provider private \
  --classification local-only
genesis registry show /local/catalog INVOCATION_VERSION --kind invocation
```

Questions are bounded to 1–10 explicitly selected datasets. The assistant exposes
only typed catalog search/show/history/evidence reads; it has no SQL, shell, file
editing, job launch, approval or deployment tool. Returned citations must refer to
selected record hashes. Source/log instructions are untrusted data, and answers
remain proposals for human interpretation.

Invocations record provider/model IDs, available model and serving revisions,
prompt/schema versions, request/source hashes, redacted responses (including failed
attempts), latency, retries and reported token usage. An API alias is not an immutable
revision. Usage across attempts is totaled only when all relevant counts are
reported; otherwise it remains null. Cost remains null because no price is guessed.
Errors include the saved invocation version for inspection. Invocations linked to
a dataset also appear in `registry history`. Cache use is an explicit Python API
option; its key includes configuration/model, prompt content/version, schema, sources,
classification and request data. Replaying a saved proposal does not rerun inference.

Use [metadata harmonization](curation.md#evidence-backed-metadata-harmonization),
[failure diagnosis](curation.md#failure-diagnosis), or
[private model deployment](llm-deployment.md) for complete workflows.

The wire adapter follows the documented
[vLLM OpenAI-compatible protocol](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/).
Live vendor/API acceptance is separate from fake HTTP tests.

## Opt-in live acceptance

After configuring an approved endpoint and credential, this separate runner sends
only the fixed public probe. It tightens output/time/retry budgets and writes an
immutable evidence report; it can incur the provider's normal charges. It is never
called by `pixi run checks`.

```bash
pixi run uv run --frozen python tests/accept_provider_live.py \
  --registry /local/catalog --config /local/providers.json --provider api \
  --authorize-egress public --output live-provider-acceptance.json
```

Known provider-specific reasoning fields are omitted from retained protocol
responses; audits retain bounded answers, evidence and brief justifications.
