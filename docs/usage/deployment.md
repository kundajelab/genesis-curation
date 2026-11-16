# Deployment

Use a private runtime directory for data, provider configuration and scratch files.
The application and worker share a SQLite database on a local filesystem. Do not put
SQLite on an SMB share. Large model files may use separate model storage.

`deploy/web/Dockerfile` builds the frontend with Node 24 and installs the locked Python
web environment. `compose.yaml` runs the app, worker and reverse proxy. The app binds to
loopback port 18765. `http-test.yaml` enables explicitly requested HTTP testing; keep
credentials and traffic exposure in mind when choosing the deployment network.

Initialize an account with `genesis web init --root /path/to/state`. Supply the password
interactively or through `--password-env`; do not commit it. Configure providers using
the example JSON and secret files outside Git. Start `genesis web serve` and
`genesis web worker` against the same root and provider file.

The host model controller in `deploy/web/model-controller.py` manages one active local
model and drains in-flight inference before switching. Models are pinned by revision
and checksum. The UI reports missing configuration or failed probes rather than treating
them as available. Search uses the optional SearXNG service and records engine failures.

This release starts with fresh state (catalog schema 2). Earlier catalog schemas are rejected. Archive old
runtime directories privately before initializing a new one; old jobs and review
history are not imported into the new application. Preserve provider secrets, model
weights and account credentials separately when replacing a deployment.

Back up both application state and immutable catalog data while workers are stopped.
Monitor disk space: campaigns pause when scratch space is insufficient. Source retrieval,
LLM inference and publication access can fail independently; inspect the activity log.

## Shared public evidence and disk headroom

The optional `deploy/web/shared-storage.yaml` overlay mounts `WEB_SHARED_ROOT` at
`/shared` and its `source-snapshots` directory at `/data/source-snapshots`. Create these
directories on a mounted shared filesystem and place a `.genesis-storage` marker in
its root before starting the services. Keep SQLite, credentials, private backups and
reference imports on local protected storage. Only use this overlay for public evidence.
When moving an existing snapshot directory, stop retrieval, copy and verify every file,
start with the overlay, then remove only verified unused local duplicates.

The app checks both filesystems. A missing marker or a share on the same device as local
state blocks background work instead of silently writing into the root disk. Below
6 GiB free the UI warns; below 2 GiB it latches a pause. After restoring storage, resume
explicitly from Review. Keep at least 8 GiB free before larger demonstrations.

The worker trims only replaceable, hash-named search and URL caches every ten minutes:
128 MiB per cache and a seven-day age limit. Immutable source snapshots and review
history are never evicted. Deployment containers use rotated logs (10 MiB, three files).
Run the host model controller under a process supervisor; it needs access to Docker,
but the web application does not. Preserve one tested rollback image and a consistent
private database backup before replacing app or worker containers.

## Demo acceptance

Run `scripts/check.sh` and the Node 24 frontend build. The suite includes a 100,000-row
synthetic selection fixture, recent activity pagination, archive/restore, storage guards
and a fragmented streaming transport fixture. These checks establish software behavior,
not biological accuracy.

With an already installed Playwright runtime, run `tests/browser_demo.cjs` using Node
and `NODE_PATH` as needed. Supply `GENESIS_BROWSER_URL`, `GENESIS_BROWSER_USER` and
`GENESIS_BROWSER_PASSWORD` through the environment. The test reads the demonstration
studies, opens evidence and checks response times without approving scientific claims.
Use `GENESIS_BROWSER_WRITE=1` only on an isolated copy to test saving and restoring a
review draft. Record live model probes, bounded discovery/curation results and a sustained
browser session separately; offline tests cannot establish those outcomes.

## Claude API on one server

An Anthropic Console API key can use the existing `anthropic-messages` provider;
no local model or Claude Code installation is needed. Keep the provider JSON and key
in a private runtime directory outside every checkout. Use directory mode `0700`
and key mode `0600`, owned by the application user. The key file contains just the
key on one line. Create the key scoped to the intended Anthropic workspace.
Reference its absolute path with `credential.file`; never put the
key in JSON, command-line arguments, the browser, a commit or a deployment image.
For Compose, use the private `/config` mount and its container path.

Copy the Claude entry from `deploy/web/providers.example.json`, select an available
Claude API model, configure its context/input/output limits, and set `mode` to `live`.
Use that private provider file for both `web serve` and `web worker`. Public metadata
is the default permitted data class. Authentication is sent only to Anthropic's HTTPS
API; model requests are external even though the credential is stored on one server.
A Claude Code subscription/OAuth login token is not an API key for this backend.
Do not copy this runtime or its credential to another host when cloning the repository.
Validate the configuration before running a live provider check. An empty key fails
closed; model availability and billing require the account's API access.
