#!/usr/bin/env bash
# Native development with owned child processes and centralized writable artifacts.
set -euo pipefail
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
: "${GENESIS_RUNTIME:?Initialize a runtime and set GENESIS_RUNTIME first}"
export TMPDIR="$GENESIS_RUNTIME/tmp" UV_CACHE_DIR="$GENESIS_RUNTIME/cache/uv"
export npm_config_cache="$GENESIS_RUNTIME/cache/npm"
mkdir -p "$TMPDIR" "$UV_CACHE_DIR" "$npm_config_cache"
cd "$repo"
[[ "$(node --version)" == v24.* ]] || { echo 'Node 24 is required; alternatively use the Docker deployment.' >&2; exit 2; }
uv sync --frozen --extra web
(cd frontend && npm ci && npm run build)
uv run --frozen genesis web worker --root "$GENESIS_RUNTIME/state" --providers "$GENESIS_RUNTIME/config/providers.json" &
worker_pid=$!
server_pid=''
cleanup() { kill "$worker_pid" ${server_pid:+"$server_pid"} 2>/dev/null || true; wait || true; }
trap cleanup EXIT
trap 'exit 130' INT TERM
uv run --frozen genesis web serve --root "$GENESIS_RUNTIME/state" --providers "$GENESIS_RUNTIME/config/providers.json" --origin "http://127.0.0.1:${GENESIS_PORT:-18765}" --port "${GENESIS_PORT:-18765}" --dev &
server_pid=$!
wait -n "$worker_pid" "$server_pid"
