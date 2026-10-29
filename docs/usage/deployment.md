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

This release starts with fresh state. Prototype databases are rejected. Archive old
runtime directories privately before initializing a new one; old jobs and review
history are not imported into the new application. Preserve provider secrets, model
weights and account credentials separately when replacing a deployment.

Back up both application state and immutable catalog data while workers are stopped.
Monitor disk space: campaigns pause when scratch space is insufficient. Source retrieval,
LLM inference and publication access can fail independently; inspect the activity log.
