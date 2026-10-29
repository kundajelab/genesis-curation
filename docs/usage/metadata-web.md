# Human-reviewed metadata curation

The GUI opens with **Discover**, followed by **Curation** and **Review**. Search
plant datasets, inspect modality tables, and resolve the exceptions that need a
reviewer. Background jobs retrieve metadata and prepare evidence-backed proposals.
No sequencing files are downloaded and no scientific pipeline is launched.

## Discovery and the reference workbook

Discover combines versioned assay/source recipes, native archive APIs, literature,
known plant repository searches, and an LLM query-expansion step. Actual requests,
results, errors and source snapshots are retained. A search hit is a lead, not a
verified experiment. Source availability and bounded pagination limit coverage.

Curation uses indexed, paginated modality tables. Filters and column selection work
without loading the entire workbook into the browser. **Curate selected** and
**Curate all** enqueue work immediately; Discover shows progress and pause/resume.
Campaigns batch records within studies using the provider's token budgets. Worker
interruptions preserve checkpoints and require explicit resume. Low disk space
pauses work. Unsupported or missing evidence remains visible for review.

To import an existing reference XLSX without modifying its source:

```bash
uv run python scripts/import_reference_workspace.py \
  --root /absolute/workspace/web-data --workbook /absolute/workspace/reference.xlsx
```

All eight known reference tabs are retained, including the headerless TSS and
MPRA lists and repeated DAP column headers. Original tab names remain in provenance;
navigation uses assay names. The immutable snapshot, row coordinates and original
cells establish lineage. Imported labels are external claims, not outputs of this
pipeline or independently validated truth. Accession-seeded replay records real
lookups and searches; it does not claim to reconstruct the original curator's work.

**History** shows original values, proposed values, cited evidence, source events,
model provenance and recorded review changes. **Export all + history** produces a
ZIP containing XLSX and JSON provenance. **Export clean metadata** restricts the
table to supported or reviewed records; unsupported claims are not promoted by
importing them. Metadata support is distinct from biological accuracy or assay QC.

For optional web search, run `deploy/web/search.yaml` alongside the app with a
private `$WEB_ROOT/search/settings.yml` based on the example (replace its secret).
Only loopback port 18888 is exposed. The worker requests Google and Brave through
SearXNG and caches responses for 24 hours. Google may reject server requests;
actual engine results and errors are recorded. Native archive searches do not
depend on SearXNG.

For goal-driven discovery, use [Find data](agentic-mining.md). It searches public
archives and sends uncertain inclusion decisions into the same reviewer to-do list.

## Reviewer workflow

1. **Add studies.** Enter a GEO/ENA/SRA/BioProject accession or upload CSV, TSV or
   JSON (at most 1,000 records and 2 MiB per upload; split larger tables). A `study_accession` column groups uploaded records into studies. Keep the
   stable record identifier and import name when retrying an import.
2. **Let preparation finish.** Public accession intake retrieves individual sample
   metadata and, for GEO, up to five linked PubMed articles and available PMC text.
   A local provider is selected by default when configured. Use **AI connection**
   to explicitly select a permitted hosted provider or source-only preparation.
3. **Complete the to-do list.** Each decision shows affected samples, a suggested
   resolution when available, and exact supporting passages. Accept, correct inline,
   keep unknown, defer, or ask the model to investigate the current field. Corrections
   require a supporting passage for every affected sample. Additional evidence can
   be attached in the same panel. Deferred items remain unfinished.
4. **Return whenever needed.** Notes, correction drafts, evidence drafts, selected
   study and task are saved on the server per reviewer. Navigation is disabled while
   a draft is saving. Interrupted preparation can be resumed; completed records and
   retrieved sources are reused. Failures remain visible and require retry or explicit
   acknowledgement; they never count as a scientific success.
5. **Sign off and export.** Check the prepared sample summary and sign off once.
   This accepts routine supported suggestions and the explicit decisions together.
   Unknown/rejected values remain excluded. A changed source or new preparation
   invalidates the current sign-off, while historical signed releases remain immutable.

The **Record history** screen retains the earlier field-review interface and legacy
exports. Those exports are distinct from a signed study release.

## Evidence and model boundaries

The worker uses deterministic extraction, sample-first evidence selection, controlled
vocabulary normalization, strict citation checks, and bounded targeted follow-up.
Prior values come from the version-checked server record, and ontology IDs are
assigned only by exact registered label/alias matching; the model supplies neither.
Supported suggestions with model-reported confidence below 0.8 also require an
explicit decision. This is a conservative review trigger, not calibrated accuracy.
An unresolved field gets at most two investigation passes for the same source versions;
new evidence is needed after that. Raw sample conflicts stay visible. General paper
context cannot become a sample fact without explicit sample attribution or a human
correction. Evidence omitted to fit the model context is recorded in provenance. A uniquely
matching citation span may restore whitespace that the model transcribed differently
(for example a non-breaking space); original model text and the restoration are
audited, and the restored quote must pass the exact-source validator. Words,
punctuation, source identifiers and locators are never repaired this way.

Built-in profiles cover general plant metadata, TF binding/DAP controls, and plant
cell-type metadata. The latter adapts principles from Alex Borowsky's
[annotation skill](https://github.com/alexborowsky/plant-celltype-annotation), pinned
in the skill metadata. It distinguishes reported and inferred cell types and does
not implement the upstream full expression-analysis workflow. Skill/profile hashes,
provider settings, source hashes and available model revisions accompany proposals.
User corrections can optionally become scoped examples after study sign-off; this
is prompt context, not model fine-tuning. Hosted providers never replace a local
provider silently, and local-only evidence cannot be sent to external APIs.

Study approval establishes a reviewed metadata release. Scientific QC and independently
measured biological accuracy remain separate and are explicitly marked unevaluated.
The study list and task API support pagination. Detailed study responses currently
load all member evidence; very large studies may need splitting for responsive review.
Public retrieval is bounded to 2 MiB per response, and unavailable text is recorded.
The metadata pilot limits sample count; ordinary GUI imports do not silently sample.

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

### Legacy record tools and custom skills

The secondary **Record history** screen supports detailed source inspection,
field-level review, contextual questions and legacy approved-field exports. Use
**Study decisions** for the default prepare → resolve exceptions → sign-off flow.
Conversation alone never approves metadata. New source evidence invalidates affected
reviews; new study preparation preserves human decisions and requires fresh sign-off.

Skills are JSON files under `$WEB_ROOT/data/skills` containing exactly `id`,
`version`, `title`, `instructions`, and `references`. They are instructions and
references, never executable code. Each proposal records their content hash and
selected examples. The built-in plant metadata skill addresses missing evidence,
TF source versus assayed organism, and distinctions among biological samples and
technical lanes. The plant cell-type profile uses Alex's guidance as instructions, never as evidence
about a sample. It is separate from execution of the full annotation workflow.

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
Do not run old code against a migrated schema. Registry schema 6 preserves existing
pipeline registry tables and creates pre-migration backups through the normal store.

## Study workflow evaluation

Run the isolated synthetic acceptance fixture with a configured local provider:

```bash
uv run --frozen --extra web python tests/evaluate_study_live.py \
  --root /absolute/workspace/NEW_STUDY_EVALUATION \
  --providers /absolute/workspace/providers.json --provider MODEL_ID
```

For public metadata only, add
`--public-manifest tests/fixtures/study/public-pilot.json`. This retrieves at most
five sample metadata records per study and available linked paper text; it does
not download sequencing files or sign off public studies. The result is stored in
`evaluation.json` under that isolated root. After a completed run, use the same
root with `--retry-failures` to retry failed studies and retain previous results.
Do not run the retry concurrently with the initial evaluation.

See [study workspace validation](study-workspace-validation.md) for the delivered
checks, pilot results, migration evidence and remaining limitations.

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

## Token-budgeted table preparation

Set `context_window_tokens` on a provider to enable table preparation. Configure
`max_input_tokens`, `max_tokens` (the output ceiling), and `token_counter` alongside
it. Supported counters are `llama`, `openai-responses`, `anthropic-messages`, and
`conservative`. The local llama counter checks the running server's context window;
a larger configured number cannot override a smaller server window. Set
`LLAMA_CONTEXT` when starting that model service, after measuring GPU headroom.

The scheduler first attempts the entire table. It reserves output capacity and a
10% context margin (at least 512 tokens), and splits automatically when either
input or output limits require it. Exact repeated evidence is sent once, with
sample-specific citation bindings retained in an immutable batch manifest. No
source passages are silently dropped. A single oversized sample becomes a visible
failure requiring a focused excerpt. If a native token counter fails, the budget
uses an explicitly labelled conservative UTF-8 byte estimate.

The model returns only changed fields, with explicit row IDs and evidence IDs.
Every row must be acknowledged. Unknown, missing, duplicate or wrong-sample
citations are rejected. Invalid rows receive at most two smaller recovery rounds;
successful rows are retained. Unresolved evidence receives one combined automatic
follow-up round. Refusals do not retry; access/service failures stop preparation.
Human decisions remain authoritative and final sign-off remains manual.

A durable checkpoint is saved after each batch. Interactive reviewer requests have
priority between batches. Cancellation preserves completed rows; a worker restart
requires explicit resume of an interrupted preparation. Reuse requires matching
record, provider, skill, vocabulary and workflow revisions. The GUI polls lightweight
progress, displays preparation/follow-up phase and elapsed time, and refreshes full
evidence after completion without overwriting an open unsaved review.

Providers without `context_window_tokens` retain the existing per-sample workflow.
Hosted provider token counting has offline contract coverage; live hosted API
validation requires separately provisioned credentials.

For a frozen metadata benchmark, use `tests/evaluate_table_live.py --help`. It
requires a new isolated root, a source snapshot, provider configuration and skills.
It never writes the source registry, downloads sequencing files or signs off rows.
Its report includes all invocation attempts, failures, token usage, value differences
and elapsed time. Value differences against an earlier model run are not an
independent biological accuracy score.

See [table preparation validation](table-preparation-validation.md) for the frozen
57-row replay, exact checks, rejected compression experiments and deployment results.

## Guided review and reference audit

The default view now offers **Start review / Continue review** across studies.
The **Plant reference audit** provides a separate, resumable evidence-first quality
check. See [guided review and audit](trustworthy-review.md) for the workflow,
assessment limits, APIs, validation commands and schema-7 recovery procedure.
