#!/usr/bin/env bash
# One scoped Compose project; no host-wide pruning or automatic data removal.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
: "${GENESIS_RUNTIME:?Choose an absolute private runtime directory}"
[[ "$GENESIS_RUNTIME" = /* && "$GENESIS_RUNTIME" != / && "$GENESIS_RUNTIME" != "$HOME" ]] || { echo 'Choose a dedicated absolute runtime directory' >&2; exit 2; }
export GENESIS_UID="$(id -u)" GENESIS_GID="$(id -g)"
compose=(docker compose -f "$here/compose.yaml")
action=${1:-status}
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  build) "${compose[@]}" build app ;;
  init) umask 077; mkdir -p "$GENESIS_RUNTIME"; "${compose[@]}" run --rm cli workspace --root "$GENESIS_RUNTIME" init "$@" ;;
  start) "${compose[@]}" up -d app worker ;;
  stop) "${compose[@]}" stop app worker ;;
  status) "${compose[@]}" ps ;;
  doctor) "${compose[@]}" run --rm cli workspace --root "$GENESIS_RUNTIME" doctor ;;
  cli) "${compose[@]}" run --rm cli "$@" ;;
  *) echo 'Usage: manage.sh build|init|start|stop|status|doctor|cli' >&2; exit 2 ;;
esac
