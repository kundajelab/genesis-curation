# Human-reviewed metadata curation

The optional web application imports metadata independently of sequencing files or
pipeline execution. It stores original snapshots, proposals, exact citations,
field-level decisions, conversations and promoted examples in a versioned registry.
Only explicitly approved fields appear as accepted metadata in exports. New evidence
or a new proposal invalidates the previous reviews. Unknown and conflicting fields
need a supported human correction before approval; rejected or information-requested
fields remain unresolved.

## Development

From a task worktree, with `TMPDIR` and `GENESIS_REPO` set to your Genesis workspace:

```bash
uv sync --frozen --extra web
cd frontend
npm ci
npm run build
cd ..
genesis web init --root /absolute/workspace/web-data
genesis web serve --root /absolute/workspace/web-data \
  --providers examples/curation/providers.mock.json \
  --origin http://127.0.0.1:18765 --dev
# Separate terminal, same root and provider configuration:
genesis web worker --root /absolute/workspace/web-data \
  --providers examples/curation/providers.mock.json
scripts/check.sh
```

Use Node 22.12+ (the Docker build pins Node 24) and Python 3.14. The `web` dependency
extra is optional for CLI-only users. `scripts/check.sh` includes it to type-check
and exercise the HTTP boundary. The mock provider is synthetic and does not measure
model accuracy. `tests/verify_web.py` uses the existing script-based test convention;
no additional test framework is required.

## Deploy on a Linux Docker host

[deploy/web](../../deploy/web/) contains the multi-stage application Dockerfile,
Compose services and an SSH-operated lifecycle script. The application and worker
run as your UID, with a read-only container filesystem, dropped capabilities, no
Docker socket and a loopback API. Nginx serves the GUI. Keep the SQLite database on
local storage, not a network filesystem. Place all state under a private directory
in your Genesis workspace. Image/build caches are managed by Docker.

Set these variables in your shell; do not commit machine paths or credentials:

```bash
export WEB_ROOT=/absolute/workspace/deploy/metadata-web
export PUBLIC_IP=YOUR_PUBLIC_IPV4
export ACME_EMAIL=YOUR_CERTIFICATE_CONTACT  # optional; omitted means no email registration
deploy/web/manage.sh prepare
deploy/web/manage.sh init                 # password prompt; at least 14 characters
deploy/web/manage.sh certificate
deploy/web/manage.sh start
deploy/web/manage.sh status
```

The GUI is `https://PUBLIC_IP:8443`. Port 80 serves only ACME challenges. The host's
network/security group must permit incoming TCP 80 and 8443. Certbot 5.4 supports IP
certificates through its webroot plugin; the short-lived profile requires frequent
renewal. The renewal container checks every six hours; nginx detects changed
certificates every minute and reloads them. Check certificate expiry and renewal
logs operationally. The containers do not change host firewall rules or Docker.
See [Let's Encrypt's IP certificate instructions](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/).

For an explicitly authorized public HTTP **test** using public/synthetic metadata:

```bash
deploy/web/manage.sh http-start
```

This serves `http://PUBLIC_IP` on port 80 and passes `--allow-insecure-http` to the
application. Login and metadata traffic are unencrypted. Use a separate account and
state directory. HTTPS is required by default; `--dev` remains loopback-only. This
HTTP configuration does not require a certificate or contact email.

### Models and provider APIs

The default configuration starts with providers **disabled**. Provision keys over
SSH as owner-only (`0600`) files in `$WEB_ROOT/config`, and edit
`$WEB_ROOT/config/providers.json`. Containers see them under `/config`.

- `openai-chat`: local vLLM or llama.cpp; no external fallback. Use an exact model
  identity, immutable model revision and serving image digest.
- `openai-responses`: native OpenAI Responses, `store: false`, structured outputs.
- `anthropic-messages`: native Claude Messages and structured outputs.

Choose actual approved hosted model IDs; the examples intentionally have placeholders.
Hosted providers allow public metadata only by default. Local-only evidence is never
sent to an external provider, even if its configuration lists that classification.
Keys and endpoints are not returned by the provider-list API. Requests have context,
output, retry, timeout and concurrency budgets; malformed output, refusal, truncation
and unsupported citations fail the job. Configuration and skill hashes are pinned at
queue submission, and changes require resubmission. Use **Models & skills → Check
connection** after enabling a provider. A successful probe establishes service
connectivity, not curation accuracy.

For a safetensors/vLLM deployment, use the existing
[model inspection and exact-plan lifecycle](llm-deployment.md). A model must fit the
actual available GPU resources; never silently change its precision or context.
The optional `llama-test.yaml` adds a separately approved Qwen3.8-27B Q8_0 GGUF
service for a 40 GiB A100. It publishes only loopback port 19091. Set `LLAMA_IMAGE`
to an immutable official CUDA-12 llama.cpp image digest, `MODEL_DIR` to the directory
containing the verified `Qwen3.8-27B-Q8_0.gguf`, and the ordinary Compose variables.
Download a pinned Hugging Face revision and verify its LFS SHA256 before startup.
The GGUF service is operated through Compose, not the safetensors deployment CLI.

```bash
docker compose -f deploy/web/compose.yaml -f deploy/web/llama-test.yaml up -d model
```

The test configuration uses 8192 context, one concurrent request, eight CPUs,
8-bit weights, and non-thinking output. Vision, speculative decoding and tools are
not enabled. It is an evaluation configuration, not a biological validation claim.

### Curator workflow

1. Upload CSV, TSV or JSON, or fetch a GEO/ENA/SRA accession. Inspect the preview,
   namespace, identifier column and classification before importing. No sequencing
   files are downloaded. Original fields remain available in source snapshots.
2. Review extracted fields or request a model proposal with a selected skill. Each
   record shows current approved values, proposed values, uncertainty and citations.
3. Select a field, inspect its exact source quote and locator, and approve, reject or
   request information with a reason. Corrected values must cite existing evidence.
   Attach supporting text when evidence is missing; set its classification explicitly.
4. Ask contextual questions or add re-analysis guidance. Conversation never accepts
   changes. Jobs survive browser closure; refresh the record when a job completes.
5. Explicitly promote an approved decision as an example after organism and tissue
   are approved. It can guide later records in the same context and compatible
   classification. Invalidated reviews stop contributing examples.
6. Export JSON with provenance, or CSV/TSV containing approved values and an explicit
   unresolved-field list. Spreadsheet formula prefixes are escaped in tabular exports.

Skills are JSON files under `$WEB_ROOT/data/skills` containing exactly `id`,
`version`, `title`, `instructions`, and `references`. They are instructions and
references, never executable code. Each proposal records their content hash and
selected examples. The built-in plant metadata skill addresses missing evidence,
TF source versus assayed organism, and distinctions among biological samples and
technical lanes. Alex's cell-type annotation integration remains separate from this
metadata release; it is not silently used as metadata evidence.

### Recovery, passwords and backups

Use `genesis web reset-password --root ... --username curator` over SSH. It revokes
existing sessions. There is no public signup or password-reset endpoint. Sessions
expire after 12 hours; login attempts are rate limited. POST requests require both
an exact Origin and session-bound CSRF token.

There is one worker per data root. Queued jobs persist; jobs interrupted by a worker
restart are marked `INTERRUPTED`, never silently retried. Inspect immutable history
before manually submitting another request. Cancellation is cooperative and is
checked before publication; a completed publication remains reported as successful
rather than falsely reported as cancelled.

```bash
export BACKUP_DIR=/absolute/workspace/backups/NEW_BACKUP_DIRECTORY
deploy/web/manage.sh backup
deploy/web/manage.sh stop
```

Backup briefly stops the application and worker, copies the complete database/data
and private provider configuration, then restarts them. Backups contain private data
and keys; protect them. For recovery, stop the services, restore the data and config
into a fresh private root owned by the curator UID, and restart the matching image.
Certificates can be reissued separately. Model weights/caches are not included.
Do not run old code against a migrated schema. Registry schema 5 preserves existing
pipeline registry tables and creates pre-migration backups through the normal store.

## Opt-in live synthetic comparison

After enabling a real provider, run:

```bash
uv run --frozen --extra web python tests/evaluate_web_live.py \
  --root /absolute/workspace/NEW_EVALUATION_REGISTRY \
  --providers /absolute/workspace/providers.json --provider MODEL_ID \
  --output /absolute/workspace/evaluation.json
```

This compares four development fixtures (explicit, prose-only, missing-evidence and
conflicting evidence) and two held-out fixtures (rice prose and unspecified ATAC
preparation), with and without the skill. It records per-case latency, validated fields,
gold matches, failure reasons and invocation provenance. It does not measure human
correction rates, sequencing quality, or biological annotation accuracy. Live hosted
API acceptance requires separately provisioned credentials.

If the host NVIDIA runtime fails while the curator's normal GPU group can access the
device, `llama-device-test.yaml` offers a separately selected container override with
explicit device nodes, that existing group (`GPU_GROUP_ID`), and read-only host
CUDA driver libraries. It requires Compose 2.24+ for `!reset`. It does not change
Docker, drivers, group membership or device modes. Check `--list-devices` before
loading the model, and keep the host/library paths appropriate to the tested node.

For an older data-center driver, an image that already bundles NVIDIA's CUDA
compatibility package can select it using
`CUDA_COMPAT_LIBRARY_PATH=/usr/local/cuda/compat:/usr/local/cuda/lib64:/app` with
the device override. This is a per-container library choice. Validate an actual
inference, not only device enumeration: CUDA PTX compilation can fail after a GPU
is successfully detected. See [NVIDIA's compatibility documentation](https://docs.nvidia.com/deploy/cuda-compatibility/forward-compatibility.html).

The optional model is a separate service: `manage.sh stop` stops the web stack.
Stop the model with the same Compose file stack used to start it, ending in
`stop model`. Do not use `--remove-orphans` while managing just the web stack; the
separately selected model may be reported as an orphan by that narrower command.
