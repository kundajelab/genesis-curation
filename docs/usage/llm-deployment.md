# Optional private model services

Use these commands to start and monitor a private vLLM service on a Linux node
where you already have Docker and GPU access. A standalone server can run it beside preprocessing. A controller can
run independent services or replicas on several existing SSH workers and mix them
with external providers. Multiple GPUs may serve one model **within one node**.
Cross-host tensor/Ray clusters, cloud provisioning and login-node fallback are not
implemented.

The current backend has offline lifecycle/command tests. Real local/remote GPU
acceptance is **NOT RUN** in the published validation evidence. Supply and test your
approved image, model, node and site allocation before operational use. Choose a model and image compatible with your hardware; the template contains
placeholders, not a tested deployment configuration.

## Prerequisites and inspection

Keep the serving stack outside Genesis Python 3.14. The worker needs an existing
clean pinned Genesis checkout, its Python environment, Docker access, NVIDIA
container support, sufficient scratch, and a separately installed/pinned `hf` CLI.
The vLLM image must already be present and specified by `@sha256:FULL_DIGEST`.
Genesis does not install drivers, containers, SSH credentials, runtime permissions,
scheduler allocations or firewall rules. The detected Git commit, Docker/HF versions,
image identity, GPU UUIDs/VRAM, RAM, CPUs and disk availability are recorded and
rechecked. Scheduler nodes require a site-approved existing allocation with expiry.

```bash
# Read metadata only; does not download weights or accept license terms.
genesis ai model inspect NAMESPACE/MODEL --revision REVISION \
  --output inspected-model.json

# For access already granted to a gated repository, use an existing token reference.
genesis ai model inspect NAMESPACE/MODEL --revision FULL_COMMIT_SHA \
  --token-env HF_TOKEN --output inspected-gated-model.json
```

Inspection resolves a branch/tag to a full immutable Hub commit and records license,
gating, architecture, tokenizer/chat template, context length, selected file sizes
and hashes. A downloadable model is not necessarily open-source. Missing license,
unknown context/template, non-safetensors weights or unsupported architectures block
planning. Custom repository code is never trusted or executed. `--snapshot FILE`
accepts an explicitly captured `{api, config, tokenizer}` metadata fixture for
read-only offline inspection; it does not establish live repository or runtime
acceptance. Public metadata APIs and templates can change; re-inspect deliberately.

The pinned download mechanism follows
[Hugging Face revision downloads](https://huggingface.co/docs/huggingface_hub/guides/download).
Model weights are fetched only during an explicitly authorized apply, through the
node's `hf` executable, using the full commit and selected file allowlist. LFS SHA256
and Git blob identities are checked, then an immutable snapshot manifest records
actual SHA256 for every staged file. Partial downloads remain available for a
reviewed replacement plan; cache files and logs are not automatically deleted.

## Build and review an exact plan

Copy [deployment.example.json](../../examples/curation/deployment.example.json)
outside the checkout and replace every synthetic value. It is intentionally not an
executable production recommendation. Configure:

- `node.id` and the existing worker `transport`, `host` (SSH only), `repo`, `python`,
  and `root`; use the same identity/path convention as processing campaigns.
- Versioned node roles, actual physical `gpu_uuids`, `dedicated`, `site_approved`,
  `login_node: false`, and `allocation` (`id`, timezone-aware `expires_at`) when required.
- RAM, CPUs, disk and per-GPU VRAM budgets; context, concurrency, tensor parallelism,
  explicit KV-cache and runtime overhead estimates. Requested vLLM utilization
  must fit the per-GPU reservation. An estimate is not a measured peak guarantee.
- A digest-pinned, locally present `image`, absolute `hf_cli` and node-local `cache`.
  `supported_architectures` is the site's reviewed compatibility list for that image.
- Node-local existing `api_key_file` and optional `hf_token_file`, both owned mode
  `0600`. Controller credentials are never copied to nodes. Provider access through
  the tunnel needs separately provisioned controller-side credentials.
- Node `port` and `controller_port`, both unprivileged; `readiness_seconds` (1–3600).
  Explicit `license_accepted` and, when applicable, `gated_access_confirmed` attest
  prior review/access. Genesis never accepts Hub terms on your behalf.

```bash
genesis ai deployment plan --config /local/deployment.json \
  --model inspected-model.json --output plan.json
```

Planning only inspects the node and writes the plan you requested. Review its exact
configuration, model metadata, observed capabilities and `plan_hash`. The plan hash
covers all these fields. Editing anything requires a new plan and hash. An offline
`--observed captured-node.json` permits testing plan construction; apply always
rechecks real node preconditions and never treats the snapshot as current hardware.

Prefer dedicated GPUs/nodes. Shared nodes require separate declared pipeline GPU
UUIDs and a nonzero pipeline RAM reservation. Campaign `slots` count jobs; they do
not reserve memory or GPUs. Service admission serializes reservations under the
node's `root/llm-services`. Active/unknown deployments retain their GPU, port, RAM
and CPU reservations. A new service cannot collide with them. All services on one
node must use the same worker root. Match processing/scheduler limits to the declared
pipeline reservation; these local checks cannot constrain unrelated users' workloads
or replace site scheduling.

## Apply, inspect, reconnect and stop

The following apply command authorizes weight staging and service launch on the
specific configured node. It is not part of installation or offline tests.

```bash
genesis registry init /local/catalog
genesis ai deployment apply /local/catalog --plan plan.json \
  --authorize EXACT_PLAN_HASH
genesis ai deployment status /local/catalog --plan plan.json
genesis ai deployment logs /local/catalog --plan plan.json
genesis ai deployment stop /local/catalog --plan plan.json
genesis ai deployment status /local/catalog --plan plan.json
```

Launch is asynchronous. States are `PLANNED`, `STAGING`, `STARTING`, `READY`,
`UNHEALTHY`, `UNKNOWN`, `STOPPING`, `STOPPED`, `FAILED`; desired and observed states
are recorded separately. `READY` requires an actual bounded inference with matching
model identity and locally validated structured output. A listening port is
insufficient. Startup failure stops only the new owned container. OOM never selects
a smaller model, different quantization/context or new scientific parameters.

The detached node supervisor continues after the controller exits. It records a
heartbeat and checks allocation expiry. `status` reconciles the exact labeled
container, verifies owned identity and refreshes readiness if the supervisor was
lost. Unknown state retains reservations. Repeating apply for the same plan cannot
start a second server. A terminal plan is not restarted in place: inspect the cause
and produce an explicit replacement plan. Restoring connectivity and calling
`status` reconnects through the existing authenticated SSH configuration.

For SSH services, `status` establishes/checks a private owned control-socket tunnel
and reports the controller loopback endpoint. A lost tunnel yields `UNHEALTHY`;
node communication failure yields `UNKNOWN`. A stale control socket requires manual
inspection/removal after confirming its SSH master exited. It is never treated as
permission to kill an arbitrary port occupant. `stop` verifies plan label and image,
stops only that container and the owned SSH tunnel, and waits for termination before
releasing reservations. While staging/starting, `STOPPING` can persist until the
supervisor acknowledges the stop; poll status again. Caches and logs remain.

Host publication is restricted to `127.0.0.1`; the container listens inside its own
network namespace. No host networking or distributed runtime ports are published.
The service runs as the existing UID, with dropped capabilities, a read-only root
filesystem and read-only model mount. Secrets are passed through a private temporary
environment file, not arguments or registry records. Telemetry and request logging
are disabled. Docker administrators retain root-equivalent access to containers.
A vLLM API key does not protect every route, so a public/non-loopback deployment is
not offered by this backend; use a separately secured, site-approved gateway design.
See [vLLM security](https://docs.vllm.ai/en/latest/usage/security/) and its
[credential environment variable](https://docs.vllm.ai/en/latest/configuration/env_vars/).

After readiness, configure the endpoint/model/revision in a private
[provider](ai-providers.md), run `ai providers check`, and use it for metadata,
questions or diagnosis. A node-local model and an external API share that interface.

## Evidence, limits and rollback

The registry stores immutable model inspection, full plan snapshots and service
observations. Node-local plan/state/identity/log files provide restart reconciliation.
Back up the registry and retain your exported plans/configurations and node caches;
SQLite backup does not copy those external files. To recover a lost plan file,
inspect its registry source record using the plan's stored source version (the
`service_heads.plan` value); it contains canonical plan JSON. Do not start a
replacement while the old service is uncertain.

Stop the owned service and confirm `STOPPED` before reverting code or repurposing
resources. Preserve caches/logs unless you explicitly choose cleanup later. Disabling
AI/providers does not stop an already-running deployment; stop it explicitly.
Neither a mock probe nor these lifecycle simulations establish real GPU memory use,
network throughput, site authorization or runtime compatibility.

## Opt-in real local or SSH acceptance

Use a new reviewed plan for a disposable acceptance service and a private provider
configured with that plan's exact model revision and controller loopback endpoint.
The following runner applies the plan, waits for real readiness, probes inference
through the selected provider, then stops only its owned service and checks cleanup.
It preserves model caches/logs. Use `transport: local` or `ssh` in the plan to choose
the topology; both are separate from offline mocks.

```bash
pixi run uv run --frozen python tests/accept_llm_live.py \
  --registry /local/catalog --plan acceptance-plan.json --authorize EXACT_PLAN_HASH \
  --config /local/providers.json --provider private --timeout 300 \
  --output live-deployment-acceptance.json
```

This performs the authorized model download/service launch and needs real hardware,
credentials and an approved allocation. It is **NOT RUN** in the current validation.
A failed/unknown cleanup is a failed acceptance; investigate before reusing resources.
Quantized repositories additionally require `quantization` in the deployment config
to match the inspected `quantization_config.quant_method` exactly. Null/omitted means
an unquantized repository. Quantization is never selected automatically.
