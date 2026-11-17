# Web deployment

See [deployment instructions](../../docs/usage/deployment.md).

`manage.sh` builds and operates one Linux Compose installation. Set an absolute
`GENESIS_RUNTIME` outside Git first. The same image serves the GUI, worker and CLI.
No credentials, example records or model weights are embedded. Local model serving
is optional; configure `model-runtime.example.json` only when using the controller.
